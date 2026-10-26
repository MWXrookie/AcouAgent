"""Fresh explicit manual proof can restore a questioned exact skill; history stays."""
import copy
import time

import pytest

from app import skill_general_library as general, skill_retrieval
from app.skill_drafts import DraftStore
from app.skill_general_validation import GeneralValidationWorker
from app.skill_validation_jobs import canonical
from tests.unit.test_skill_general_library import env, admitted, delivered, query


def request(env, skill):
    head = env["store"].get_skill(skill)
    with env["store"]._read_db() as db:
        revision = db.execute("SELECT max(id) FROM skill_events WHERE skill_id=?", (skill,)).fetchone()[0]
    result = env["store"].request_revalidation(skill, expected_updated_at=head["updated_at"],
        expected_event_id=revision, actor="portal-admin", reason="Please independently validate a fresh run")
    time.sleep(.01)  # Must be after the persisted millisecond dispatch epoch.
    return result


def fresh(env, key):
    spec = copy.deepcopy(env["spec"]); spec["name"] += key
    drafts = DraftStore(env["store"])
    draft = drafts.create(canonical(spec), actor_id="alice", idempotency_key=key)
    draft = env["library"].save_controlled_draft(draft["draft_id"], actor_id="alice",
        expected_revision=draft["revision"], apply=True)["draft"]
    env["jobs"].enqueue(draft["draft_id"], actor_id="alice", expected_revision=draft["revision"], idempotency_key=key)
    result = GeneralValidationWorker(env["jobs"], env["assets"], env["library"]).run_once("worker-" + key)
    assert result["state"] == "validated"
    return result


def feedback(env, skill, run):
    return env["store"].record_negative_feedback(skill, run, actor="portal-admin", reason="Observed bad result")


def test_first_general_negative_questions_immediately_and_blocks_fresh_delivery(env):
    row, accepted = admitted(env)
    args = delivered(env)
    used = general.execute_reference(**args)
    assert used["execution_reuse_verified"]
    result = feedback(env, accepted["skill_id"], used["run_id"])
    assert result["status"] == "questioned"
    assert not skill_retrieval.search(query(env)["scenario_type"], query(env)["parameters_json"])["skills"]


def test_explicit_epoch_fresh_manual_run_restores_but_feedback_is_preserved(env):
    old, accepted = admitted(env); skill = accepted["skill_id"]
    feedback(env, skill, old["attempt"]["run_id"])
    epoch = request(env, skill)
    assert epoch["quality_level"] == "Q2"
    assert env["library"].admit(old["job_id"], actor_id="alice", expected_revision=old["revision"])["admitted"] is False
    new = fresh(env, "fresh")
    restored = env["library"].admit(new["job_id"], actor_id="alice", expected_revision=new["revision"])
    assert restored["admitted"] and restored["skill_id"] == skill and restored["version"] > accepted["version"]
    with env["store"]._read_db() as db:
        assert db.execute("SELECT count(*) FROM skill_feedback WHERE skill_id=?", (skill,)).fetchone()[0] == 1
        assert not env["library"]._feedback_allows(db, skill, new, automatic=True)
    args = delivered(env)
    assert general.execute_reference(**args)["execution_reuse_verified"]


def test_pre_epoch_validation_cannot_be_used_after_request(env):
    old, accepted = admitted(env)
    before = fresh(env, "before-withdrawal")
    feedback(env, accepted["skill_id"], old["attempt"]["run_id"])
    request(env, accepted["skill_id"])
    with pytest.raises(ValueError, match="negative_feedback_prevents_readmission"):
        env["library"].admit(before["job_id"], actor_id="alice", expected_revision=before["revision"])


def test_new_negative_after_epoch_requires_another_explicit_request(env):
    old, accepted = admitted(env); skill = accepted["skill_id"]
    feedback(env, skill, old["attempt"]["run_id"])
    request(env, skill)
    new = fresh(env, "new-but-reported")
    # A bound negative on this new execution invalidates the pending epoch.
    # Bind it through a legitimately admitted source version before a second report.
    env["library"].admit(new["job_id"], actor_id="alice", expected_revision=new["revision"])
    feedback(env, skill, new["attempt"]["run_id"])
    later = fresh(env, "later")
    with pytest.raises(ValueError):
        env["library"].admit(later["job_id"], actor_id="alice", expected_revision=later["revision"])


def test_archive_restore_requires_epoch_and_fresh_manual_validation(env):
    old, accepted = admitted(env); skill = accepted["skill_id"]
    head = env["store"].get_skill(skill)
    archived = env["store"].set_archived(skill, archived=True, expected_updated_at=head["updated_at"], actor="portal-admin", reason="Withdraw")
    env["store"].set_archived(skill, archived=False, expected_updated_at=archived["updated_at"], actor="portal-admin", reason="Restore for revalidation")
    request(env, skill)
    assert not env["library"].admit(old["job_id"], actor_id="alice", expected_revision=old["revision"])["admitted"]
    new = fresh(env, "after-restore")
    assert env["library"].admit(new["job_id"], actor_id="alice", expected_revision=new["revision"])["admitted"]
