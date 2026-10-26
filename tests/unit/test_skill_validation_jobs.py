"""Durable validation ownership and recovery contracts, using disposable databases."""
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from threading import Barrier

import pytest

from app import config
from app.skill_drafts import DraftStore
from app.skill_store import SkillStore
from app.skill_validation_jobs import ValidationJobs


def body(name="trial"):
    path = Path(__file__).resolve().parents[2] / "docs/skills-next/examples/manual-skill.json"
    spec = json.loads(path.read_text(encoding="utf-8"))
    spec["name"] = name
    return json.dumps(spec)


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SKILL_AUTHORING_ENABLED", True)
    monkeypatch.setattr(config, "SKILL_VALIDATION_ENABLED", True)
    clock = [1_000_000]
    monkeypatch.setattr("app.skill_validation_jobs._now", lambda: clock[0])
    store = SkillStore(tmp_path / "jobs.db")
    store.migrate()
    store.migrate_skill_authoring()
    drafts = DraftStore(store)
    jobs = ValidationJobs(store, "a" * 64)
    jobs.install_trial_profile()
    return jobs, drafts, store.path, clock


def enqueue(env, key="job", actor="alice"):
    jobs, drafts, _, _ = env
    draft = drafts.create(body(key), actor_id=actor, idempotency_key="draft-" + key)
    return jobs.enqueue(draft["draft_id"], actor_id=actor,
                        expected_revision=draft["revision"], idempotency_key=key)


def lease(job, owner="worker"):
    return dict(owner=owner, fence=job["fence"], expected_revision=job["revision"])


def count(path, table):
    with closing(sqlite3.connect(path)) as db:
        return db.execute("SELECT count(*) FROM " + table).fetchone()[0]


def test_disabled_does_not_create_database(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SKILL_AUTHORING_ENABLED", False)
    path = tmp_path / "absent.db"
    jobs = ValidationJobs(SkillStore(path), "a" * 64)
    with pytest.raises(ValueError):
        jobs.install_trial_profile()
    assert not path.exists()


def test_jobs_do_not_silently_migrate_base17(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SKILL_AUTHORING_ENABLED", True)
    monkeypatch.setattr(config, "SKILL_VALIDATION_ENABLED", True)
    store = SkillStore(tmp_path / "old.db")
    store.migrate()
    with pytest.raises(ValueError):
        ValidationJobs(store, "a" * 64).install_trial_profile()
    with closing(sqlite3.connect(store.path)) as db:
        assert db.execute("SELECT max(version) FROM schema_migrations").fetchone()[0] == 17


def test_submit_freezes_draft_and_replays_original_job(env):
    jobs, drafts, path, _ = env
    draft = drafts.create(body(), actor_id="alice", idempotency_key="draft")
    args = dict(actor_id="alice", expected_revision=draft["revision"], idempotency_key="submit")
    first = jobs.enqueue(draft["draft_id"], **args)
    replay = jobs.enqueue(draft["draft_id"], **args)
    assert replay["job_id"] == first["job_id"]
    assert count(path, "skill_validation_jobs") == 1
    head = drafts.get(draft["draft_id"], actor_id="alice")
    assert head["state"] == "submitted"
    with pytest.raises(ValueError):
        drafts.update(draft["draft_id"], body("changed"), actor_id="alice", expected_revision=head["revision"])


def test_actor_read_and_submit_isolation(env):
    jobs, _, _, _ = env
    first = enqueue(env)
    with pytest.raises(ValueError):
        jobs.get(first["job_id"], actor_id="bob")
    assert jobs.get(first["job_id"], actor_id="alice")["job_id"] == first["job_id"]


def test_validate_intent_cannot_grant_trust(env):
    jobs, drafts, path, _ = env
    draft = drafts.create(body(), actor_id="alice", idempotency_key="draft")
    with pytest.raises(ValueError):
        jobs.enqueue(draft["draft_id"], actor_id="alice", expected_revision=draft["revision"], idempotency_key="validate", intent="validate")
    assert count(path, "skill_validation_jobs") == 0
    assert drafts.get(draft["draft_id"], actor_id="alice")["state"] == "editing"


def test_claim_freezes_payload_and_only_one_parallel_worker_wins(env):
    jobs, _, path, clock = env
    first = enqueue(env)
    clock[0] += 1
    enqueue(env, "second")
    barrier = Barrier(2)
    def claim(owner):
        barrier.wait()
        return jobs.claim(owner)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(claim, ("worker-a", "worker-b")))
    winners = [r for r in results if r is not None]
    assert len(winners) == 1
    job = winners[0]
    assert job["job_id"] == first["job_id"]
    assert job["state"] == "running"
    assert job["attempt"]["dispatch_state"] == "prepared"
    assert job["attempt"]["runtime_sha256"] == "a" * 64
    assert len(job["attempt"]["request_nonce"]) >= 32
    assert job["spec"]["name"] == "job"
    assert job["content_sha256"] and job["case_fingerprint"]
    assert count(path, "skill_validation_attempts") == 1


@pytest.mark.parametrize("bad", ["owner", "fence", "revision"])
def test_heartbeat_rejects_stale_ownership(env, bad):
    jobs, _, _, _ = env
    enqueue(env)
    job = jobs.claim("worker")
    args = lease(job)
    args[{"owner": "owner", "fence": "fence", "revision": "expected_revision"}[bad]] = "other" if bad == "owner" else 999
    with pytest.raises(ValueError):
        jobs.heartbeat(job["job_id"], **args)
    assert jobs.worker_get(job["job_id"])["revision"] == job["revision"]


def test_heartbeat_extends_lease_but_rejects_old_revision(env):
    jobs, _, _, clock = env
    enqueue(env)
    job = jobs.claim("worker")
    clock[0] += 5_000
    fresh = jobs.heartbeat(job["job_id"], **lease(job))
    assert fresh["revision"] > job["revision"]
    assert fresh["lease_until_ms"] > job["lease_until_ms"]
    with pytest.raises(ValueError):
        jobs.heartbeat(job["job_id"], **lease(job))


def test_expired_prepared_can_retry_with_new_attempt_and_nonce(env):
    jobs, _, path, clock = env
    enqueue(env)
    old = jobs.claim("worker")
    clock[0] += 120_001
    assert jobs.recover_expired() == 1
    new = jobs.claim("replacement")
    assert new["job_id"] == old["job_id"]
    assert new["fence"] > old["fence"]
    assert new["attempt"]["run_id"] != old["attempt"]["run_id"]
    assert new["attempt"]["request_nonce"] != old["attempt"]["request_nonce"]
    assert count(path, "skill_validation_attempts") == 2
    with pytest.raises(ValueError):
        jobs.mark_dispatching(old["job_id"], **lease(old))


def test_expired_dispatch_remains_unresolved_and_occupies_slot(env):
    jobs, _, _, clock = env
    first = enqueue(env)
    clock[0] += 1
    enqueue(env, "second")
    running = jobs.claim("worker")
    dispatched = jobs.mark_dispatching(running["job_id"], **lease(running))
    clock[0] += 120_001
    with pytest.raises(ValueError):
        jobs.finish(first["job_id"], **lease(dispatched), reason="stale completion")
    assert jobs.recover_expired() == 1
    pending = jobs.worker_get(first["job_id"])
    assert pending["state"] == "needs_reconciliation"
    assert jobs.claim("replacement") is None
    with pytest.raises(ValueError):
        jobs.reconcile(first["job_id"], expected_revision=pending["revision"], reason="no evidence")
    assert jobs.worker_get(first["job_id"])["state"] == "needs_reconciliation"
    assert jobs.claim("replacement") is None


def test_cancel_queued_never_creates_attempt(env):
    jobs, _, path, _ = env
    job = enqueue(env)
    cancelled = jobs.cancel(job["job_id"], actor_id="alice", expected_revision=job["revision"])
    assert cancelled["state"] == "cancelled"
    assert jobs.claim("worker") is None
    assert count(path, "skill_validation_attempts") == 0


def test_running_cancel_does_not_release_slot(env):
    jobs, _, _, _ = env
    enqueue(env)
    enqueue(env, "second")
    running = jobs.claim("worker")
    cancelled = jobs.cancel(running["job_id"], actor_id="alice", expected_revision=running["revision"])
    assert cancelled["state"] == "cancel_requested"
    assert jobs.claim("another") is None


def test_prepared_cannot_be_finished_as_if_it_executed(env):
    jobs, _, _, _ = env
    enqueue(env)
    running = jobs.claim("worker")
    with pytest.raises(ValueError):
        jobs.finish(running["job_id"], **lease(running), succeeded=True, manifest_sha256="b" * 64, receipt_sha256="c" * 64)


def test_successful_trial_never_creates_evidence_admission_or_active_skill(env):
    jobs, _, path, _ = env
    enqueue(env)
    running = jobs.claim("worker")
    dispatched = jobs.mark_dispatching(running["job_id"], **lease(running))
    # The worker has persisted a server-created, explicitly ungraded trial manifest.
    with closing(sqlite3.connect(path)) as db:
        db.execute("INSERT INTO skill_validation_assets VALUES (?,?,?,?,?,?,?,?)",
                   ("b" * 64, "trial/evidence.json", 2, "application/json", "{}",
                    "evidence", "unreviewed", 1_000_000))
        db.commit()
    done = jobs.finish(running["job_id"], **lease(dispatched), succeeded=True,
                       manifest_sha256="b" * 64, receipt_sha256="c" * 64)
    assert done["state"] == "rejected"
    assert done["reason_code"] == "trial_completed_ungraded"
    for table in ("skill_validation_evidence", "skill_validation_admissions", "simulation_skills", "skill_versions"):
        assert count(path, table) == 0
    assert jobs.claim("worker") is None


def test_job_total_budget_prevents_repeated_prepared_retries(env):
    jobs, _, _, clock = env
    first = enqueue(env)
    jobs.claim("worker")
    clock[0] += 180_001
    jobs.recover_expired()
    assert jobs.claim("another") is None
    assert jobs.worker_get(first["job_id"])["state"] == "failed"


def test_queue_bound_is_enforced_atomically(env):
    jobs, drafts, path, _ = env
    for n in range(100):
        enqueue(env, str(n))
    draft = drafts.create(body("overflow"), actor_id="alice", idempotency_key="overflow")
    with pytest.raises(ValueError):
        jobs.enqueue(draft["draft_id"], actor_id="alice", expected_revision=draft["revision"], idempotency_key="overflow")
    assert count(path, "skill_validation_jobs") == 100
    assert drafts.get(draft["draft_id"], actor_id="alice")["state"] == "editing"


def test_idempotency_key_cannot_redirect_to_different_draft(env):
    jobs, drafts, path, _ = env
    first = enqueue(env)
    draft = drafts.create(body("different"), actor_id="alice", idempotency_key="different")
    with pytest.raises(ValueError):
        jobs.enqueue(draft["draft_id"], actor_id="alice", expected_revision=draft["revision"], idempotency_key="job")
    assert count(path, "skill_validation_jobs") == 1
    assert jobs.get(first["job_id"], actor_id="alice")["draft_id"] != draft["draft_id"]
    assert drafts.get(draft["draft_id"], actor_id="alice")["state"] == "editing"


def test_unknown_dispatch_cannot_be_claimed_again(env):
    jobs, _, path, _ = env
    enqueue(env)
    running = jobs.claim("worker")
    dispatched = jobs.mark_dispatching(running["job_id"], **lease(running))
    unknown = jobs.mark_unknown(running["job_id"], **lease(dispatched), reason="transport disconnected")
    assert unknown["state"] == "needs_reconciliation"
    assert jobs.claim("replacement") is None
    assert count(path, "skill_validation_attempts") == 1


@pytest.mark.parametrize("runtime", ["", "x" * 64, "a" * 63, "a" * 65])
def test_runtime_binding_requires_exact_sha256(env, runtime):
    _, _, path, _ = env
    with pytest.raises(ValueError):
        ValidationJobs(SkillStore(path), runtime)



@pytest.mark.parametrize("flag", ["SKILL_AUTHORING_ENABLED", "SKILL_VALIDATION_ENABLED"])
def test_each_feature_flag_independently_blocks_jobs(env, monkeypatch, flag):
    jobs, _, path, _ = env
    monkeypatch.setattr(config, flag, False)
    with pytest.raises(ValueError):
        jobs.claim("worker")
    assert count(path, "skill_validation_attempts") == 0


def test_success_without_persisted_manifest_is_rejected(env):
    jobs, _, _, _ = env
    enqueue(env)
    running = jobs.claim("worker")
    dispatched = jobs.mark_dispatching(running["job_id"], **lease(running))
    with pytest.raises(ValueError):
        jobs.finish(running["job_id"], **lease(dispatched), succeeded=True,
                    manifest_sha256="b" * 64, receipt_sha256="c" * 64)
    assert jobs.worker_get(running["job_id"])["state"] == "running"


def test_trusted_failure_reconciliation_releases_slot_without_reexecution(env):
    jobs, _, path, clock = env
    first = enqueue(env)
    clock[0] += 1
    second = enqueue(env, "second")
    running = jobs.claim("worker")
    dispatched = jobs.mark_dispatching(first["job_id"], **lease(running))
    unknown = jobs.mark_unknown(first["job_id"], **lease(dispatched))
    with pytest.raises(ValueError):
        jobs.reconcile(first["job_id"], expected_revision=unknown["revision"] - 1,
                       receipt_sha256="c" * 64, reason="executor_failed")
    terminal = jobs.reconcile(first["job_id"], expected_revision=unknown["revision"],
                              receipt_sha256="c" * 64, reason="executor_failed")
    assert terminal["state"] == "failed"
    next_job = jobs.claim("next")
    assert next_job["job_id"] == second["job_id"]
    assert count(path, "skill_validation_attempts") == 2


def test_cancelled_prepared_cannot_be_dispatched(env):
    jobs, _, _, _ = env
    enqueue(env)
    running = jobs.claim("worker")
    cancelled = jobs.cancel(running["job_id"], actor_id="alice", expected_revision=running["revision"])
    with pytest.raises(ValueError):
        jobs.mark_dispatching(running["job_id"], **lease(cancelled))
    assert jobs.worker_get(running["job_id"])["state"] == "cancel_requested"


@pytest.mark.parametrize("dispatch", [False, True])
def test_worker_process_exit_preserves_prepared_or_unknown_boundary(env, dispatch):
    import os
    import subprocess
    import sys
    jobs, _, path, clock = env
    submitted = enqueue(env)
    script = """import os,sys
from app.skill_store import SkillStore
from app import skill_validation_jobs as m
m._now=lambda:1000000
jobs=m.ValidationJobs(SkillStore(sys.argv[1]),'a'*64)
job=jobs.claim('terminated-process')
if sys.argv[2]=='dispatch':
 jobs.mark_dispatching(job['job_id'],owner='terminated-process',fence=job['fence'],expected_revision=job['revision'])
os._exit(0)
"""
    child_env = dict(os.environ, SKILL_AUTHORING_ENABLED="true", SKILL_VALIDATION_ENABLED="true")
    completed = subprocess.run([sys.executable, "-c", script, str(path), "dispatch" if dispatch else "prepared"],
                               env=child_env, capture_output=True, timeout=20)
    assert completed.returncode == 0, completed.stderr.decode(errors="replace")
    old = jobs.worker_get(submitted["job_id"])
    assert old["attempt"]["dispatch_state"] == ("dispatching" if dispatch else "prepared")
    clock[0] += 121_000
    restarted = ValidationJobs(SkillStore(path), "a" * 64)
    assert restarted.recover_expired() == 1
    if dispatch:
        assert restarted.worker_get(submitted["job_id"])["state"] == "needs_reconciliation"
        assert restarted.claim("restarted-process") is None
    else:
        new = restarted.claim("restarted-process")
        assert new["attempt"]["attempt_no"] == 2
        assert new["attempt"]["request_nonce"] != old["attempt"]["request_nonce"]


def test_profile_retirement_blocks_new_dispatch_but_prepared_can_cancel_safely(env):
    jobs, _, path, clock = env
    enqueue(env)
    running = jobs.claim("worker")
    with closing(sqlite3.connect(path)) as db:
        db.execute("UPDATE skill_validation_profiles SET state='retired'")
        db.commit()
    with pytest.raises(ValueError):
        jobs.mark_dispatching(running["job_id"], **lease(running))
    current = jobs.worker_get(running["job_id"])
    assert current["attempt"]["dispatch_state"] == "prepared"
    cancelled = jobs.cancel(current["job_id"], actor_id="alice", expected_revision=current["revision"])
    clock[0] += 120_001
    assert jobs.recover_expired() == 1
    terminal = jobs.worker_get(cancelled["job_id"])
    assert terminal["state"] == "cancelled"
    assert terminal["attempt"]["dispatch_state"] == "terminal"


def test_cancel_after_dispatch_remains_cancelled_on_trusted_completion(env):
    jobs, _, _, _ = env
    enqueue(env)
    running = jobs.claim("worker")
    dispatched = jobs.mark_dispatching(running["job_id"], **lease(running))
    cancelled = jobs.cancel(running["job_id"], actor_id="alice", expected_revision=dispatched["revision"])
    terminal = jobs.finish(running["job_id"], **lease(cancelled), receipt_sha256="c" * 64,
                           succeeded=False, reason="executor_failed")
    assert terminal["state"] == "cancelled"
    assert terminal["reason_code"] == "cancelled_after_execution"
