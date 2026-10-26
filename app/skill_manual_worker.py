"""Default-off manual job pump; existing single-slot/fencing rules remain authoritative."""
import logging
import threading
import uuid

from app import config
from app.skill_authoring_schema import schema_ready
from app.skill_general_library import get_library
from app.skill_general_validation import GeneralValidationJobs, GeneralValidationWorker
from app.skill_validation_jobs import ValidationJobs
from app.skill_validation_worker import ValidationWorker

ACTOR = "portal-admin"
LOG = logging.getLogger(__name__)
_LOCK = threading.Lock()
_WORKER = None


def enabled():
    return config.SKILL_AUTHORING_ENABLED is True and config.SKILL_VALIDATION_ENABLED is True


class _ManualClaim:
    def claim(self, owner, *, expected_job_id=None):
        if not enabled():
            return None
        if self.policy.get("grants_quality") is not False and config.SKILL_GENERAL_ADMISSION_ENABLED is not True:
            return None
        with self._connection() as db:
            row = db.execute("""SELECT j.job_id,d.actor_id,d.origin FROM skill_validation_jobs j
                JOIN skill_drafts d ON d.draft_id=j.draft_id
                WHERE j.state='queued' ORDER BY j.created_ms,j.job_id LIMIT 1""").fetchone()
        if row is None or row["actor_id"] != ACTOR or row["origin"] != "manual":
            return None
        if expected_job_id is not None and row["job_id"] != expected_job_id:
            return None
        return super().claim(owner, expected_job_id=row["job_id"])


class ManualTrialJobs(_ManualClaim, ValidationJobs):
    pass


class ManualGeneralJobs(_ManualClaim, GeneralValidationJobs):
    pass


def components(library, job):
    if job["intent"] == "trial":
        jobs = ManualTrialJobs(library.store, library.runtime_sha256)
        return jobs, ValidationWorker(jobs, library.assets)
    from app.skill_general_profiles import match_profile
    jobs = ManualGeneralJobs(library.store, library.runtime_sha256, match_profile(job["spec"]), library)
    return jobs, GeneralValidationWorker(jobs, library.assets, library)


def tick(library, owner):
    if not enabled():
        return None
    base = ValidationJobs(library.store, library.runtime_sha256)
    base.recover_expired()
    with base._connection() as db:
        row = db.execute("""SELECT j.job_id,j.state,d.actor_id,d.origin FROM skill_validation_jobs j
            JOIN skill_drafts d ON d.draft_id=j.draft_id
            WHERE j.state IN ('running','cancel_requested','needs_reconciliation','queued')
            ORDER BY CASE WHEN j.state='queued' THEN 1 ELSE 0 END,j.created_ms,j.job_id LIMIT 1""").fetchone()
    if row is None or row["actor_id"] != ACTOR or row["origin"] != "manual":
        return None
    if row["state"] in ("running", "cancel_requested"):
        return None
    job = base.worker_get(row["job_id"])
    if job["intent"] == "validate" and config.SKILL_GENERAL_ADMISSION_ENABLED is not True:
        return None
    _, worker = components(library, job)
    return worker.reconcile(job["job_id"]) if row["state"] == "needs_reconciliation" else worker.run_once(owner)


class ManualWorker:
    def __init__(self, library, interval=5):
        if type(interval) not in (int, float) or not 0 < interval <= 60:
            raise ValueError("invalid_manual_interval")
        self.library, self.interval = library, interval
        self.owner = "manual-worker-" + uuid.uuid4().hex
        self._stop = threading.Event()
        self._thread = None
        self._start_lock = threading.Lock()

    def start(self):
        with self._start_lock:
            if not enabled():
                return None
            if self._stop.is_set():
                raise RuntimeError("manual_worker_stopped")
            if self._thread is None:
                self._thread = threading.Thread(target=self._run, name="acouagent-manual-jobs", daemon=True)
                self._thread.start()
        return self

    def _run(self):
        while not self._stop.wait(self.interval):
            try:
                tick(self.library, self.owner)
            except Exception:
                LOG.warning("manual_validation_tick_failed")

    def is_alive(self):
        return self._thread is not None and self._thread.is_alive()

    def stop(self):
        self._stop.set()
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout=1)
        return not self.is_alive()


def start_worker():
    global _WORKER
    if not enabled():
        return None
    with _LOCK:
        if _WORKER is not None and _WORKER.is_alive():
            return _WORKER
        try:
            library = get_library()
            with library.store._read_db() as db:
                if not schema_ready(db):
                    return None
            worker = ManualWorker(library)
            _WORKER = worker.start()
            return _WORKER
        except Exception:
            LOG.warning("manual_validation_worker_not_ready")
            return None
