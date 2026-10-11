import json
import pytest
from app import skill_consistency_admission as admission,skill_consistency_source as source
from app import skill_consistency_reference as reference,skill_generation_decision as decisions,config
from host_observer.consistency_signing import reference_policy
from tests.unit.test_skill_consistency_source import prepared,records,NOW


def candidate_and_diagnostic(prepared):
    store,key,batch,contexts,signed=prepared
    candidate=admission.accumulate_candidate(batch_id=batch,records=signed)
    assert candidate["recorded"] and not candidate["trusted_run"]
    sid=candidate["skill_id"]
    return sid,admission.record_diagnostic(skill_id=sid,version=1,batch_id=batch,records=signed)


def test_complete_source_and_consistency_never_promotes_or_grants_code(prepared):
    store,key,batch,contexts,signed=prepared
    sid,result=candidate_and_diagnostic(prepared)
    assert result["diagnostic_recorded"] and not result["admitted"] and result["quality_level"]=="Q2"
    assert all(result[k] is False for k in source.FALSE_TRUST)
    assert "code" not in result
    record=store.get_skill(sid)
    assert record["status"]=="candidate" and record["quality_level"]=="Q2"
    with store._read_db() as db:
        assert db.execute("SELECT COUNT(*) FROM skill_host_verifications").fetchone()[0]==1
        detail=json.loads(db.execute("SELECT detail FROM skill_events WHERE event_type='consistency_profile_diagnostic'").fetchone()[0])
        assert not detail["physical_acceptance_passed"]
        assert all(detail[k] is False for k in source.FALSE_TRUST)
        assert set(detail["source"])==set(source.numerical.PROFILES)
        with pytest.raises(ValueError,match="independent_complete_physical_acceptance_required"):
            admission.verify_admitted_connection(db,skill_id=sid,version=1,batch_id=batch,records=signed,now=NOW+5)
    assert not admission.record_diagnostic(skill_id=sid,version=1,batch_id=batch,records=signed)["diagnostic_recorded"]


@pytest.mark.parametrize("fault",["parameters","code","batch","derived","archive","questioned","boundary","grade","missing"])
def test_diagnostic_rejects_scope_lifecycle_or_incomplete_group(prepared,fault):
    store,key,batch,contexts,signed=prepared
    candidate=admission.accumulate_candidate(batch_id=batch,records=signed);sid=candidate["skill_id"]
    with store._db_existing_write() as db:
        if fault=="parameters":db.execute("UPDATE skill_versions SET normalized_params='{}' WHERE skill_id=?",(sid,))
        elif fault=="code":db.execute("UPDATE skill_versions SET code_template='pass' WHERE skill_id=?",(sid,))
        elif fault=="batch":
            row=json.loads(db.execute("SELECT physics_evidence FROM skill_versions WHERE skill_id=?",(sid,)).fetchone()[0])
            row["batch_id"]="changed"
            db.execute("UPDATE skill_versions SET physics_evidence=? WHERE skill_id=?",(json.dumps(row),sid))
        elif fault=="derived":db.execute("UPDATE skill_versions SET reference_skill_ids='[1]' WHERE skill_id=?",(sid,))
        elif fault=="archive":db.execute("UPDATE simulation_skills SET archived=1 WHERE skill_id=?",(sid,))
        elif fault=="questioned":db.execute("UPDATE simulation_skills SET status='questioned' WHERE skill_id=?",(sid,))
        elif fault=="boundary":db.execute("UPDATE simulation_skills SET preconditions='{}' WHERE skill_id=?",(sid,))
        elif fault=="grade":db.execute("UPDATE simulation_skills SET quality_level='Q3' WHERE skill_id=?",(sid,))
    if fault=="missing":signed.pop("grid")
    assert not admission.record_diagnostic(skill_id=sid,version=1,batch_id=batch,records=signed)["diagnostic_recorded"]
    with store._read_db() as db:
        assert db.execute("SELECT COUNT(*) FROM skill_host_verifications").fetchone()[0]==0


def test_diagnostic_write_failure_rolls_back_event_and_record(prepared):
    store,key,batch,contexts,signed=prepared
    candidate=admission.accumulate_candidate(batch_id=batch,records=signed);sid=candidate["skill_id"]
    with store._db_existing_write() as db:
        db.execute("CREATE TRIGGER fail_consistency_receipt BEFORE INSERT ON skill_host_verifications BEGIN SELECT RAISE(ABORT,'test failure'); END")
    assert not admission.record_diagnostic(skill_id=sid,version=1,batch_id=batch,records=signed)["diagnostic_recorded"]
    with store._read_db() as db:
        assert not db.execute("SELECT 1 FROM skill_events WHERE event_type='consistency_profile_diagnostic'").fetchone()
    assert store.get_skill(sid)["status"]=="candidate"


def test_separate_owner_policy_cannot_override_missing_physical_acceptance(prepared,monkeypatch):
    import uuid
    store,key,batch,contexts,signed=prepared
    sid,result=candidate_and_diagnostic(prepared)
    monkeypatch.setattr(config,"SKILL_RETRIEVAL_ENABLED",True)
    monkeypatch.setattr(config,"SKILL_INJECTION_ENABLED",True)
    proof=dict(format=reference.FORMAT,skill_id=sid,version=1,batch_id=batch,records=signed,
        approval_document=reference_policy(key,skill_id=sid,version=1,batch_id=batch,records=signed,
            revision=1,valid_from_ns=NOW-1,valid_until_ns=NOW+100_000_000_000))
    assert not reference.register(proof)["recorded"]
    original="original generation context";params=json.dumps(admission.parameters())
    prepared_decision=decisions.prepare(scenario_type="initial_pressure_2d",parameters_json=params,
        original_context=original,_existing_store=True)
    consumed=reference.consume(decision_id=prepared_decision["decision_id"],nonce=prepared_decision["nonce"],
        generation_run_id=str(uuid.uuid4()),scenario_type="initial_pressure_2d",parameters_json=params,
        original_context=original,skill_id=sid,version=1)
    assert consumed["fallback"] and not consumed["consumed"] and consumed["context"]==original
    assert consumed["skills"]==[] and all(consumed[k] is False for k in source.FALSE_TRUST)
    with store._read_db() as db:
        assert db.execute("SELECT COUNT(*) FROM skill_reference_capsules").fetchone()[0]==0
        assert db.execute("SELECT COUNT(*) FROM skill_generation_consumptions").fetchone()[0]==0


@pytest.mark.parametrize("case",["grid","time","equivalent"])
def test_auxiliary_run_feedback_binds_original_case_and_withdraws_bundle(prepared,case):
    store,key,batch,contexts,signed=prepared
    sid,result=candidate_and_diagnostic(prepared)
    run=contexts[case]["run_id"]
    store.record_feedback_statement(sid,run,confirmed=True,request_id="confirm-"+case,
        actor="portal-admin",reason="recorded statement only")
    receipt=store.record_feedback_statement(sid,run,confirmed=False,request_id="withdraw-"+case,
        actor="portal-admin",reason="result not accepted")
    with store._read_db() as db:
        detail=json.loads(db.execute("SELECT detail FROM skill_events WHERE id=?",(receipt["event_id"],)).fetchone()[0])
        association=detail["source_association"]
        assert association["kind"]=="source_consistency_experiment" and association["version"]==1
        assert association["case"]==case and association["batch_id"]==batch
        assert not association["actual_use_authenticated"] and not association["host_recorded_diagnostic"]["trust_verified"]
        assert association["code_sha256"]==source.manifest()["recipes"][case]["code_sha256"]
        checked=source.verify_connection(db,batch_id=batch,records=signed,now=NOW+5)
        with pytest.raises(ValueError,match="withdrawn_consistency_source"):
            admission._feedback_epoch(db,sid,checked)


def test_unconsumed_auxiliary_dispatch_is_not_feedback_source(prepared):
    store,key,batch,contexts,signed=prepared
    candidate=admission.accumulate_candidate(batch_id=batch,records=signed);sid=candidate["skill_id"]
    with pytest.raises(ValueError,match="run_not_associated"):
        store.record_feedback_statement(sid,contexts["grid"]["run_id"],confirmed=False,
            request_id="unbound",actor="portal-admin",reason="no recorded diagnostic")
    with store._read_db() as db:
        assert not db.execute("SELECT 1 FROM skill_events WHERE event_type='feedback_statement'").fetchone()


def test_auxiliary_initial_negative_feedback_needs_no_prior_confirmation(prepared):
    store,key,batch,contexts,signed=prepared
    sid,result=candidate_and_diagnostic(prepared)
    store.record_negative_feedback(sid,contexts["grid"]["run_id"],actor="portal-admin",reason="result not right")
    with store._read_db() as db:
        checked=source.verify_connection(db,batch_id=batch,records=signed,now=NOW+5)
        with pytest.raises(ValueError,match="negative_consistency_source"):
            admission._feedback_epoch(db,sid,checked)
