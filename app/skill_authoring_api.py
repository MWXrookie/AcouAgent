"""Bounded administrator authoring API. Clients cannot assign scientific authority."""
import asyncio
import base64
import io
import json
from pathlib import Path
import sqlite3
import uuid

from starlette.concurrency import run_in_threadpool
from starlette.responses import HTMLResponse, Response

from app import config, skill_library_service as service
from app.skill_api import JSONResponse
from app.skill_drafts import DraftError, DraftStore, _revision
from app.skill_spec import SkillSpecError, _decode, prepare_skill_spec
from app.skill_validation_jobs import ValidationJobs, canonical
from app.ui_chrome import render_page

ROOT = Path(__file__).resolve().parents[1] / "docs" / "skills-next"
ACTOR = "portal-admin"
PREFIX = "/skills/api/v1"


class RequestError(ValueError):
    def __init__(self, code, status=400, field=None):
        self.code, self.status, self.field = code, status, field


def _fields(body, required, optional=()):
    if type(body) is not dict or not set(required) <= body.keys() or body.keys() - set(required) - set(optional):
        raise RequestError("invalid_fields")


async def _body(request):
    data = bytearray()
    async for chunk in request.stream():
        if len(data) + len(chunk) > 64000:
            raise RequestError("request_too_large", 413)
        data.extend(chunk)
    try:
        return _decode(bytes(data))
    except SkillSpecError as error:
        raise RequestError(error.code) from None


def _spec(value):
    if type(value) is not dict:
        raise RequestError("invalid_spec", 422, "/spec")
    # Locate common top-level field errors before the authoritative semantic check.
    for field, maximum in (("name", 120), ("description", 2048), ("code_template", 20000)):
        item = value.get(field)
        if type(item) is not str or not item.strip() or len(item) > maximum:
            raise RequestError("invalid_" + field, 422, "/spec/" + field)
    try:
        return prepare_skill_spec(canonical(value))["spec"]
    except SkillSpecError as error:
        pointers = {"invalid_pml": "/spec/parameters/pml_size", "zero_source": "/spec/parameters/helmholtz_source_coefficient",
                    "index_in_pml": "/spec/parameters/source_index", "invalid_regions": "/spec/parameters/material_regions",
                    "invalid_region_bounds": "/spec/parameters/material_regions", "overlapping_regions": "/spec/parameters/material_regions",
                    "insufficient_spatial_resolution": "/spec/parameters/dx_m", "result_shape_mismatch": "/spec/result_spec/grid_shape",
                    "invalid_result_contract": "/spec/result_spec", "invalid_result_shape": "/spec/result_spec/grid_shape",
                    "invalid_validation_policy": "/spec/validation_request/policy", "code_too_large": "/spec/code_template"}
        raise RequestError(error.code, 422, pointers.get(error.code, "/spec")) from None


def _jobs(library, draft=None, job=None, trial=False):
    if trial or (job is not None and job["intent"] == "trial"):
        return ValidationJobs(library.store, library.runtime_sha256)
    from app.skill_general_profiles import match_profile
    from app.skill_general_validation import GeneralValidationJobs
    case = match_profile((job or draft)["spec"])
    if case is None:
        raise RequestError("unsupported_validation_case", 422, "/spec/parameters")
    return GeneralValidationJobs(library.store, library.runtime_sha256, case, library)


def _job_view(job):
    # Deliberate allow-list: never return internal nonce, receipts, dispatch or filesystem paths.
    result = {key: job[key] for key in ("job_id", "draft_id", "draft_version", "intent", "state", "revision",
              "reason_code", "created_ms", "updated_ms", "attempt_no")}
    result["run_id"] = job["attempt"]["run_id"] if job.get("attempt") else None
    result["status_url"] = PREFIX + "/jobs/" + job["job_id"]
    result["result_url"] = result["status_url"] + "/result"
    result["field_url"] = result["status_url"] + "/field"
    return result


def _result(library, job):
    if job["state"] not in ("rejected", "failed", "cancelled", "validated", "admitted"):
        raise RequestError("result_not_ready", 409)
    attempt = job.get("attempt")
    result = dict(job=_job_view(job), has_result=False, quality_level=None, phase_certified=False)
    if not attempt or not attempt["output_manifest_sha256"]:
        return result
    manifest = library.assets.read_manifest(attempt["output_manifest_sha256"])
    # Recheck original receipt before presenting a stored array as this job's output.
    from app.skill_validation_worker import read_unique_history, verify_recorded_attempt
    receipt = verify_recorded_attempt(job, read_unique_history(attempt["run_id"], attempt["request_nonce"]))
    expected = {key: attempt[key] for key in ("run_id", "request_nonce", "code_sha256", "parameters_sha256",
               "runtime_sha256", "request_sha256", "attempt_no")}
    expected.update(job_id=job["job_id"], content_sha256=job["content_sha256"], case_fingerprint=job["case_fingerprint"],
                    result_spec=job["spec"]["result_spec"], receipt_sha256=receipt)
    if any(manifest.get(key) != value for key, value in expected.items()):
        raise RequestError("result_binding_failed", 409)
    from app.skill_result_assets import decode_executor_field
    import numpy as np
    original = decode_executor_field(read_unique_history(attempt["run_id"], attempt["request_nonce"]), manifest["result_spec"])
    field = library.assets.read_field(manifest["field_asset_sha256"], manifest["result_spec"])
    if original.dtype != field.dtype or not np.array_equal(original, field):
        raise RequestError("result_binding_failed", 409)
    view = library.assets.present(manifest["field_asset_sha256"], manifest["result_spec"])
    result.update(has_result=True, shape=view["shape"], units=view["units"], max_pressure=view["max_pressure"],
                  rms_pressure=view["rms_pressure"], axis_order=view["axis_order"], quality_level="Q2")
    if job["intent"] == "validate":
        proof = library._comparison(job, attempt["output_manifest_sha256"])
        result["comparison"] = proof["comparison"]
        if job["state"] in ("validated", "admitted") and proof["comparison"]["numeric_pass"]:
            result["quality_level"] = "Q4" if job["state"] == "admitted" else None
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    figure = Figure(figsize=(6, 4.5)); canvas = FigureCanvasAgg(figure)
    axes = figure.add_subplot()
    image = axes.imshow(view["amplitude"], origin="lower")
    axes.set_xlabel("y grid index"); axes.set_ylabel("x grid index")
    figure.colorbar(image, ax=axes, label="Magnitude (numerical source normalization)")
    stream = io.BytesIO(); canvas.print_png(stream)
    result["heatmap_png_base64"] = base64.b64encode(stream.getvalue()).decode("ascii")
    return result


def _operation(request, body):
    from app.skill_general_library import get_library
    drafts = DraftStore(service.STORE)
    resource = request.path_params.get("resource")
    action = request.path_params.get("action")
    path = request.url.path
    if path == PREFIX + "/drafts":
        if request.method == "GET":
            if set(request.query_params) - {"after_id"}:
                raise RequestError("invalid_query")
            return drafts.list_drafts(actor_id=ACTOR, limit=20, after_id=request.query_params.get("after_id")), 200
        _fields(body, {"spec"}, {"origin"})
        if body.get("origin", "manual") != "manual":
            raise RequestError("unsupported_origin", 422, "/origin")
        result = drafts.create(canonical(_spec(body["spec"])), actor_id=ACTOR,
                               idempotency_key=request.headers.get("Idempotency-Key"))
        return result, 200 if result["duplicate"] else 201
    if "/drafts/" in path:
        if action == "jobs" and request.method == "GET":
            drafts.get(resource, actor_id=ACTOR)
            base = ValidationJobs(service.STORE, config.SKILL_GENERAL_RUNTIME_SHA256)
            with drafts._connection() as db:
                ids = db.execute("SELECT job_id FROM skill_validation_jobs WHERE draft_id=? ORDER BY created_ms DESC,job_id DESC LIMIT 20", (resource,)).fetchall()
            return {"items": [_job_view(base.get(row[0], actor_id=ACTOR)) for row in ids]}, 200
        if action is None:
            if request.method == "GET":
                if set(request.query_params) - {"version"}:
                    raise RequestError("invalid_query")
                version = request.query_params.get("version")
                return drafts.get(resource, actor_id=ACTOR, version=int(version) if version is not None else None), 200
            _fields(body, {"expected_revision", "spec"})
            return drafts.update(resource, canonical(_spec(body["spec"])), actor_id=ACTOR,
                                 expected_revision=body["expected_revision"]), 200
        if action == "archive":
            _fields(body, {"expected_revision"})
            return drafts.archive(resource, actor_id=ACTOR, expected_revision=body["expected_revision"]), 200
        if action == "controlled":
            _fields(body, {"expected_revision", "apply"})
            if type(body["apply"]) is not bool:
                raise RequestError("invalid_apply")
            return get_library().save_controlled_draft(resource, actor_id=ACTOR,
                        expected_revision=body["expected_revision"], apply=body["apply"]), 200
        if action in ("validations", "trials"):
            _fields(body, {"expected_revision", "draft_version"})
            draft = drafts.get(resource, actor_id=ACTOR)
            _revision(body["draft_version"])
            # Version is already bound by revision in enqueue; allow identical idempotency replay
            # after the submission increments the draft revision, but never a mismatched version.
            if draft["current_version"] != body["draft_version"]:
                raise DraftError("revision_conflict")
            library = get_library()
            jobs = _jobs(library, draft=draft, trial=action == "trials")
            job = jobs.enqueue(resource, actor_id=ACTOR, expected_revision=body["expected_revision"],
                               idempotency_key=request.headers.get("Idempotency-Key"),
                               intent="trial" if action == "trials" else "validate")
            return _job_view(job), 202
        raise RequestError("not_found", 404)
    base = ValidationJobs(service.STORE, config.SKILL_GENERAL_RUNTIME_SHA256)
    job = base.get(resource, actor_id=ACTOR)
    if action is None:
        return _job_view(job), 200
    library = get_library()
    if action == "result":
        return _result(library, job), 200
    if action == "field" and request.method == "GET":
        checked = _result(library, job)
        if not checked["has_result"]:
            raise RequestError("result_not_available", 409)
        manifest = library.assets.read_manifest(job["attempt"]["output_manifest_sha256"])
        field = library.assets.read_field(manifest["field_asset_sha256"], manifest["result_spec"])
        import numpy as np
        buffer = io.BytesIO(); np.save(buffer, field, allow_pickle=False)
        return Response(buffer.getvalue(), media_type="application/octet-stream", headers={
            "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
            "Content-Disposition": 'attachment; filename="complex-pressure-' + job["job_id"] + '.npy"'}), 200
    _fields(body, {"expected_revision"})
    if action == "cancel":
        job = base.cancel(resource, actor_id=ACTOR, expected_revision=body["expected_revision"])
        return _job_view(job), 200 if job["state"] == "cancelled" else 202
    _revision(body["expected_revision"])
    if body["expected_revision"] != job["revision"]:
        raise DraftError("revision_conflict")
    if action in ("reconcile", "resume"):
        if job["state"] != "needs_reconciliation":
            raise RequestError("reconciliation_not_required", 409)
        from app.skill_manual_worker import components
        _, worker = components(library, job)
        return _job_view(worker.reconcile(resource)), 202
    if action == "admit":
        if job["intent"] != "validate":
            raise RequestError("trial_cannot_be_admitted", 409)
        return library.admit(resource, actor_id=ACTOR, expected_revision=body["expected_revision"]), 200
    raise RequestError("not_found", 404)


def _error(error):
    if isinstance(error, RequestError):
        return error.code, error.status, error.field
    code = getattr(error, "code", "invalid_request")
    if code == "not_found" or code == "invalid_draft_id":
        return "not_found", 404, None
    if code == "queue_full":
        return code, 429, None
    if any(part in code for part in ("unavailable", "disabled", "not_ready", "profile_not_available")):
        return code, 503, None
    if any(part in code for part in ("conflict", "not_editable", "not_cancellable", "not_validated", "cancelled", "reconciliation")):
        return code, 409, None
    return code, 422, "/spec" if "spec" in code or "controlled" in code else None


def register(mcp):
    @mcp.custom_route("/skills/authoring", methods=["GET"])
    async def page(request):
        if config.SKILL_LIBRARY_ENABLED is not True:
            return JSONResponse({"error": "disabled"}, status_code=404)
        return HTMLResponse(render_page((Path(__file__).parent / "web" / "skill_authoring.html").read_text(encoding="utf-8")),
            headers={"Cache-Control": "no-store", "Content-Security-Policy": "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; img-src data:; base-uri 'none'; frame-ancestors 'none'; form-action 'none'"})

    @mcp.custom_route(PREFIX + "/spec-schema", methods=["GET"])
    async def schema(request):
        if config.SKILL_AUTHORING_ENABLED is not True:
            return JSONResponse({"error": "disabled"}, status_code=404)
        return JSONResponse(dict(spec_version="skill-spec.v1", schema=json.loads((ROOT / "skill-spec-v1.schema.json").read_text(encoding="utf-8")),
            templates={case: json.loads((ROOT / "examples" / filename).read_text(encoding="utf-8")) for case, filename in
                       (("B01", "manual-skill.json"), ("B02", "b02-manual-skill.json"), ("B03", "b03-manual-skill.json"))}))

    async def handle(request):
        request_id = uuid.uuid4().hex
        try:
            if config.SKILL_AUTHORING_ENABLED is not True or config.SKILL_LIBRARY_ENABLED is not True:
                raise RequestError("authoring_disabled", 404)
            try:
                service.authorize(request.headers.get("X-Admin-Token", ""))
            except PermissionError:
                raise RequestError("admin_required", 403) from None
            body = await asyncio.wait_for(_body(request), timeout=2) if request.method in ("POST", "PUT") else None
            result, status = await run_in_threadpool(_operation, request, body)
            if isinstance(result, Response):
                return result
            return JSONResponse(result, status_code=status)
        except asyncio.TimeoutError:
            code, status, field = "request_timeout", 408, None
        except (RequestError, DraftError, SkillSpecError) as error:
            code, status, field = _error(error)
        except (sqlite3.Error, OSError):
            code, status, field = "authoring_store_unavailable", 503, None
        except (ValueError, TypeError, KeyError, OverflowError, RecursionError, UnicodeError):
            code, status, field = "invalid_request", 400, None
        return JSONResponse({"error": {"code": code, "message": code, "retryable": status in (429, 503),
                             "fields": [{"pointer": field, "code": code}] if field else []}, "request_id": request_id}, status_code=status)

    # Registered before legacy /skills/api/{skill_id}; all sensitive reads use the same guard.
    for path, methods in (("/drafts", ["GET", "POST"]), ("/drafts/{resource}", ["GET", "PUT"]),
                          ("/drafts/{resource}/{action}", ["POST", "GET"]), ("/jobs/{resource}", ["GET"]),
                          ("/jobs/{resource}/{action}", ["GET", "POST"])):
        mcp.custom_route(PREFIX + path, methods=methods)(handle)
