"""Automatic orchestration with real proof machinery and injected source authority.

Synthetic reference arrays test bookkeeping/failure boundaries, not acoustics.
The independent server source enrollment/commit guard is tested separately.
"""
import copy
import json
import sqlite3
import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app import config, execution, skill_auto_source
from app.skill_auto_learning import ACTOR, AutoLearning
from app.skill_drafts import DraftError, DraftStore
from app.skill_general_profiles import build_controlled_spec
from app.skill_general_validation import GeneralValidationJobs
from app.skill_spec import prepare_skill_spec
from app.skill_store import SkillStore
from app.skill_validation_jobs import canonical, digest
from tests.unit.test_skill_general_library import env as general_env, delivered


@pytest.fixture
def env(general_env, monkeypatch):
    value = general_env
    monkeypatch.setattr(config, "SKILL_LEARNING_ENABLED", True)
    source_run = str(uuid.uuid4())
    source_spec = build_controlled_spec(canonical(value["spec"]))["spec"]
    source_prepared = prepare_skill_spec(canonical(source_spec))
    source = dict(source_run_id=source_run, spec=source_spec, prepared=source_prepared,
                  source_binding_sha256=digest("server-source-" + source_run))
    sources = {source_run: source}
    verifier = Mock(side_effect=lambda run: copy.deepcopy(sources[run]))
    source_state = {"available": True}
    def source_guard(db, run):
        # Replace only this fixture's separately-owned source authority. The
        # admission code still derives automatic origin from persistent rows.
        recorded = db.execute("SELECT request_sha256 FROM skill_draft_requests WHERE actor_id=? AND operation='auto-source' AND idempotency_key=?", (ACTOR, run)).fetchone()
        if (not source_state["available"] or run not in sources or recorded is None
                or recorded[0] != sources[run]["source_binding_sha256"]):
            raise DraftError("fixture_source_unavailable")
        return True
    monkeypatch.setattr(skill_auto_source, "require_source_connection", source_guard)
    learning = AutoLearning(value["library"], verifier)
    original_admit = value["library"].admit
    admissions = []
    # Component isolation: the real source module has its own registry tests.
    # All independent comparison and skill lifecycle gates remain real here.
    def admit(job_id, *, actor_id, expected_revision, _automatic=False):
        assert _automatic is True
        admissions.append(job_id)
        return original_admit(job_id, actor_id=actor_id, expected_revision=expected_revision)
    monkeypatch.setattr(value["library"], "admit", admit)
    value.update(learning=learning, source_run=source_run, sources=sources, verifier=verifier, admissions=admissions,
                 source_state=source_state, real_admit=original_admit)
    return value


def rows(env, table):
    with sqlite3.connect(env["store"].path) as db:
        return db.execute("SELECT count(*) FROM " + table).fetchone()[0]


def observed(env):
    result = env["learning"].observe(env["source_run"])
    assert result["observed"] and result["queued"], result
    return result


def test_disabled_learning_neither_verifies_source_nor_creates_database(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SKILL_LEARNING_ENABLED", False)
    source = Mock(side_effect=AssertionError("must not read source"))
    library = SimpleNamespace(store=SkillStore(tmp_path / "missing.db"))
    scheduler = AutoLearning(library, source)
    assert scheduler.observe(str(uuid.uuid4()))["reason"] == "learning_disabled"
    assert scheduler.tick("worker")["reason"] == "learning_disabled"
    assert scheduler.counts() is None
    assert not library.store.path.exists()
    source.assert_not_called()


@pytest.mark.parametrize("flag", ["SKILL_LEARNING_ENABLED", "SKILL_AUTHORING_ENABLED", "SKILL_VALIDATION_ENABLED", "SKILL_GENERAL_ADMISSION_ENABLED"])
def test_each_required_flag_blocks_observation_and_scheduling(env, monkeypatch, flag):
    before = rows(env, "skill_drafts")
    monkeypatch.setattr(config, flag, False)
    assert env["learning"].observe(env["source_run"])["reason"] == "learning_disabled"
    assert env["learning"].tick("worker")["reason"] == "learning_disabled"
    env["verifier"].assert_not_called()
    assert rows(env, "skill_drafts") == before
    assert not env["calls"]


def test_automatic_origin_source_binding_and_replay_are_durable(env):
    first = observed(env)
    second = env["learning"].observe(env["source_run"])
    assert second["observed"] and second["job_id"] == first["job_id"]
    assert second["reason"] == "automatic_source_replayed"
    assert rows(env, "skill_drafts") == rows(env, "skill_draft_versions") == rows(env, "skill_validation_jobs") == 1
    assert rows(env, "skill_validation_outbox") == 1
    with env["store"]._read_db() as db:
        head = db.execute("SELECT * FROM skill_drafts").fetchone()
        version = db.execute("SELECT * FROM skill_draft_versions").fetchone()
        request = db.execute("SELECT * FROM skill_draft_requests WHERE operation='auto-source'").fetchone()
        assert head["origin"] == "automatic" and head["actor_id"] == ACTOR
        assert version["source_run_id"] == env["source_run"]
        assert request["request_sha256"] == env["sources"][env["source_run"]]["source_binding_sha256"]
    assert env["learning"].counts() == dict(candidates=1, validated=0, admitted=0, reused=0)
    assert rows(env, "simulation_skills") == 0


def test_source_change_cannot_rewrite_an_automatic_draft(env):
    first = observed(env)
    env["sources"][env["source_run"]]["source_binding_sha256"] = "0" * 64
    denied = env["learning"].observe(env["source_run"])
    assert not denied["observed"] and denied["reason"] == "automatic_source_changed"
    assert rows(env, "skill_drafts") == rows(env, "skill_validation_jobs") == 1
    assert GeneralValidationJobs(env["store"], env["library"].runtime_sha256, "B01", env["library"]).worker_get(first["job_id"])["state"] == "queued"


def test_unavailable_source_errors_are_redacted_and_write_nothing(env):
    env["verifier"].side_effect = ValueError("password=private-value; secret path")
    result = env["learning"].observe(env["source_run"])
    assert result["reason"] == "automatic_source_unavailable"
    assert "private-value" not in json.dumps(result)
    assert rows(env, "skill_drafts") == 0


def test_enqueue_failure_resumes_same_immutable_source_draft(env, monkeypatch):
    original = GeneralValidationJobs.enqueue
    attempts = []
    def enqueue(self, *args, **kwargs):
        attempts.append(True)
        if len(attempts) == 1:
            raise DraftError("queue_full")
        return original(self, *args, **kwargs)
    monkeypatch.setattr(GeneralValidationJobs, "enqueue", enqueue)
    assert not env["learning"].observe(env["source_run"])["observed"]
    assert rows(env, "skill_drafts") == 1 and rows(env, "skill_validation_jobs") == 0
    assert observed(env)["observed"]
    assert rows(env, "skill_drafts") == rows(env, "skill_validation_jobs") == 1


def test_restart_resumes_source_draft_left_before_enqueue(env, monkeypatch):
    original = GeneralValidationJobs.enqueue
    monkeypatch.setattr(GeneralValidationJobs, "enqueue", Mock(side_effect=DraftError("queue_full")))
    assert not env["learning"].observe(env["source_run"])["observed"]
    with env["store"]._read_db() as db:
        assert db.execute("SELECT count(*) FROM skill_draft_requests WHERE operation='auto-failure-queue_full'").fetchone()[0] == 1
    monkeypatch.setattr(GeneralValidationJobs, "enqueue", original)
    restarted = AutoLearning(env["library"], env["verifier"])
    progress = restarted.tick("restarted")
    assert progress["queued"]
    assert restarted.tick("restarted")["admitted"]
    assert rows(env, "skill_drafts") == rows(env, "skill_validation_jobs") == 1
    assert len(env["calls"]) == 1


def test_outbox_failure_after_enqueue_resumes_without_another_job(env, monkeypatch):
    original = env["learning"]._event
    calls = []
    def event(*args, **kwargs):
        calls.append(True)
        if len(calls) == 1:
            raise DraftError("draft_store_unavailable")
        return original(*args, **kwargs)
    monkeypatch.setattr(env["learning"], "_event", event)
    assert not env["learning"].observe(env["source_run"])["observed"]
    assert rows(env, "skill_validation_jobs") == 1 and rows(env, "skill_validation_outbox") == 0
    observed(env)
    assert rows(env, "skill_drafts") == rows(env, "skill_validation_jobs") == rows(env, "skill_validation_outbox") == 1


def test_tick_validates_and_admits_without_per_skill_approval(env):
    first = observed(env)
    result = env["learning"].tick("auto-worker")
    assert result["admitted"] and result["state"] == "admitted", result
    assert env["admissions"] == [first["job_id"]]
    assert len(env["calls"]) == 1
    assert env["learning"].counts() == dict(candidates=1, validated=1, admitted=1, reused=0)
    replay = env["learning"].observe(env["source_run"])
    assert replay["admitted"] and replay["job_id"] == first["job_id"]
    assert env["learning"].tick("another")["reason"] == "no_automatic_job"
    assert len(env["calls"]) == 1
    assert rows(env, "simulation_skills") == rows(env, "skill_versions") == rows(env, "skill_validation_admissions") == 1


def test_admission_crash_is_repaired_without_reexecuting_the_solver(env, monkeypatch):
    observed(env)
    original = env["library"].admit
    count = []
    def admit(*args, **kwargs):
        count.append(True)
        if len(count) == 1:
            raise OSError("disk temporarily unavailable")
        return original(*args, **kwargs)
    monkeypatch.setattr(env["library"], "admit", admit)
    assert not env["learning"].tick("worker")["admitted"]
    assert env["learning"].counts() == dict(candidates=1, validated=1, admitted=0, reused=0)
    assert env["learning"].tick("restarted")["admitted"]
    assert len(env["calls"]) == 1
    assert rows(env, "skill_validation_admissions") == 1


def test_learning_toggle_during_admission_rolls_back_every_trust_write(env, monkeypatch):
    first = observed(env)
    original = env["store"].create_candidate
    def disable_after_candidate(**kwargs):
        skill_id = original(**kwargs)
        monkeypatch.setattr(config, "SKILL_LEARNING_ENABLED", False)
        return skill_id
    monkeypatch.setattr(env["store"], "create_candidate", disable_after_candidate)
    result = env["learning"].tick("worker")
    assert not result["admitted"]
    assert rows(env, "simulation_skills") == rows(env, "skill_versions") == rows(env, "skill_validation_admissions") == 0
    assert env["jobs"].worker_get(first["job_id"])["state"] == "validated"
    assert len(env["calls"]) == 1
    monkeypatch.setattr(config, "SKILL_LEARNING_ENABLED", True)
    monkeypatch.setattr(env["store"], "create_candidate", original)
    assert env["learning"].tick("restarted")["admitted"]
    assert len(env["calls"]) == 1


def test_default_admission_flag_cannot_bypass_persisted_automatic_source_guard(env, monkeypatch):
    first = observed(env)
    monkeypatch.setattr(env["library"], "admit", Mock(side_effect=OSError("interrupt before admission")))
    assert not env["learning"].tick("worker")["admitted"]
    job = env["jobs"].worker_get(first["job_id"])
    assert job["state"] == "validated"
    env["source_state"]["available"] = False
    with pytest.raises(DraftError, match="fixture_source_unavailable"):
        # No _automatic argument: persisted origin still requires the source.
        env["real_admit"](job["job_id"], actor_id=ACTOR, expected_revision=job["revision"])
    assert rows(env, "simulation_skills") == rows(env, "skill_validation_admissions") == 0
    env["source_state"]["available"] = True
    monkeypatch.setattr(config, "SKILL_LEARNING_ENABLED", False)
    with pytest.raises(ValueError, match="learning_disabled"):
        env["real_admit"](job["job_id"], actor_id=ACTOR, expected_revision=job["revision"])
    assert rows(env, "simulation_skills") == rows(env, "skill_validation_admissions") == 0


def test_unknown_dispatch_keeps_the_slot_and_is_never_replayed(env, monkeypatch):
    observed(env)
    failed = Mock(side_effect=TimeoutError("transport lost"))
    monkeypatch.setattr(execution, "_execute_code", failed)
    first = env["learning"].tick("worker")
    assert first["state"] == "needs_reconciliation", first
    second = env["learning"].tick("restarted")
    assert second["state"] == "needs_reconciliation", second
    failed.assert_called_once()
    assert rows(env, "skill_validation_attempts") == 1
    assert rows(env, "skill_validation_admissions") == 0


def test_cancelled_queue_job_is_never_executed_and_outbox_is_closed(env):
    first = observed(env)
    jobs = env["jobs"]
    job = jobs.worker_get(first["job_id"])
    jobs.cancel(job["job_id"], actor_id=ACTOR, expected_revision=job["revision"])
    result = env["learning"].tick("worker")
    assert result["state"] == "cancelled" and not result["admitted"]
    assert not env["calls"]
    with env["store"]._read_db() as db:
        assert db.execute("SELECT state FROM skill_validation_outbox").fetchone()[0] == "done"


def test_actual_reuse_is_counted_separately_from_admission(env):
    from app.skill_general_library import execute_reference
    observed(env)
    assert env["learning"].tick("worker")["admitted"]
    assert env["learning"].counts()["reused"] == 0
    outcome = execute_reference(**delivered(env))
    assert outcome["execution_reuse_verified"]
    assert env["learning"].counts()["reused"] == 1
    assert len(env["calls"]) == 2


def test_archive_blocks_relearning_instead_of_restoring_the_skill(env):
    observed(env)
    assert env["learning"].tick("worker")["admitted"]
    with env["store"]._read_db() as db:
        row = db.execute("SELECT skill_id,updated_at FROM simulation_skills").fetchone()
    env["store"].set_archived(row["skill_id"], archived=True, expected_updated_at=row["updated_at"], actor="internal-system", reason="withdraw")
    result = env["learning"].observe(env["source_run"])
    assert not result["observed"] and result["reason"] == "withdrawn_skill_cannot_be_relearned"
    assert len(env["calls"]) == 1


def test_profile_retirement_blocks_execution_with_stable_persistent_reason(env):
    observed(env)
    with env["store"]._db_existing_write() as db:
        db.execute("UPDATE skill_validation_profiles SET state='retired'")
    result = env["learning"].tick("worker")
    assert result["reason"] == "automatic_profile_unavailable"
    assert not env["calls"]
    with env["store"]._read_db() as db:
        event = db.execute("SELECT state,payload_json FROM skill_validation_outbox").fetchone()
        assert event["state"] == "blocked"
        assert json.loads(event["payload_json"])["reason_code"] == "automatic_profile_unavailable"
        assert db.execute("SELECT count(*) FROM skill_draft_requests WHERE operation='auto-failure-automatic_profile_unavailable'").fetchone()[0] == 1


def test_parallel_observation_creates_one_source_draft_and_one_job(env):
    barrier = Barrier(2)
    def submit(_):
        barrier.wait()
        return env["learning"].observe(env["source_run"])
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(submit, range(2)))
    assert all(result["observed"] for result in results), results
    assert len({r["job_id"] for r in results}) == 1
    assert rows(env, "skill_drafts") == rows(env, "skill_validation_jobs") == rows(env, "skill_validation_outbox") == 1
