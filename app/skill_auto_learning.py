"""Opt-in automatic learning from immutable server execution sources.

This scheduler never grants trust itself. It persists provenance and progress,
then uses the same controlled execution, independent validation and atomic
admission gates as manual skills. Unknown dispatches are never replayed.
"""
import json
import re
import sqlite3
import time
import uuid

from app import config, skill_general_profiles as profiles
from app.skill_drafts import DraftError, DraftStore, _identifier
from app.skill_general_validation import GeneralValidationJobs, GeneralValidationWorker
from app.skill_spec import prepare_skill_spec
from app.skill_validation_jobs import ValidationJobs, canonical, digest, _hash

ACTOR = "internal-auto-learning"
OPERATION = "auto-source"
EVENT = "automatic_skill_learning.v1"
TERMINAL = {"admitted", "cancelled", "failed", "rejected"}


def enabled():
    return (config.SKILL_LEARNING_ENABLED is True and config.SKILL_AUTHORING_ENABLED is True
            and config.SKILL_VALIDATION_ENABLED is True and config.SKILL_GENERAL_ADMISSION_ENABLED is True)


def _require_enabled():
    if not enabled():
        raise DraftError("learning_disabled")


class _LearningDrafts(DraftStore):
    @staticmethod
    def _enabled():
        _require_enabled()


class _SelectedJobs(GeneralValidationJobs):
    def __init__(self, store, runtime_sha256, case, library, job_id):
        super().__init__(store, runtime_sha256, case, library)
        self.selected_job_id = job_id

    def claim(self, owner):
        return super().claim(owner, expected_job_id=self.selected_job_id)


class AutoLearning:
    def __init__(self, library, source_verifier=None):
        self.library = library
        self.store = library.store
        self.drafts = _LearningDrafts(self.store)
        self.source_verifier = source_verifier

    def _source(self, run_id):
        _require_enabled()
        verifier = self.source_verifier
        if verifier is None:
            from app.skill_auto_source import verify_source
            verifier = verify_source
        try:
            _identifier(run_id)
            source = verifier(run_id)
            if type(source) is not dict or source.get("source_run_id") != run_id:
                raise ValueError("invalid_source")
            prepared = prepare_skill_spec(canonical(source["spec"]))
            if source.get("prepared", {}).get("content_sha256") != prepared["content_sha256"]:
                raise ValueError("source_content_mismatch")
            _hash(source["source_binding_sha256"])
        except (ValueError, TypeError, KeyError, OSError, sqlite3.Error):
            raise DraftError("automatic_source_unavailable") from None
        controlled = profiles.build_controlled_spec(canonical(prepared["spec"]))
        case = profiles.match_profile(controlled)
        if case is None:
            raise DraftError("unsupported_learning_case")
        return source, prepared, controlled, case

    def _lifecycle(self, db, fingerprint):
        row = db.execute("SELECT skill_id,status,archived FROM simulation_skills WHERE fingerprint=?", (fingerprint,)).fetchone()
        if row and (row["archived"] or row["status"] in ("questioned", "retired")
                    or db.execute("SELECT 1 FROM skill_feedback WHERE skill_id=? LIMIT 1", (row["skill_id"],)).fetchone()):
            raise DraftError("withdrawn_skill_cannot_be_relearned")
        return row

    def _profile(self, db, case):
        try:
            return self.library.require_profile(db, case, self.library.runtime_sha256)
        except ValueError:
            raise DraftError("automatic_profile_unavailable") from None

    def _request(self, db, run_id):
        return db.execute("SELECT * FROM skill_draft_requests WHERE actor_id=? AND operation=? AND idempotency_key=?",
                          (ACTOR, OPERATION, run_id)).fetchone()

    def _job_for_draft(self, db, draft_id, run_id):
        return db.execute("""SELECT * FROM skill_validation_jobs WHERE draft_id=?
            AND idempotency_scope=? AND idempotency_key=?""",
                          (draft_id, digest(canonical([ACTOR, "validate"])), run_id)).fetchone()

    def _new_source_draft(self, run_id, source, prepared):
        with self.drafts._connection(True) as db:
            prior = self._request(db, run_id)
            if prior is not None:
                if prior["request_sha256"] != source["source_binding_sha256"]:
                    raise DraftError("automatic_source_changed")
                return prior["draft_id"], True
            self._lifecycle(db, prepared["case_fingerprint"])
            draft_id, now = str(uuid.uuid4()), time.time_ns() // 1_000_000
            db.execute("INSERT INTO skill_drafts VALUES(?,'automatic',?,'editing',1,1,?,?)", (draft_id, ACTOR, now, now))
            self.drafts._version(db, draft_id, 1, prepared, now, source_run_id=run_id)
            db.execute("INSERT INTO skill_draft_requests VALUES(?,?,?,?,?,1,?)",
                       (ACTOR, OPERATION, run_id, source["source_binding_sha256"], draft_id, now))
            return draft_id, False

    def _event(self, job, run_id, binding, case, *, state="pending", owner=None):
        identity = dict(source_run_id=run_id, source_binding_sha256=binding, draft_id=job["draft_id"], case=case)
        payload = canonical(dict(identity, reason_code="job_" + job["state"]))
        with self.drafts._connection(True) as db:
            old = db.execute("SELECT * FROM skill_validation_outbox WHERE dedupe_key=?", ("auto:" + run_id,)).fetchone()
            now = time.time_ns() // 1_000_000
            if old is None:
                db.execute("""INSERT INTO skill_validation_outbox
                    (event_id,dedupe_key,job_id,event_type,payload_json,state,revision,attempts,
                     lease_owner,lease_until_ms,next_ms,created_ms,updated_ms)
                    VALUES(?,?,?,?,?,?,1,0,?,NULL,?,?,?)""",
                           (str(uuid.uuid4()), "auto:" + run_id, job["job_id"], EVENT, payload, state, owner, now, now, now))
            else:
                previous = json.loads(old["payload_json"])
                if (old["job_id"] != job["job_id"] or old["event_type"] != EVENT
                        or type(previous) is not dict or set(previous) - set(identity) - {"reason_code"}
                        or any(previous.get(key) != value for key, value in identity.items())):
                    raise DraftError("automatic_event_conflict")
                db.execute("""UPDATE skill_validation_outbox SET state=?,payload_json=?,revision=revision+1,
                    attempts=attempts+?,lease_owner=?,lease_until_ms=NULL,updated_ms=? WHERE event_id=?""",
                           (state, payload, int(state == "processing"), owner, now, old["event_id"]))

    @staticmethod
    def _reason(error):
        value = error.code if isinstance(error, DraftError) else "automatic_learning_unavailable"
        return value if type(value) is str and re.fullmatch(r"[a-z_]{1,64}", value) else "automatic_learning_unavailable"

    def _failure_audit(self, run_id, reason, job_id=None):
        """Best-effort stable diagnostics; never persists an exception message."""
        if not enabled():
            return
        try:
            with self.drafts._connection(True) as db:
                source = self._request(db, run_id)
                if source is not None:
                    operation = "auto-failure-" + reason
                    if db.execute("SELECT 1 FROM skill_draft_requests WHERE actor_id=? AND operation=? AND idempotency_key=?",
                                  (ACTOR, operation, run_id)).fetchone() is None:
                        db.execute("INSERT INTO skill_draft_requests VALUES(?,?,?,?,?,?,?)",
                                   (ACTOR, operation, run_id, digest(canonical([reason, source["request_sha256"]])),
                                    source["draft_id"], source["version"], time.time_ns() // 1_000_000))
                if job_id is not None:
                    event = db.execute("SELECT * FROM skill_validation_outbox WHERE job_id=? AND event_type=?", (job_id, EVENT)).fetchone()
                    if event is not None:
                        payload = json.loads(event["payload_json"])
                        payload["reason_code"] = reason
                        db.execute("UPDATE skill_validation_outbox SET state='blocked',payload_json=?,revision=revision+1,updated_ms=? WHERE event_id=?",
                                   (canonical(payload), time.time_ns() // 1_000_000, event["event_id"]))
        except (ValueError, TypeError, KeyError, OSError, sqlite3.Error, TimeoutError):
            pass

    def counts(self):
        if not enabled():
            return None
        with self.drafts._connection() as db:
            prefix = " FROM skill_validation_jobs j JOIN skill_drafts d ON d.draft_id=j.draft_id WHERE d.origin='automatic' AND d.actor_id=?"
            candidates = db.execute("SELECT count(*) FROM skill_drafts WHERE origin='automatic' AND actor_id=?", (ACTOR,)).fetchone()[0]
            validated = db.execute("SELECT count(*)" + prefix + " AND j.state IN ('validated','admitted')", (ACTOR,)).fetchone()[0]
            admitted = db.execute("SELECT count(*)" + prefix + " AND j.state='admitted'", (ACTOR,)).fetchone()[0]
            reused = db.execute("""SELECT count(DISTINCT c.run_id) FROM skill_local_reference_completions c
                JOIN skill_local_reference_dispatches r ON r.run_id=c.run_id
                JOIN skill_validation_admissions a ON a.skill_id=r.skill_id AND a.skill_version=r.version
                JOIN skill_validation_jobs j ON j.job_id=a.job_id JOIN skill_drafts d ON d.draft_id=j.draft_id
                WHERE c.outcome='verified' AND d.origin='automatic' AND d.actor_id=?""", (ACTOR,)).fetchone()[0]
        return dict(candidates=candidates, validated=validated, admitted=admitted, reused=reused)

    def _summary(self, reason, **extra):
        return dict(observed=False, queued=False, admitted=False, reason=reason, **extra)

    def observe(self, run_id):
        if not enabled():
            return self._summary("learning_disabled")
        try:
            source, prepared, controlled, case = self._source(run_id)
            with self.drafts._connection() as db:
                self._profile(db, case)
                self._lifecycle(db, controlled["case_fingerprint"])
            draft_id, duplicate = self._new_source_draft(run_id, source, prepared)
            jobs = GeneralValidationJobs(self.store, self.library.runtime_sha256, case, self.library)
            with self.drafts._connection() as db:
                old_job = self._job_for_draft(db, draft_id, run_id)
            if old_job is None:
                head = self.drafts.get(draft_id, actor_id=ACTOR)
                if head["state"] != "editing":
                    with self.drafts._connection() as db:
                        old_job = self._job_for_draft(db, draft_id, run_id)
                    if old_job is None:
                        raise DraftError("automatic_draft_not_editable")
                if old_job is None and head["spec"] != controlled["spec"]:
                    try:
                        self.library.save_controlled_draft(draft_id, actor_id=ACTOR, expected_revision=head["revision"], apply=True)
                    except DraftError as error:
                        if error.code != "revision_conflict":
                            raise
                    head = self.drafts.get(draft_id, actor_id=ACTOR)
                # Another observer may have completed enqueue after this read.
                if old_job is None:
                    with self.drafts._connection() as db:
                        old_job = self._job_for_draft(db, draft_id, run_id)
                if old_job is None:
                    try:
                        job = jobs.enqueue(draft_id, actor_id=ACTOR, expected_revision=head["revision"], idempotency_key=run_id)
                    except DraftError as error:
                        with self.drafts._connection() as db:
                            concurrent = self._job_for_draft(db, draft_id, run_id)
                        if concurrent is None:
                            raise error
                        job = jobs.worker_get(concurrent["job_id"])
                else:
                    job = jobs.worker_get(old_job["job_id"])
            else:
                job = jobs.worker_get(old_job["job_id"])
            self._event(job, run_id, source["source_binding_sha256"], case,
                        state="done" if job["state"] in TERMINAL else "pending")
            return dict(observed=True, queued=job["state"] == "queued", admitted=job["state"] == "admitted",
                        reason="automatic_source_replayed" if duplicate else "automatic_validation_queued",
                        source_run_id=run_id, draft_id=draft_id, job_id=job["job_id"], state=job["state"], counters=self.counts())
        except (DraftError, ValueError, TypeError, KeyError, OSError, sqlite3.Error, TimeoutError) as error:
            reason = self._reason(error)
            self._failure_audit(run_id, reason)
            return self._summary(reason)

    def _unfinished_source(self):
        with self.drafts._connection() as db:
            row = db.execute("""SELECT r.idempotency_key FROM skill_draft_requests r
                JOIN skill_drafts d ON d.draft_id=r.draft_id
                WHERE r.actor_id=? AND r.operation=? AND d.origin='automatic' AND d.actor_id=?
                AND d.state='editing' AND NOT EXISTS(SELECT 1 FROM skill_validation_jobs j WHERE j.draft_id=d.draft_id)
                ORDER BY r.created_ms,r.draft_id LIMIT 1""", (ACTOR, OPERATION, ACTOR)).fetchone()
            return row[0] if row else None

    def _next(self):
        with self.drafts._connection() as db:
            # Validation jobs own execution concurrency; the outbox is a progress
            # journal, never a second authority or a substitute execution lease.
            row = db.execute("""SELECT j.*,r.idempotency_key source_run_id,r.request_sha256 source_binding_sha256
                FROM skill_validation_jobs j JOIN skill_drafts d ON d.draft_id=j.draft_id
                JOIN skill_draft_requests r ON r.draft_id=d.draft_id AND r.actor_id=? AND r.operation=?
                WHERE d.origin='automatic' AND d.actor_id=? AND
                (j.state IN ('validated','needs_reconciliation','cancel_requested','running','queued')
                 OR EXISTS(SELECT 1 FROM skill_validation_outbox o WHERE o.job_id=j.job_id AND o.event_type='automatic_skill_learning.v1' AND o.state!='done'))
                ORDER BY CASE j.state WHEN 'validated' THEN 0 WHEN 'needs_reconciliation' THEN 1
                WHEN 'cancel_requested' THEN 2 WHEN 'running' THEN 3 ELSE 4 END,j.created_ms,j.job_id LIMIT 1""",
                             (ACTOR, OPERATION, ACTOR)).fetchone()
            return dict(row) if row else None

    def tick(self, owner):
        if not enabled():
            return self._summary("learning_disabled")
        row = None
        try:
            if self.source_verifier is None:
                from app.skill_auto_source import tick_source
                source_progress = tick_source(self.library, owner)
                if source_progress is not None:
                    return source_progress
            row = self._next()
            if row is None:
                unfinished = self._unfinished_source()
                if unfinished is not None:
                    return self.observe(unfinished)
                return self._summary("no_automatic_job", counters=self.counts())
            if row["state"] in TERMINAL:
                with self.drafts._connection() as db:
                    terminal = ValidationJobs._view(db, ValidationJobs._row(db, row["job_id"]))
                case = profiles.match_profile(terminal["spec"])
                self._event(terminal, row["source_run_id"], row["source_binding_sha256"], case, state="done", owner=owner)
                return dict(observed=True, queued=False, admitted=terminal["state"] == "admitted", reason="automatic_job_terminal",
                            job_id=terminal["job_id"], state=terminal["state"], counters=self.counts())
            source, _, controlled, case = self._source(row["source_run_id"])
            if source["source_binding_sha256"] != row["source_binding_sha256"]:
                raise DraftError("automatic_source_changed")
            jobs = _SelectedJobs(self.store, self.library.runtime_sha256, case, self.library, row["job_id"])
            job = jobs.worker_get(row["job_id"])
            with self.drafts._connection() as db:
                self._lifecycle(db, controlled["case_fingerprint"])
                self._profile(db, case)
                head = self.drafts._head(db, job["draft_id"], ACTOR)
                if head["state"] == "archived" or head["current_version"] != job["draft_version"]:
                    raise DraftError("automatic_draft_changed")
                if job["state"] == "queued":
                    first = db.execute("SELECT job_id FROM skill_validation_jobs WHERE state='queued' ORDER BY created_ms,job_id LIMIT 1").fetchone()
                    if first is not None and first[0] != job["job_id"]:
                        return self._summary("global_queue_precedes_automatic", job_id=job["job_id"], state=job["state"])
            self._event(job, row["source_run_id"], row["source_binding_sha256"], case, state="processing", owner=owner)
            worker = GeneralValidationWorker(jobs, self.library.assets, self.library)
            jobs.recover_expired()
            job = jobs.worker_get(job["job_id"])
            if job["state"] == "queued":
                result = worker.run_once(owner)
                if result is not None:
                    job = result
            elif job["state"] == "needs_reconciliation":
                job = worker.reconcile(job["job_id"])
            admitted = False
            if job["state"] == "validated" and enabled():
                current_source, _, _, _ = self._source(row["source_run_id"])
                if current_source["source_binding_sha256"] != row["source_binding_sha256"]:
                    raise DraftError("automatic_source_changed")
                result = self.library.admit(job["job_id"], actor_id=ACTOR, expected_revision=job["revision"], _automatic=True)
                admitted = result["admitted"]
                job = jobs.worker_get(job["job_id"])
            if enabled():
                self._event(job, row["source_run_id"], row["source_binding_sha256"], case,
                            state="done" if job["state"] in TERMINAL else "pending")
            return dict(observed=True, queued=job["state"] == "queued", admitted=admitted,
                        reason="automatic_admission_complete" if admitted else "automatic_job_pending" if job["state"] not in TERMINAL else "automatic_job_terminal",
                        source_run_id=row["source_run_id"], job_id=job["job_id"], state=job["state"], counters=self.counts())
        except (DraftError, ValueError, TypeError, KeyError, OSError, sqlite3.Error, TimeoutError) as error:
            reason = self._reason(error)
            if row is not None:
                self._failure_audit(row["source_run_id"], reason, row["job_id"])
            return self._summary(reason)
