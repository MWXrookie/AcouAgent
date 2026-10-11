"""Default-disabled skill APIs. No activation, code execution or learning."""
import json
import asyncio
import sqlite3

from pathlib import Path
from starlette.responses import HTMLResponse, JSONResponse as _JSONResponse
from app import config, dashboard, skill_library_service as service


class JSONResponse(_JSONResponse):
    """Skill responses must not persist code or audit contents in caches."""
    def __init__(self, content, status_code=200):
        super().__init__(content, status_code=status_code,
            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})


async def _small_json(request):
    data = bytearray()
    async for chunk in request.stream():
        if len(data) + len(chunk) > 4096:
            raise OverflowError("request_size_limit")
        data.extend(chunk)
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("duplicate_json_key")
            value[key] = item
        return value
    def nonfinite(value):
        raise ValueError("nonfinite_json")
    result = json.loads(data.decode("utf-8"), object_pairs_hook=unique, parse_constant=nonfinite)
    if not isinstance(result, dict):
        raise ValueError("object_required")
    return result


def register(mcp):
    @mcp.custom_route("/skills", methods=["GET"])
    async def skills_page(request):
        if config.SKILL_LIBRARY_ENABLED is not True:
            return JSONResponse({"error": "disabled"}, status_code=404)
        return HTMLResponse((Path(__file__).parent / "web" / "skills.html").read_text(encoding="utf-8"),
            headers={"Cache-Control": "no-store", "Content-Security-Policy":
                "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'"})

    @mcp.custom_route("/skills/search", methods=["POST"])
    async def search_skills(request):
        # Read-only metadata, no learning/action/admin side effect.
        if config.SKILL_LIBRARY_ENABLED is not True:
            return JSONResponse({"error":"disabled"},status_code=404)
        import asyncio
        from starlette.concurrency import run_in_threadpool
        from app import skill_retrieval
        try:
            body=await asyncio.wait_for(_small_json(request),timeout=2)
            if not {"scenario_type","parameters"}<=set(body) or not set(body)<={"scenario_type","parameters","limit"}:
                raise ValueError("search_fields")
            if type(body["parameters"]) is not dict:
                raise ValueError("parameters_object")
            result=await run_in_threadpool(skill_retrieval.search,body["scenario_type"],
                json.dumps(body["parameters"],sort_keys=True,separators=(",",":"),allow_nan=False),
                body.get("limit",3))
            return JSONResponse(result)
        except asyncio.TimeoutError:
            return JSONResponse({"error":"request_timeout"},status_code=408)
        except OverflowError:
            return JSONResponse({"error":"request_too_large"},status_code=413)
        except (ValueError,TypeError,KeyError,RecursionError,UnicodeError):
            return JSONResponse({"error":"invalid_request"},status_code=400)

    @mcp.custom_route("/skills/api", methods=["GET"])
    async def list_skills(request):
        if config.SKILL_LIBRARY_ENABLED is not True:
            return JSONResponse({"error": "disabled"}, status_code=404)
        try:
            limit = int(request.query_params.get("limit", "20"))
            archived = request.query_params.get("archived", "false")
            if archived not in {"true", "false"}:
                raise ValueError("invalid_filter")
            return JSONResponse(service.STORE.list_metadata(limit=limit, include_archived=archived == "true"))
        except ValueError:
            return JSONResponse({"error": "invalid_request"}, status_code=400)

    @mcp.custom_route("/skills/api/{skill_id}", methods=["GET"])
    async def skill_detail(request):
        if config.SKILL_LIBRARY_ENABLED is not True:
            return JSONResponse({"error": "disabled"}, status_code=404)
        try:
            if set(request.query_params) - {"version"}:
                raise ValueError("unexpected_query")
            raw = request.query_params.get("version")
            result = service.STORE.read_detail(request.path_params["skill_id"],
                                               version=int(raw) if raw is not None else None)
            return JSONResponse(result)
        except ValueError:
            return JSONResponse({"error": "invalid_request"}, status_code=400)

    @mcp.custom_route("/skills/api/{skill_id}/events", methods=["GET"])
    async def skill_events(request):
        if config.SKILL_LIBRARY_ENABLED is not True:
            return JSONResponse({"error": "disabled"}, status_code=404)
        try:
            if set(request.query_params) - {"after", "limit", "until"}:
                raise ValueError("unexpected_query")
            return JSONResponse(service.STORE.read_events(request.path_params["skill_id"],
                after=int(request.query_params.get("after", "0")),
                limit=int(request.query_params.get("limit", "20")),
                until=int(request.query_params["until"]) if "until" in request.query_params else None))
        except ValueError:
            return JSONResponse({"error": "invalid_request"}, status_code=400)

    @mcp.custom_route("/skills/api/{skill_id}/feedback", methods=["GET"])
    async def feedback_state(request):
        if config.SKILL_LIBRARY_ENABLED is not True:
            return JSONResponse({"error": "disabled"}, status_code=404)
        try:
            if set(request.query_params) != {"run_id"} or len(request.query_params.getlist("run_id")) != 1:
                raise ValueError("unexpected_query")
            return JSONResponse(service.STORE.read_feedback_state(
                request.path_params["skill_id"], request.query_params["run_id"]))
        except ValueError:
            return JSONResponse({"error": "invalid_request"}, status_code=400)

    @mcp.custom_route("/skills/api/{skill_id}", methods=["POST"])
    async def manage_skill(request):
        if config.SKILL_LIBRARY_ENABLED is not True:
            return JSONResponse({"error": "disabled"}, status_code=404)
        skill_id = request.path_params["skill_id"]
        supplied = request.headers.get("X-Admin-Token", "")
        try:
            service.authorize(supplied)
        except PermissionError:
            try:
                dashboard.record_admin_event("manage_skill", target_id=skill_id[:128], allowed=False,
                    remote_host=request.client.host if request.client else None,
                    detail="missing or invalid admin token")
            except (sqlite3.Error, OSError):
                return JSONResponse({"error": "audit_store_unavailable"}, status_code=503)
            return JSONResponse({"error": "admin_required"}, status_code=403)
        try:
            dashboard.record_admin_event("manage_skill", target_id=skill_id[:128], allowed=True,
                remote_host=request.client.host if request.client else None, detail="authorized")
        except (sqlite3.Error, OSError):
            return JSONResponse({"error": "audit_store_unavailable"}, status_code=503)
        try:
            body = await asyncio.wait_for(_small_json(request),timeout=2)
            action = body.get("action")
            if action in {"archive", "restore"}:
                if set(body) != {"action", "expected_updated_at", "reason"}:
                    raise ValueError("unexpected_fields")
                result = service.archive(supplied, skill_id, archived=action == "archive",
                    expected_updated_at=body["expected_updated_at"], reason=body["reason"])
            elif action == "request_revalidation":
                if set(body)!={"action","expected_updated_at","expected_event_id","reason"}:
                    raise ValueError("unexpected_fields")
                result=service.request_revalidation(supplied,skill_id,
                    expected_updated_at=body["expected_updated_at"],
                    expected_event_id=body["expected_event_id"],reason=body["reason"])
            elif action in {"confirm_feedback", "withdraw_feedback"}:
                if set(body) != {"action", "run_id", "request_id", "reason"}:
                    raise ValueError("unexpected_fields")
                result = service.feedback_statement(supplied, skill_id, run_id=body["run_id"],
                    confirmed=action == "confirm_feedback", request_id=body["request_id"],
                    reason=body["reason"])
            elif action == "negative_feedback":
                if set(body) != {"action", "run_id", "reason"}:
                    raise ValueError("unexpected_fields")
                result = service.negative_feedback(supplied, skill_id, run_id=body["run_id"], reason=body["reason"])
            else:
                raise ValueError("unsupported_action")
            return JSONResponse(result)
        except asyncio.TimeoutError:
            return JSONResponse({"error":"request_timeout"},status_code=408)
        except OverflowError:
            return JSONResponse({"error": "request_too_large"}, status_code=413)
        except (ValueError, KeyError, TypeError, RecursionError):
            return JSONResponse({"error": "invalid_or_stale_request"}, status_code=400)
        except (sqlite3.Error, OSError):
            return JSONResponse({"error": "skill_store_unavailable"}, status_code=503)
