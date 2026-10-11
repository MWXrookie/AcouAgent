import json
import pytest
from app import config,skill_host_intake as intake,skill_fixed_activation as activation
from tests.unit.test_skill_host_intake import ready
from tests.unit.test_skill_authority import prepared,root

def test_fixed_admission_is_bounded_audited_and_not_user_use(ready):
    store,key,ctx,obs,args,doc=ready
    assert not activation.admit(**args)["admitted"]  # no immutable source receipt
    assert intake.record(**args)["recorded"]
    result=activation.admit(**args)
    assert result["admitted"] and result["quality_level"]=="Q4"
    assert not result["actual_use_authenticated"]
    skill=store.get_skill(args["skill_id"])
    assert skill["status"]=="active"
    with store._read_db() as db:
        bounds=json.loads(db.execute("SELECT preconditions FROM simulation_skills WHERE skill_id=?",(args["skill_id"],)).fetchone()[0])
        assert set(bounds)=={"dimensions","parameters_sha256"}
        assert db.execute("SELECT count(*) FROM skill_events WHERE event_type='fixed_profile_admitted'").fetchone()[0]==1
    assert not activation.admit(**args)["admitted"]

@pytest.mark.parametrize("failure",["revoked","withdrawn","numerical","commit","disabled"])
def test_admission_denial_keeps_candidate(ready,monkeypatch,failure):
    store,key,ctx,obs,args,doc=ready
    assert intake.record(**args)["recorded"]
    if failure=="revoked":assert activation.intake.skill_authority.record_root_statement(root(key,2,"revoke"))["recorded"]
    elif failure=="withdrawn":
        store.record_feedback_statement(args["skill_id"],ctx["run_id"],confirmed=True,
            request_id="confirmed",actor="portal-admin",reason="test")
        store.record_feedback_statement(args["skill_id"],ctx["run_id"],confirmed=False,
            request_id="withdrawn",actor="portal-admin",reason="test")
    elif failure=="numerical":args["stdout"]='{}'
    elif failure=="commit":
        with store._db_existing_write() as db:
            db.execute("CREATE TRIGGER fault BEFORE UPDATE ON simulation_skills BEGIN SELECT RAISE(ABORT,'fault'); END")
    elif failure=="disabled":monkeypatch.setattr(config,"SKILL_LEARNING_ENABLED",False)
    assert not activation.admit(**args)["admitted"]
    assert store.get_skill(args["skill_id"])["status"]=="candidate"
    with store._read_db() as db:
        assert db.execute("SELECT count(*) FROM skill_events WHERE event_type='fixed_profile_admitted'").fetchone()[0]==0

def test_parameter_declaration_drift_cannot_be_admitted(ready):
    store,key,ctx,obs,args,doc=ready
    assert intake.record(**args)["recorded"]
    with store._db_existing_write() as db:
        db.execute("UPDATE skill_versions SET normalized_params=? WHERE skill_id=? AND version=1",
                   ('{"dimensions":2}',args["skill_id"]))
    assert not activation.admit(**args)["admitted"]


def test_positive_confirmation_does_not_itself_grant_or_block_admission(ready):
    store,key,ctx,obs,args,doc=ready
    store.record_feedback_statement(args["skill_id"],ctx["run_id"],confirmed=True,
        request_id="confirmed",actor="portal-admin",reason="test")
    assert not activation.admit(**args)["admitted"]
    assert intake.record(**args)["recorded"]
    assert activation.admit(**args)["admitted"]

def test_negative_source_feedback_prevents_admission(ready):
    store,key,ctx,obs,args,doc=ready
    assert intake.record(**args)["recorded"]
    store.record_negative_feedback(args["skill_id"],ctx["run_id"],actor="portal-admin",reason="test")
    assert not activation.admit(**args)["admitted"]

@pytest.mark.parametrize("failure",["expired","disabled","manual_disabled"])
def test_admission_rechecks_after_write_and_rolls_back(ready,monkeypatch,failure):
    store,key,ctx,obs,args,doc=ready
    if failure=="manual_disabled":
        monkeypatch.setattr(config,"SKILL_MANUAL_INTAKE_ENABLED",True)
        monkeypatch.setattr(config,"SKILL_LEARNING_ENABLED",False)
    assert intake.record(**args)["recorded"]
    original=activation._budget
    def change_at_final_budget(db):
        original(db)
        if db.execute("SELECT 1 FROM skill_events WHERE event_type='fixed_profile_admitted' LIMIT 1").fetchone():
            if failure=="expired":
                monkeypatch.setattr(activation.time,"time_ns",lambda:180_000_000_000)
            elif failure=="manual_disabled":
                monkeypatch.setattr(config,"SKILL_MANUAL_INTAKE_ENABLED",False)
            else:
                monkeypatch.setattr(config,"SKILL_LEARNING_ENABLED",False)
    monkeypatch.setattr(activation,"_budget",change_at_final_budget)
    assert not activation.admit(**args)["admitted"]
    assert store.get_skill(args["skill_id"])["status"]=="candidate"
    with store._read_db() as db:
        assert db.execute("SELECT count(*) FROM skill_events WHERE event_type='fixed_profile_admitted'").fetchone()[0]==0
