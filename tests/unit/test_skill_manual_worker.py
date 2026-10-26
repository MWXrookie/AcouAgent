"""Manual dispatch obeys queue ownership and never requires automatic learning."""
import copy
import time
from types import SimpleNamespace

import pytest

from app import config, skill_manual_worker as pump
from app.skill_drafts import DraftStore
from app.skill_validation_jobs import ValidationJobs, canonical
from tests.unit.test_skill_general_library import env


def enqueue(env, actor=pump.ACTOR, key="manual"):
    draft = DraftStore(env["store"]).create(canonical(env["spec"]), actor_id=actor, idempotency_key=key)
    jobs = ValidationJobs(env["store"], env["library"].runtime_sha256)
    jobs.install_trial_profile()
    return jobs, jobs.enqueue(draft["draft_id"], actor_id=actor, expected_revision=draft["revision"], idempotency_key=key)


def test_learning_off_manual_trial_runs_without_admission(env, monkeypatch):
    monkeypatch.setattr(config, "SKILL_LEARNING_ENABLED", False)
    monkeypatch.setattr(config, "SKILL_GENERAL_ADMISSION_ENABLED", False)
    jobs, job = enqueue(env)
    result = pump.tick(env["library"], "manual")
    assert result["state"] == "rejected" and result["reason_code"] == "trial_completed_ungraded"
    assert len(env["calls"]) == 1
    with env["store"]._read_db() as db:
        assert db.execute("SELECT count(*) FROM skill_validation_admissions").fetchone()[0] == 0


def test_other_actor_head_not_skipped(env):
    _, old = enqueue(env, actor="other", key="old")
    _, new = enqueue(env, key="new")
    assert pump.tick(env["library"], "manual") is None
    assert not env["calls"]


def test_general_validation_not_auto_admitted(env, monkeypatch):
    from app.skill_general_validation import GeneralValidationJobs
    monkeypatch.setattr(config, "SKILL_LEARNING_ENABLED", False)
    draft = DraftStore(env["store"]).create(canonical(env["spec"]), actor_id=pump.ACTOR, idempotency_key="draft")
    draft = env["library"].save_controlled_draft(draft["draft_id"], actor_id=pump.ACTOR,
        expected_revision=draft["revision"], apply=True)["draft"]
    jobs = GeneralValidationJobs(env["store"], env["library"].runtime_sha256, "B01", env["library"])
    job = jobs.enqueue(draft["draft_id"], actor_id=pump.ACTOR, expected_revision=draft["revision"], idempotency_key="validation")
    monkeypatch.setattr(config, "SKILL_GENERAL_ADMISSION_ENABLED", False)
    assert pump.tick(env["library"], "manual") is None
    monkeypatch.setattr(config, "SKILL_GENERAL_ADMISSION_ENABLED", True)
    assert pump.tick(env["library"], "manual")["state"] == "validated"
    assert pump.tick(env["library"], "manual") is None
    with env["store"]._read_db() as db:
        assert db.execute("SELECT count(*) FROM skill_validation_admissions").fetchone()[0] == 0


def test_unknown_retains_slot_and_reconciliation_never_redispatches(env):
    jobs, row = enqueue(env)
    row = jobs.claim("crashed")
    row = jobs.mark_dispatching(row["job_id"], owner="crashed", fence=row["fence"], expected_revision=row["revision"])
    jobs.mark_unknown(row["job_id"], owner="crashed", fence=row["fence"], expected_revision=row["revision"], reason="dispatch_or_binding_unknown")
    enqueue(env, key="next")
    assert pump.tick(env["library"], "manual")["state"] == "needs_reconciliation"
    assert pump.tick(env["library"], "manual")["state"] == "needs_reconciliation"
    assert not env["calls"]


def test_flags_disable_claim_even_after_worker_created(env, monkeypatch):
    jobs, row = enqueue(env)
    scoped = pump.ManualTrialJobs(env["store"], env["library"].runtime_sha256)
    monkeypatch.setattr(config, "SKILL_VALIDATION_ENABLED", False)
    assert scoped.claim("manual") is None
    assert pump.tick(env["library"], "manual") is None
    assert not env["calls"]


def test_disabled_start_does_not_create_or_resolve(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SKILL_AUTHORING_ENABLED", False)
    monkeypatch.setattr(pump, "get_library", lambda: pytest.fail("must not resolve"))
    assert pump.start_worker() is None
    assert list(tmp_path.iterdir()) == []


def test_missing_store_start_no_migration_and_logs_sanitized(tmp_path, monkeypatch, caplog):
    from app.skill_store import SkillStore
    for flag in ("SKILL_AUTHORING_ENABLED", "SKILL_VALIDATION_ENABLED"):
        monkeypatch.setattr(config, flag, True)
    store = SkillStore(tmp_path / "missing.db")
    monkeypatch.setattr(pump, "get_library", lambda: SimpleNamespace(store=store))
    monkeypatch.setattr(pump, "_WORKER", None)
    assert pump.start_worker() is None
    assert not store.path.exists()
    assert "manual_validation_worker_not_ready" in caplog.text


def test_singleton_no_startup_writes_and_bounded_stop(env, monkeypatch):
    monkeypatch.setattr(pump, "_WORKER", None)
    monkeypatch.setattr(pump, "get_library", lambda: env["library"])
    before = env["store"].path.read_bytes()
    worker = pump.start_worker()
    try:
        assert worker and worker.is_alive()
        assert pump.start_worker() is worker
        assert env["store"].path.read_bytes() == before
    finally:
        assert worker.stop()


@pytest.mark.parametrize("interval", [0, -1, True, float("nan"), 61])
def test_worker_interval_bounded(interval):
    with pytest.raises(ValueError):
        pump.ManualWorker(None, interval=interval)
