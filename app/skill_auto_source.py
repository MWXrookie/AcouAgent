"""Server-owned first executions with immutable provenance for automatic learning.

Source draft, queued trial and pre-dispatch marker commit together. Ordinary
history, a caller's run id or a self-reported success cannot mint a source.
"""
from collections import OrderedDict
from datetime import datetime, timezone
import hashlib
import io
import json
import math
from pathlib import Path
import re
import threading
import time
import uuid

from app import config, dashboard, skill_general_profiles as profiles
from app.skill_drafts import DraftStore, DraftError
from app.skill_general_library import get_library, normalize_query
from app.skill_result_assets import decode_executor_field
from app.skill_spec import prepare_skill_spec
from app.skill_validation_jobs import ValidationJobs, canonical, digest, QUEUE_LIMIT
from app.skill_validation_worker import ValidationWorker, verify_recorded_attempt, read_unique_history

ACTOR = "internal-auto-source"
MARKER = "controlled-source-job"
_VERIFIED = OrderedDict()
_LOCK = threading.Lock()


def enabled():
    return (config.SKILL_LEARNING_ENABLED is True and config.SKILL_AUTHORING_ENABLED is True
            and config.SKILL_VALIDATION_ENABLED is True and config.SKILL_GENERAL_ADMISSION_ENABLED is True)


def _enabled():
    if not enabled():
        raise DraftError("learning_disabled")


class _SourceJobs(ValidationJobs):
    def __init__(self, library, job_id=None):
        super().__init__(library.store, library.runtime_sha256)
        self.selected = job_id

    @staticmethod
    def _enabled():
        ValidationJobs._enabled()
        if config.SKILL_GENERAL_ADMISSION_ENABLED is not True:
            raise DraftError("general_admission_disabled")

    def claim(self, owner):
        if not enabled():
            return None
        return super().claim(owner, expected_job_id=self.selected)

    def mark_dispatching(self, job_id, **kwargs):
        _enabled()
        return super().mark_dispatching(job_id, **kwargs)


def _binding(job, prepared):
    return dict(version="controlled-source.v1", job_id=job["job_id"], draft_id=job["draft_id"],
                draft_version=job["draft_version"], profile_id=job["profile_id"],
                runtime_sha256=job["runtime_sha256"] if "runtime_sha256" in job else None,
                content_sha256=prepared["content_sha256"], case_fingerprint=prepared["case_fingerprint"],
                code_sha256=prepared["code_sha256"], adapter_sha256=profiles.source_sha256())


def start_source(spec, *, request_key, library=None):
    _enabled()
    if type(request_key) is not str or re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", request_key) is None:
        raise DraftError("invalid_source_request_key")
    library = library or get_library()
    prepared = profiles.build_controlled_spec(canonical(spec))
    request_sha = digest(canonical(dict(content=prepared["content_sha256"], runtime=library.runtime_sha256,
                                        adapter=prepared["adapter_sha256"])))
    jobs = _SourceJobs(library)
    jobs.install_trial_profile()
    with DraftStore(library.store)._connection(True) as db:
        _enabled()
        old = db.execute("SELECT * FROM skill_draft_requests WHERE actor_id=? AND operation='source-request' AND idempotency_key=?",
                         (ACTOR, request_key)).fetchone()
        if old:
            if old["request_sha256"] != request_sha:
                raise DraftError("source_request_conflict")
            marker = db.execute("SELECT idempotency_key FROM skill_draft_requests WHERE actor_id=? AND operation=? AND draft_id=?",
                                (ACTOR, MARKER, old["draft_id"])).fetchone()
            if marker is None:
                raise DraftError("source_marker_missing")
            return ValidationJobs._view(db, ValidationJobs._row(db, marker[0]))
        jobs._profile(db)
        if db.execute("SELECT count(*) FROM skill_validation_jobs WHERE state='queued'").fetchone()[0] >= QUEUE_LIMIT:
            raise DraftError("queue_full")
        now = time.time_ns() // 1_000_000
        draft_id, job_id = str(uuid.uuid4()), str(uuid.uuid4())
        db.execute("INSERT INTO skill_drafts VALUES(?,'automatic',?,'submitted',1,2,?,?)", (draft_id, ACTOR, now, now))
        DraftStore._version(db, draft_id, 1, prepared, now)
        scope = digest(canonical([ACTOR, "trial"]))
        db.execute("""INSERT INTO skill_validation_jobs
            (job_id,draft_id,draft_version,profile_id,profile_version,intent,idempotency_scope,idempotency_key,
             request_sha256,state,revision,created_ms,updated_ms)
            VALUES(?,?,1,?,1,'trial',?,?,?,'queued',1,?,?)""",
                   (job_id, draft_id, jobs.profile_id, scope, request_key,
                    digest(canonical(dict(draft_id=draft_id, expected_revision=1, profile_id=jobs.profile_id, intent="trial"))), now, now))
        row = dict(ValidationJobs._row(db, job_id), runtime_sha256=library.runtime_sha256)
        marker_sha = digest(canonical(_binding(row, prepared)))
        for operation, key, sha in (("source-request", request_key, request_sha), (MARKER, job_id, marker_sha)):
            db.execute("INSERT INTO skill_draft_requests VALUES(?,?,?,?,?,?,?)", (ACTOR, operation, key, sha, draft_id, 1, now))
        _enabled()
        return ValidationJobs._view(db, ValidationJobs._row(db, job_id))


def _source_connection(db, run_id, library, *, _for_presentation=False):
    if _for_presentation:
        _SourceJobs._enabled()
    else:
        _enabled()
    try:
        if str(uuid.UUID(run_id)) != run_id:
            raise ValueError
    except (ValueError, TypeError, AttributeError):
        raise DraftError("invalid_source_run") from None
    rows = db.execute("SELECT job_id FROM skill_validation_attempts WHERE run_id=? LIMIT 2", (run_id,)).fetchall()
    if len(rows) != 1:
        raise DraftError("source_attempt_not_unique")
    job = ValidationJobs._view(db, ValidationJobs._row(db, rows[0][0]))
    prepared = prepare_skill_spec(canonical(job["spec"]))
    head = DraftStore._head(db, job["draft_id"], ACTOR)
    marker = db.execute("SELECT * FROM skill_draft_requests WHERE actor_id=? AND operation=? AND idempotency_key=?",
                        (ACTOR, MARKER, job["job_id"])).fetchone()
    expected = digest(canonical(_binding(dict(job, runtime_sha256=library.runtime_sha256), prepared)))
    if (marker is None or marker["request_sha256"] != expected or marker["draft_id"] != job["draft_id"]
            or marker["version"] != job["draft_version"] or head["origin"] != "automatic"
            or head["state"] == "archived" or head["current_version"] != job["draft_version"]
            or job["intent"] != "trial" or job["state"] != "rejected" or job["reason_code"] != "trial_completed_ungraded"
            or job["cancel_ms"] is not None or job["attempt"]["run_id"] != run_id
            or job["attempt"]["dispatch_state"] != "terminal" or job["attempt"]["runtime_sha256"] != library.runtime_sha256):
        raise DraftError("source_not_learnable")
    _SourceJobs(library)._profile(db)
    if job["profile_id"] != _SourceJobs(library).profile_id or profiles.build_controlled_spec(canonical(job["spec"]))["code_template"] != job["spec"]["code_template"]:
        raise DraftError("source_not_controlled")
    return job, prepared, expected


def verify_source(run_id, *, library=None, _for_presentation=False):
    if _for_presentation:
        _SourceJobs._enabled()
    else:
        _enabled()
    library = library or get_library()
    with DraftStore(library.store)._connection() as db:
        job, prepared, marker = _source_connection(db, run_id, library, _for_presentation=_for_presentation)
    attempt = job["attempt"]
    history = read_unique_history(run_id, attempt["request_nonce"])
    receipt_sha = verify_recorded_attempt(job, history)
    receipt = json.loads(history["executor_evidence_json"])["receipt"]
    runtime = receipt["runtime"]
    source = Path(__file__).resolve().parents[1] / "executor/executor.py"
    if (runtime["executor_source_sha256"] != hashlib.sha256(source.read_bytes().replace(b"\r\n", b"\n")).hexdigest()
            or runtime["library_versions"] != profiles.policy("B01", library.runtime_sha256)["library_versions"]
            or receipt_sha != attempt["receipt_sha256"] or history["exit_code"] != 0 or history["timed_out"] is not False):
        raise DraftError("source_runtime_or_receipt_changed")
    manifest_sha = attempt["output_manifest_sha256"]
    manifest = library.assets.read_manifest(manifest_sha)
    expected = {key: attempt[key] for key in ("run_id", "request_nonce", "request_sha256", "code_sha256", "parameters_sha256", "runtime_sha256", "attempt_no")}
    expected.update(job_id=job["job_id"], content_sha256=prepared["content_sha256"], case_fingerprint=prepared["case_fingerprint"],
                    result_spec=job["spec"]["result_spec"], receipt_sha256=receipt_sha)
    if any(manifest[key] != value for key, value in expected.items()):
        raise DraftError("source_manifest_mismatch")
    import numpy as np
    field = library.assets.read_field(manifest["field_asset_sha256"], manifest["result_spec"])
    raw_field = decode_executor_field(history, manifest["result_spec"])
    if field.dtype != raw_field.dtype or not np.array_equal(field, raw_field) or float(np.abs(field).max()) <= 0:
        raise DraftError("source_field_invalid")
    binding = digest(canonical(dict(marker_sha256=marker, run_id=run_id, nonce=attempt["request_nonce"],
                                    receipt_sha256=receipt_sha, output_manifest_sha256=manifest_sha)))
    with _LOCK:
        _VERIFIED[(str(library.store.path.resolve()), run_id)] = dict(binding=binding, marker=marker,
             manifest_sha256=manifest_sha, field_sha256=manifest["field_asset_sha256"], receipt_sha256=receipt_sha, dtype=str(field.dtype))
        while len(_VERIFIED) > 64:
            _VERIFIED.popitem(last=False)
    return dict(spec=prepared["spec"], prepared=prepared, source_run_id=run_id, source_binding_sha256=binding,
                source_job_id=job["job_id"], manifest=manifest)


def require_source_connection(db, run_id):
    """Commit-time veto: no array parsing, no network and no authority from cache."""
    library = get_library()
    job, _, marker = _source_connection(db, run_id, library)
    with _LOCK:
        checked = _VERIFIED.get((str(library.store.path.resolve()), run_id))
    if checked is None or checked["marker"] != marker:
        raise DraftError("source_preflight_required")
    receipt_sha = verify_recorded_attempt(job, read_unique_history(run_id, job["attempt"]["request_nonce"]))
    if (receipt_sha != checked["receipt_sha256"] or job["attempt"]["receipt_sha256"] != receipt_sha
            or job["attempt"]["output_manifest_sha256"] != checked["manifest_sha256"]):
        raise DraftError("source_changed_since_preflight")
    for sha, role, suffix, media in ((checked["manifest_sha256"], "evidence", "json", "application/json"),
                                     (checked["field_sha256"], "result", "npy", "application/x-npy")):
        row = db.execute("SELECT * FROM skill_validation_assets WHERE asset_sha256=?", (sha,)).fetchone()
        if (row is None or row["provenance"] != "unreviewed" or row["role"] != role
                or row["storage_key"] != sha + "." + suffix or row["media_type"] != media):
            raise DraftError("source_asset_changed")
        metadata = json.loads(row["metadata_json"])
        if ((role == "evidence" and metadata != {"kind": "execution_binding_v1"})
                or (role == "result" and metadata != {"result_spec": job["spec"]["result_spec"], "dtype": checked["dtype"]})):
            raise DraftError("source_asset_changed")
        raw = library.assets._bytes(library.assets._path(row["storage_key"]))
        if len(raw) != row["byte_count"] or hashlib.sha256(raw).hexdigest() != sha:
            raise DraftError("source_asset_changed")
    return True


def _observe(library, job):
    if not job or job["state"] != "rejected" or job["reason_code"] != "trial_completed_ungraded":
        return {"recorded": False, "reason": "source_pending_or_failed"}
    from app.skill_auto_learning import AutoLearning
    return AutoLearning(library).observe(job["attempt"]["run_id"])


def _source_history_writer(_tool_name, *args, **kwargs):
    return dashboard.record_execution("automatic_source_execute", *args, **kwargs)


def tick_source(library, owner):
    _enabled()
    jobs = _SourceJobs(library)
    jobs.recover_expired()
    with DraftStore(library.store)._connection() as db:
        active = db.execute("SELECT * FROM skill_validation_jobs WHERE state IN ('running','cancel_requested','needs_reconciliation') ORDER BY created_ms,job_id LIMIT 1").fetchone()
        row = active or db.execute("SELECT * FROM skill_validation_jobs WHERE state='queued' ORDER BY created_ms,job_id LIMIT 1").fetchone()
        if row is None:
            # Do not starve an already validated learning job while recovering
            # the separate source-completed -> observe crash window.
            if db.execute("""SELECT 1 FROM skill_validation_jobs j JOIN skill_drafts d ON d.draft_id=j.draft_id
                WHERE d.actor_id='internal-auto-learning' AND j.state='validated' LIMIT 1""").fetchone():
                return None
            completed = db.execute("""SELECT j.job_id,a.run_id FROM skill_validation_jobs j
                JOIN skill_draft_requests r ON r.idempotency_key=j.job_id AND r.actor_id=? AND r.operation=?
                JOIN skill_validation_attempts a ON a.job_id=j.job_id AND a.attempt_no=j.attempt_no
                WHERE j.state='rejected' AND j.reason_code='trial_completed_ungraded'
                AND NOT EXISTS(SELECT 1 FROM skill_draft_requests x WHERE
                    x.actor_id='internal-auto-learning' AND x.operation='auto-source' AND x.idempotency_key=a.run_id)
                AND NOT EXISTS(SELECT 1 FROM skill_draft_requests x WHERE
                    x.actor_id=? AND x.operation='source-observation-rejected' AND x.idempotency_key=a.run_id)
                ORDER BY j.created_ms,j.job_id LIMIT 1""", (ACTOR, MARKER, ACTOR)).fetchone()
            if completed is None:
                return None
            job_id, state = completed["job_id"], "completed_unobserved"
        else:
            job_id, state = row["job_id"], row["state"]
        marker = db.execute("SELECT 1 FROM skill_draft_requests WHERE actor_id=? AND operation=? AND idempotency_key=?", (ACTOR, MARKER, job_id)).fetchone()
        if marker is None:
            return None
    jobs.selected = job_id
    worker = ValidationWorker(jobs, library.assets, history_writer=_source_history_writer)
    if state == "queued":
        job = worker.run_once(owner)
    elif state == "completed_unobserved":
        job = jobs.worker_get(job_id)
    elif state == "needs_reconciliation":
        job = worker.reconcile(job_id)
    else:
        return {"reason": "source_in_progress", "job_id": job_id}
    learning = _observe(library, job)
    if learning.get("reason") in {"unsupported_learning_case", "withdrawn_skill_cannot_be_relearned"} and job and job["attempt"]:
        with DraftStore(library.store)._connection(True) as db:
            _enabled()
            key = job["attempt"]["run_id"]
            if not db.execute("SELECT 1 FROM skill_draft_requests WHERE actor_id=? AND operation='source-observation-rejected' AND idempotency_key=?", (ACTOR, key)).fetchone():
                db.execute("INSERT INTO skill_draft_requests VALUES(?,?,?,?,?,?,?)", (ACTOR, "source-observation-rejected", key,
                    digest(canonical(dict(reason=learning["reason"], source_run_id=key))), job["draft_id"], job["draft_version"], time.time_ns() // 1_000_000))
    return {"reason": "source_processed", "job_id": job_id, "learning": learning}


def _analysis(library, manifest):
    view = library.assets.present(manifest["field_asset_sha256"], manifest["result_spec"])
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    fig = Figure(figsize=(6, 4.5)); canvas = FigureCanvasAgg(fig)
    ax = fig.add_subplot(); picture = ax.imshow(view["amplitude"], origin="lower")
    ax.set_xlabel("y grid index"); ax.set_ylabel("x grid index")
    fig.colorbar(picture, ax=ax, label="Magnitude (numerical source normalization)")
    buffer = io.BytesIO(); canvas.print_png(buffer)
    import base64
    return dict(verdict="normal", quality_level="Q2", max_pressure=view["max_pressure"], rms_pressure=view["rms_pressure"],
                field_shape=view["shape"], has_signal=True, units=view["units"],
                summary="Controlled first execution: finite nonzero full complex field. Independent admission is a separate new validation run.",
                heatmap_png_base64=base64.b64encode(buffer.getvalue()).decode("ascii"))


def execute_source(*, scenario_type, parameters_json, request_key):
    _enabled(); library = get_library()
    query = normalize_query(scenario_type, parameters_json)
    spec = dict(schema_version="skill-spec.v1", name="Automatic controlled source", description="Server structured physical request",
                scenario_type=scenario_type, parameters=query["parameters"], result_spec=query["result_spec"],
                code_template="pass\n", validation_request={"policy": "server_required"})
    job = start_source(spec, request_key=request_key, library=library)
    jobs = _SourceJobs(library, job["job_id"])
    if job["state"] == "queued":
        worker_result = ValidationWorker(jobs, library.assets, history_writer=_source_history_writer).run_once("source-" + uuid.uuid4().hex)
        job = worker_result or jobs.worker_get(job["job_id"])
    if job["state"] != "rejected" or job["reason_code"] != "trial_completed_ungraded":
        return dict(source_execution_verified=False, job_id=job["job_id"], run_id=job["attempt"]["run_id"] if job["attempt"] else None,
                    reason="source_pending_or_failed")
    source = verify_source(job["attempt"]["run_id"], library=library, _for_presentation=True)
    analyzed = _analysis(library, source["manifest"])
    dashboard.record_analysis_event(0, "normal", analyzed["max_pressure"], analyzed["rms_pressure"], analyzed["field_shape"], True,
        analyzed["summary"] + " Units: numerical source normalization; not calibrated Pa.", analyzed["heatmap_png_base64"], None, "", "", run_id=source["source_run_id"])
    learning = {"recorded": False, "reason": "learning_pending"}
    try:
        from app.skill_auto_learning import AutoLearning
        engine = AutoLearning(library)
        learning = engine.observe(source["source_run_id"])
        progress = engine.tick("inline-learning-" + uuid.uuid4().hex)
        learning = dict(learning, progress=progress, counts=engine.counts())
    except Exception:
        learning = {"recorded": False, "reason": "learning_deferred"}
    return dict(source_execution_verified=True, run_id=source["source_run_id"], job_id=job["job_id"], analysis=analyzed, learning=learning)
