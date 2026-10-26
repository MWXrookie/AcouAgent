"""Opt-in persistent trial jobs. A trial never validates or admits a skill.

SQLite transactions contain bookkeeping only. Unknown dispatches retain the
single execution slot until a trusted terminal response has been reconciled.
"""
import hashlib
import json
import re
import time
import uuid

from app import config
from app.skill_drafts import DraftStore, DraftError, _identity, _identifier, _revision

LEASE_MS = 120_000
JOB_BUDGET_MS = 180_000
QUEUE_LIMIT = 100
TIMEOUT_SECONDS = 30
OCCUPYING = ("running", "cancel_requested", "needs_reconciliation")


def _now():
    return time.time_ns() // 1_000_000


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _hash(value):
    if type(value) is not str or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise DraftError("invalid_digest")
    return value


class ValidationJobs(DraftStore):
    def __init__(self, store, runtime_sha256):
        super().__init__(store)
        self.runtime_sha256 = _hash(runtime_sha256)
        self.profile_id = "trial-" + runtime_sha256
        self.policy = dict(version="trial-complex-pressure.v1", runtime_sha256=runtime_sha256,
                           timeout_seconds=30, grants_quality=False,
                           result_kind="steady_complex_pressure")
        self.policy_json = canonical(self.policy)

    @staticmethod
    def _enabled():
        DraftStore._enabled()
        if getattr(config, "SKILL_VALIDATION_ENABLED", False) is not True:
            raise DraftError("validation_disabled")

    def install_trial_profile(self):
        """Explicit operator registration, never a client-selected validator."""
        with self._connection(True) as db:
            row = db.execute("SELECT * FROM skill_validation_profiles WHERE profile_id=? AND version=1",
                             (self.profile_id,)).fetchone()
            if row is None:
                db.execute("""INSERT INTO skill_validation_profiles VALUES(?,1,?,?,?,?,'enabled',?)""",
                           (self.profile_id, self.policy_json, digest(self.policy_json),
                            "trial_only", digest("trial_only.no_scientific_validation.v1"), _now()))
            self._profile(db)
        return {"profile_id": self.profile_id, "version": 1, "grants_quality": False}

    def _profile(self, db):
        row = db.execute("SELECT * FROM skill_validation_profiles WHERE profile_id=? AND version=1",
                         (self.profile_id,)).fetchone()
        if (row is None or row["state"] != "enabled" or row["policy_json"] != self.policy_json
                or row["policy_sha256"] != digest(self.policy_json) or row["validator_id"] != "trial_only"
                or row["validator_sha256"] != digest("trial_only.no_scientific_validation.v1")):
            raise DraftError("profile_not_available")
        return row

    @staticmethod
    def _row(db, job_id, actor_id=None):
        row = db.execute("""SELECT j.* FROM skill_validation_jobs j JOIN skill_drafts d
            ON d.draft_id=j.draft_id WHERE j.job_id=?""" + (" AND d.actor_id=?" if actor_id is not None else ""),
                         (job_id, actor_id) if actor_id is not None else (job_id,)).fetchone()
        if row is None:
            raise DraftError("not_found")
        return row

    @staticmethod
    def _view(db, row):
        result = dict(row)
        version = db.execute("SELECT * FROM skill_draft_versions WHERE draft_id=? AND version=?",
                             (row["draft_id"], row["draft_version"])).fetchone()
        if version is None:
            raise DraftError("draft_store_unavailable")
        # Recompute the frozen draft identities before any execution resumes.
        from app.skill_spec import prepare_skill_spec
        prepared = prepare_skill_spec(version["spec_json"])
        for key in ("content_sha256", "case_fingerprint", "code_sha256", "normalizer_version", "fingerprint_version"):
            if prepared[key] != version[key]:
                raise DraftError("draft_integrity_failed")
            result[key] = version[key]
        result["spec"] = prepared["spec"]
        attempt = db.execute("SELECT * FROM skill_validation_attempts WHERE job_id=? AND attempt_no=?",
                             (row["job_id"], row["attempt_no"])).fetchone()
        result["attempt"] = dict(attempt) if attempt else None
        return result

    def get(self, job_id, *, actor_id):
        _identifier(job_id); _identity(actor_id, "invalid_actor")
        with self._connection() as db:
            return self._view(db, self._row(db, job_id, actor_id))

    def worker_get(self, job_id):
        _identifier(job_id)
        with self._connection() as db:
            return self._view(db, self._row(db, job_id))

    @staticmethod
    def _intent(intent):
        if intent != "trial":
            raise DraftError("validation_not_implemented")
        return intent

    def enqueue(self, draft_id, *, actor_id, expected_revision, idempotency_key, intent="trial"):
        self._enabled()
        _identifier(draft_id); _identity(actor_id, "invalid_actor"); _revision(expected_revision)
        intent = self._intent(intent)
        if type(idempotency_key) is not str or re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", idempotency_key) is None:
            raise DraftError("invalid_idempotency_key")
        # The actor is server-assigned; no credential or caller-selected policy is persisted.
        scope = digest(canonical([actor_id, intent]))
        with self._connection(True) as db:
            head = self._head(db, draft_id, actor_id)
            request_sha = digest(canonical(dict(draft_id=draft_id, expected_revision=expected_revision,
                                                profile_id=self.profile_id, intent=intent)))
            old = db.execute("SELECT * FROM skill_validation_jobs WHERE idempotency_scope=? AND idempotency_key=?",
                             (scope, idempotency_key)).fetchone()
            if old:
                if old["request_sha256"] != request_sha:
                    raise DraftError("idempotency_conflict")
                return dict(self._view(db, old), duplicate=True)
            self._profile(db)
            if head["revision"] != expected_revision:
                raise DraftError("revision_conflict")
            if head["state"] != "editing":
                raise DraftError("draft_not_editable")
            if db.execute("SELECT count(*) FROM skill_validation_jobs WHERE state='queued'").fetchone()[0] >= QUEUE_LIMIT:
                raise DraftError("queue_full")
            job_id, now = str(uuid.uuid4()), _now()
            db.execute("""INSERT INTO skill_validation_jobs
                (job_id,draft_id,draft_version,profile_id,profile_version,intent,idempotency_scope,
                 idempotency_key,request_sha256,state,revision,created_ms,updated_ms)
                VALUES(?,?,?,?,1,?,?,?,?,'queued',1,?,?)""",
                       (job_id, draft_id, head["current_version"], self.profile_id, intent, scope,
                        idempotency_key, request_sha, now, now))
            db.execute("UPDATE skill_drafts SET state='submitted',revision=revision+1,updated_ms=? WHERE draft_id=?",
                       (now, draft_id))
            return dict(self._view(db, self._row(db, job_id)), duplicate=False)

    @staticmethod
    def _release_draft(db, row, now):
        db.execute("""UPDATE skill_drafts SET state='editing',revision=revision+1,updated_ms=?
            WHERE draft_id=? AND state='submitted' AND current_version=?""",
                   (now, row["draft_id"], row["draft_version"]))

    def _recover(self, db, now):
        rows = db.execute("""SELECT * FROM skill_validation_jobs WHERE
            state IN ('running','cancel_requested') AND (lease_until_ms<=? OR created_ms+?<=?)""",
                          (now, JOB_BUDGET_MS, now)).fetchall()
        for row in rows:
            attempt = db.execute("SELECT * FROM skill_validation_attempts WHERE job_id=? AND attempt_no=?",
                                 (row["job_id"], row["attempt_no"])).fetchone()
            prepared = attempt is not None and attempt["dispatch_state"] == "prepared"
            if prepared:
                state = ("cancelled" if row["cancel_ms"] is not None else
                         "failed" if row["created_ms"] + JOB_BUDGET_MS <= now else "queued")
                reason = "cancelled_before_dispatch" if state == "cancelled" else "job_budget_exhausted" if state == "failed" else "prepared_recovered"
                db.execute("UPDATE skill_validation_attempts SET dispatch_state='terminal',updated_ms=? WHERE job_id=? AND attempt_no=?",
                           (now, row["job_id"], row["attempt_no"]))
            else:
                state, reason = "needs_reconciliation", "dispatch_outcome_unknown"
                db.execute("UPDATE skill_validation_attempts SET dispatch_state='unknown',updated_ms=? WHERE job_id=? AND attempt_no=? AND dispatch_state!='terminal'",
                           (now, row["job_id"], row["attempt_no"]))
            db.execute("""UPDATE skill_validation_jobs SET state=?,reason_code=?,revision=revision+1,
                fence=fence+1,lease_owner=NULL,lease_until_ms=NULL,updated_ms=? WHERE job_id=?""",
                       (state, reason, now, row["job_id"]))
            if state in ("cancelled", "failed"):
                self._release_draft(db, row, now)
        return len(rows)

    def recover_expired(self):
        with self._connection(True) as db:
            return self._recover(db, _now())

    def claim(self, owner, *, expected_job_id=None):
        _identity(owner, "invalid_owner")
        if expected_job_id is not None:
            _identifier(expected_job_id)
        with self._connection(True) as db:
            now = _now()
            self._recover(db, now)
            if db.execute("SELECT 1 FROM skill_validation_jobs WHERE state IN (?,?,?) LIMIT 1", OCCUPYING).fetchone():
                return None
            self._profile(db)
            row = db.execute("SELECT * FROM skill_validation_jobs WHERE state='queued' ORDER BY created_ms,job_id LIMIT 1").fetchone()
            if row is None:
                return None
            if expected_job_id is not None and row["job_id"] != expected_job_id:
                return None
            if row["created_ms"] + JOB_BUDGET_MS <= now:
                db.execute("UPDATE skill_validation_jobs SET state='failed',reason_code='job_budget_exhausted',revision=revision+1,updated_ms=? WHERE job_id=?",
                           (now, row["job_id"]))
                self._release_draft(db, row, now)
                return None
            if row["profile_id"] != self.profile_id:
                raise DraftError("profile_not_available")
            view = self._view(db, row)
            run_id, nonce = str(uuid.uuid4()), uuid.uuid4().hex
            number, fence = row["attempt_no"] + 1, row["fence"] + 1
            code = view["spec"]["code_template"]
            request_sha = digest(canonical(dict(code=code, timeout_seconds=30, request_nonce=nonce)))
            parameters_sha = digest(canonical(view["spec"]["parameters"]))
            db.execute("""INSERT INTO skill_validation_attempts
                (job_id,attempt_no,fence,run_id,request_nonce,request_sha256,code_sha256,
                 parameters_sha256,runtime_sha256,dispatch_state,created_ms,updated_ms)
                VALUES(?,?,?,?,?,?,?,?,?,'prepared',?,?)""",
                       (row["job_id"], number, fence, run_id, nonce, request_sha,
                        view["code_sha256"], parameters_sha, self.runtime_sha256, now, now))
            db.execute("""UPDATE skill_validation_jobs SET state='running',reason_code=NULL,
                revision=revision+1,attempt_no=?,fence=?,lease_owner=?,lease_until_ms=?,updated_ms=? WHERE job_id=?""",
                       (number, fence, owner, now + LEASE_MS, now, row["job_id"]))
            return self._view(db, self._row(db, row["job_id"]))

    def _owned(self, db, job_id, owner, fence, expected_revision):
        _identifier(job_id); _identity(owner, "invalid_owner"); _revision(fence); _revision(expected_revision)
        row = self._row(db, job_id)
        if row["revision"] != expected_revision:
            raise DraftError("revision_conflict")
        if (row["state"] not in ("running", "cancel_requested") or row["lease_owner"] != owner
                or row["fence"] != fence or row["lease_until_ms"] is None or row["lease_until_ms"] <= _now()
                or row["created_ms"] + JOB_BUDGET_MS <= _now()):
            raise DraftError("lease_lost")
        return row

    def heartbeat(self, job_id, *, owner, fence, expected_revision):
        with self._connection(True) as db:
            row = self._owned(db, job_id, owner, fence, expected_revision)
            now = _now()
            db.execute("UPDATE skill_validation_jobs SET revision=revision+1,lease_until_ms=?,updated_ms=? WHERE job_id=?",
                       (min(now + LEASE_MS, row["created_ms"] + JOB_BUDGET_MS), now, job_id))
            return self._view(db, self._row(db, job_id))

    def mark_dispatching(self, job_id, *, owner, fence, expected_revision):
        with self._connection(True) as db:
            row = self._owned(db, job_id, owner, fence, expected_revision)
            self._profile(db)
            if row["state"] != "running" or row["cancel_ms"] is not None:
                raise DraftError("dispatch_cancelled")
            changed = db.execute("""UPDATE skill_validation_attempts SET dispatch_state='dispatching',updated_ms=?
                WHERE job_id=? AND attempt_no=? AND dispatch_state='prepared'""", (_now(), job_id, row["attempt_no"]))
            if changed.rowcount != 1:
                raise DraftError("already_dispatched")
            db.execute("UPDATE skill_validation_jobs SET revision=revision+1,updated_ms=? WHERE job_id=?", (_now(), job_id))
            return self._view(db, self._row(db, job_id))

    def mark_unknown(self, job_id, *, owner, fence, expected_revision, reason="dispatch_outcome_unknown"):
        with self._connection(True) as db:
            row = self._owned(db, job_id, owner, fence, expected_revision)
            attempt = db.execute("SELECT dispatch_state FROM skill_validation_attempts WHERE job_id=? AND attempt_no=?", (job_id, row["attempt_no"])).fetchone()
            if not attempt or attempt[0] not in ("dispatching", "unknown"):
                raise DraftError("invalid_dispatch_state")
            db.execute("UPDATE skill_validation_attempts SET dispatch_state='unknown',updated_ms=? WHERE job_id=? AND attempt_no=?", (_now(), job_id, row["attempt_no"]))
            db.execute("""UPDATE skill_validation_jobs SET state='needs_reconciliation',reason_code='dispatch_outcome_unknown',
                revision=revision+1,fence=fence+1,lease_owner=NULL,lease_until_ms=NULL,updated_ms=? WHERE job_id=?""", (_now(), job_id))
            return self._view(db, self._row(db, job_id))

    def cancel(self, job_id, *, actor_id, expected_revision):
        _identifier(job_id); _identity(actor_id, "invalid_actor"); _revision(expected_revision)
        with self._connection(True) as db:
            row = self._row(db, job_id, actor_id)
            if row["revision"] != expected_revision:
                raise DraftError("revision_conflict")
            if row["state"] in ("cancelled", "failed", "rejected"):
                return self._view(db, row)
            if row["state"] not in ("queued", "running", "cancel_requested", "needs_reconciliation"):
                raise DraftError("job_not_cancellable")
            now = _now()
            state = "cancelled" if row["state"] == "queued" else row["state"] if row["state"] == "needs_reconciliation" else "cancel_requested"
            db.execute("UPDATE skill_validation_jobs SET state=?,cancel_ms=COALESCE(cancel_ms,?),reason_code='cancellation_requested',revision=revision+1,updated_ms=? WHERE job_id=?",
                       (state, now, now, job_id))
            if state == "cancelled":
                self._release_draft(db, row, now)
            return self._view(db, self._row(db, job_id))

    def _complete(self, db, row, manifest_sha256, receipt_sha256, succeeded, reason):
        # Called only by the internal response-verifying worker, never HTTP.
        _hash(receipt_sha256)
        if type(succeeded) is not bool:
            raise DraftError("invalid_result")
        if succeeded:
            _hash(manifest_sha256)
            asset = db.execute("SELECT role,provenance FROM skill_validation_assets WHERE asset_sha256=?", (manifest_sha256,)).fetchone()
            if asset is None or asset["role"] != "evidence" or asset["provenance"] != "unreviewed":
                raise DraftError("result_asset_required")
        if reason not in ("trial_completed_ungraded", "executor_failed", "invalid_result", "result_storage_failed"):
            raise DraftError("invalid_reason")
        cancelled = row["cancel_ms"] is not None
        state = "cancelled" if cancelled else "rejected" if succeeded else "failed"
        reason = "cancelled_after_execution" if cancelled else "trial_completed_ungraded" if succeeded else reason
        now = _now()
        db.execute("""UPDATE skill_validation_attempts SET dispatch_state='terminal',receipt_sha256=?,
            output_manifest_sha256=?,updated_ms=? WHERE job_id=? AND attempt_no=?""",
                   (receipt_sha256, manifest_sha256, now, row["job_id"], row["attempt_no"]))
        db.execute("""UPDATE skill_validation_jobs SET state=?,reason_code=?,revision=revision+1,
            lease_owner=NULL,lease_until_ms=NULL,updated_ms=? WHERE job_id=?""", (state, reason, now, row["job_id"]))
        self._release_draft(db, row, now)
        return self._view(db, self._row(db, row["job_id"]))

    def finish(self, job_id, *, owner, fence, expected_revision, manifest_sha256=None,
               receipt_sha256=None, succeeded=False, reason="executor_failed"):
        with self._connection(True) as db:
            row = self._owned(db, job_id, owner, fence, expected_revision)
            attempt = db.execute("SELECT dispatch_state FROM skill_validation_attempts WHERE job_id=? AND attempt_no=?", (job_id, row["attempt_no"])).fetchone()
            if attempt is None or attempt[0] != "dispatching":
                raise DraftError("invalid_dispatch_state")
            return self._complete(db, row, manifest_sha256, receipt_sha256, succeeded, reason)

    def reconcile(self, job_id, *, expected_revision, manifest_sha256=None, receipt_sha256=None,
                  succeeded=False, reason="executor_failed"):
        _identifier(job_id); _revision(expected_revision)
        with self._connection(True) as db:
            row = self._row(db, job_id)
            if row["revision"] != expected_revision:
                raise DraftError("revision_conflict")
            if row["state"] != "needs_reconciliation":
                raise DraftError("reconciliation_not_required")
            attempt = db.execute("SELECT dispatch_state FROM skill_validation_attempts WHERE job_id=? AND attempt_no=?", (job_id, row["attempt_no"])).fetchone()
            if not attempt or attempt[0] != "unknown":
                raise DraftError("invalid_dispatch_state")
            return self._complete(db, row, manifest_sha256, receipt_sha256, succeeded, reason)
