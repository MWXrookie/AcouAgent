"""Positive handoffs use synthetic owner/host signatures and analytic traces.
They do not enroll the user's root, establish production Q4, or run a model."""
import hashlib
import json
import uuid
from concurrent.futures import ThreadPoolExecutor
import pytest
from app import config,skill_generation_decision as decisions
from app import skill_reference_consumer as consumer,skill_host_intake as intake
from app import skill_fixed_activation as activation
from scripts.validation.host_forward_signature import canonical
from tests.unit.test_skill_host_intake import ready
from tests.unit.test_skill_authority import prepared,root,signed

@pytest.fixture
def available(ready,monkeypatch):
    store,key,ctx,obs,args,doc=ready
    for flag in ("SKILL_RETRIEVAL_ENABLED","SKILL_INJECTION_ENABLED"):
        monkeypatch.setattr(config,flag,True)
    assert intake.record(**args)["recorded"]
    assert activation.admit(**args)["admitted"]
    params=json.dumps(intake.controlled_dispatch.fixed_retrieval_parameters())
    pre=decisions.prepare(scenario_type="plane_wave_2d",parameters_json=params,original_context="original")
    assert pre["prepared"]
    call=dict(decision_id=pre["decision_id"],nonce=pre["nonce"],generation_run_id=str(uuid.uuid4()),
        scenario_type="plane_wave_2d",parameters_json=params,original_context="original",**args)
    norm,ps,cs=decisions._request(call["scenario_type"],params,"original")
    payload=dict(purpose="approve_generation_reference",scenario_type="plane_wave_2d",root_id=ctx["root_id"],revision=ctx["revision"],
        profile=intake.approved.PROFILE,decision_id=call["decision_id"],decision_nonce=call["nonce"],
        generation_run_id=call["generation_run_id"],skill_id=args["skill_id"],skill_version=1,
        parameters_sha256=ps,generation_context_sha256=cs,
        observation_sha256=hashlib.sha256(canonical(json.loads(doc())["payload"])).hexdigest(),
        validator_sha256=intake.pins.VALIDATOR_SHA256,expires_ns=120_000_000_000,
        **consumer.authority.fixed_scope())
    call["approval_document"]=signed(key,payload)
    return store,key,call,payload

def test_fixed_reference_handoff_is_once_and_committed(available):
    store,key,call,payload=available
    got=consumer.consume(**call)
    assert got["consumed"] and not got["fallback"] and len(got["skills"])==1
    assert got["skills"][0]["code_sha256"]==intake.pins.RECIPE_SHA256
    assert got["context"].startswith("original\n")
    assert not got["actual_use_authenticated"]
    assert all(got[k] for k in consumer.authority.TRUST)
    assert not consumer.consume(**call)["consumed"]
    with store._read_db() as db:
        assert db.execute("SELECT count(*) FROM skill_generation_consumptions").fetchone()[0]==1

@pytest.mark.parametrize("mutation",["unsigned","expired","query","context","stdout","candidate","withdrawn","revoked","version"])
def test_failed_recheck_returns_no_code_and_no_audit(available,mutation):
    store,key,call,payload=available
    if mutation=="unsigned":call["approval_document"]=json.dumps({"payload":payload,"signature":"0"*128})
    elif mutation=="expired":
        payload["expires_ns"]=100_500_000_000;call["approval_document"]=signed(key,payload)
    elif mutation=="query":call["parameters_json"]='{"dimensions":2}'
    elif mutation=="context":call["original_context"]="changed"
    elif mutation=="stdout":call["stdout"]=call["stdout"].replace('"pressure_x1"', '"pressure_changed"')
    elif mutation in {"candidate","withdrawn"}:
        with store._db_existing_write() as db:
            db.execute("UPDATE simulation_skills SET status=? WHERE skill_id=?",
                       ("candidate" if mutation=="candidate" else "questioned",call["skill_id"]))
    elif mutation=="revoked":
        assert consumer.authority.record_root_statement(root(key,2,"revoke"))["recorded"]
    elif mutation=="version":call["version"]=2
    got=consumer.consume(**call)
    assert not got["consumed"] and got["skills"]==[] and got["context"]==call["original_context"]
    with store._read_db() as db:
        assert db.execute("SELECT count(*) FROM skill_generation_consumptions").fetchone()[0]==0

def test_commit_failure_never_leaks_reference(available):
    store,key,call,payload=available
    with store._db_existing_write() as db:
        db.execute("CREATE TRIGGER fault BEFORE INSERT ON skill_generation_consumptions BEGIN SELECT RAISE(ABORT,'fault'); END")
    got=consumer.consume(**call)
    assert not got["consumed"] and got["skills"]==[] and got["context"]=="original"

def test_concurrent_one_handoff(available):
    store,key,call,payload=available
    with ThreadPoolExecutor(max_workers=2) as pool:
        out=list(pool.map(lambda _:consumer.consume(**call),range(2)))
    assert sum(item["consumed"] for item in out)==1
    assert sum(bool(item["skills"]) for item in out)==1

def test_default_off_skips_authority_and_database(tmp_path,monkeypatch):
    monkeypatch.setattr(config,"SKILL_INJECTION_ENABLED",False)
    monkeypatch.setattr(consumer.authority,"_document",lambda *a:pytest.fail("authority must not be touched"))
    got=consumer.consume(decision_id="",nonce="",generation_run_id="",scenario_type="",
        parameters_json="",original_context="unchanged",approval_document="",skill_id="",version=0,
        observation_document=b"",stdout="",stderr="")
    assert not got["consumed"] and got["context"]=="unchanged" and got["skills"]==[]


def test_historical_source_can_be_reused_after_dispatch_window_but_not_root_expiry(ready,monkeypatch):
    store,key,ctx,obs,args,doc=ready
    assert intake.record(**args)["recorded"]
    for flag in ("SKILL_RETRIEVAL_ENABLED","SKILL_INJECTION_ENABLED"):
        monkeypatch.setattr(config,flag,True)
    assert activation.admit(**args)["admitted"]
    monkeypatch.setattr(consumer.time,"time_ns",lambda:170_000_000_000)
    params=json.dumps(intake.controlled_dispatch.fixed_retrieval_parameters())
    pre=decisions.prepare(scenario_type="plane_wave_2d",parameters_json=params,original_context="later")
    call=dict(decision_id=pre["decision_id"],nonce=pre["nonce"],generation_run_id=str(uuid.uuid4()),
              scenario_type="plane_wave_2d",parameters_json=params,original_context="later",**args)
    norm,ps,cs=decisions._request("plane_wave_2d",params,"later")
    payload=dict(purpose="approve_generation_reference",scenario_type="plane_wave_2d",root_id=ctx["root_id"],revision=1,
        profile=intake.approved.PROFILE,decision_id=call["decision_id"],decision_nonce=call["nonce"],
        generation_run_id=call["generation_run_id"],skill_id=args["skill_id"],skill_version=1,
        parameters_sha256=ps,generation_context_sha256=cs,
        observation_sha256=hashlib.sha256(canonical(json.loads(doc())["payload"])).hexdigest(),
        validator_sha256=intake.pins.VALIDATOR_SHA256,expires_ns=190_000_000_000,
        **consumer.authority.fixed_scope())
    call["approval_document"]=signed(key,payload)
    assert consumer.consume(**call)["consumed"]
    call["generation_run_id"]=str(uuid.uuid4())
    later=decisions.prepare(scenario_type="plane_wave_2d",parameters_json=params,original_context="later")
    call["decision_id"]=later["decision_id"];call["nonce"]=later["nonce"]
    payload.update(decision_id=call["decision_id"],decision_nonce=call["nonce"],generation_run_id=call["generation_run_id"])
    call["approval_document"]=signed(key,payload)
    monkeypatch.setattr(consumer.time,"time_ns",lambda:180_000_000_000)
    denied=consumer.consume(**call)
    assert not denied["consumed"] and denied["skills"]==[]


def test_host_grant_helper_is_explicit_and_does_not_consume(available):
    from host_observer.authority_signing import generation_reference_approval
    store,key,call,payload=available
    call["approval_document"]=generation_reference_approval(key,payload=payload)
    with store._read_db() as db:
        assert db.execute("SELECT count(*) FROM skill_generation_consumptions").fetchone()[0]==0
    assert consumer.consume(**call)["consumed"]
    payload["runtime_approved"]=True
    with pytest.raises(ValueError):generation_reference_approval(key,payload=payload)


def test_internal_generation_reuses_exact_policy_then_withdraws(available):
    from host_observer.authority_signing import skill_reference_policy
    from app import skill_retrieval
    store,key,call,payload=available
    policy=skill_reference_policy(key,skill_id=call["skill_id"],version=1,
        parameters_sha256=payload["parameters_sha256"],observation_sha256=payload["observation_sha256"],
        revision=1,valid_from_ns=90_000_000_000,valid_until_ns=180_000_000_000)
    proof={k:call[k] for k in ("skill_id","version","observation_document","stdout","stderr")}
    proof["approval_document"]=policy
    for original in ("first generation","second generation"):
        result=skill_retrieval.generation_context(original,scenario_type="plane_wave_2d",
            parameters_json=call["parameters_json"],reference_evidence=proof)
        assert result["context"].startswith(original+"\n")
        assert not result["skill_usage"]["fallback"] and result["skill_usage"]["audit_recorded"]
        assert not result["skill_usage"]["actual_use_authenticated"]
    source=json.loads(call["observation_document"])["payload"]["observation"]["run_id"]
    store.record_feedback_statement(call["skill_id"],source,confirmed=True,
        request_id="confirm",actor="portal-admin",reason="test")
    store.record_feedback_statement(call["skill_id"],source,confirmed=False,
        request_id="withdraw",actor="portal-admin",reason="test")
    result=skill_retrieval.generation_context("after withdrawal",scenario_type="plane_wave_2d",
        parameters_json=call["parameters_json"],reference_evidence=proof)
    assert result["context"]=="after withdrawal" and result["skill_usage"]["skills"]==[]
    with store._read_db() as db:
        assert db.execute("SELECT count(*) FROM skill_generation_consumptions WHERE fallback_reason='fixed_profile_reference_handoff'").fetchone()[0]==2

@pytest.mark.parametrize("mutation",["wrong_scope","expired","boolean","too_long","unsigned"])
def test_repeated_policy_cannot_bypass_signature_or_scope(available,mutation):
    store,key,call,payload=available
    policy={k:payload[k] for k in consumer.POLICY_FIELDS if k in payload}
    policy.update(purpose="approve_skill_reference_policy",valid_from_ns=90_000_000_000,
                  valid_until_ns=180_000_000_000)
    if mutation=="wrong_scope":policy["parameters_sha256"]="0"*64
    elif mutation=="expired":policy["valid_until_ns"]=100_500_000_000
    elif mutation=="boolean":policy["valid_from_ns"]=False
    elif mutation=="too_long":policy["valid_until_ns"]=200_000_000_000_000
    call["approval_document"]=signed(key,policy)
    if mutation=="unsigned":
        doc=json.loads(call["approval_document"]);doc["signature"]="0"*128
        call["approval_document"]=json.dumps(doc)
    got=consumer.consume(**call)
    assert not got["consumed"] and got["skills"]==[]


def test_derived_label_does_not_replace_upstream_authority(available):
    store,key,call,payload=available
    with store._db_existing_write() as db:
        db.execute("UPDATE skill_versions SET reference_skill_ids=? WHERE skill_id=? AND version=1",
                   ('["unverified-parent"]',call["skill_id"]))
    out=consumer.consume(**call)
    assert not out["consumed"] and out["skills"]==[]


def test_fixed_pulse_scene_requires_its_own_signed_category(available):
    from host_observer.authority_signing import skill_reference_policy
    from app import skill_retrieval
    store,key,call,payload=available
    with store._db_existing_write() as db:
        db.execute("UPDATE simulation_skills SET scenario_type='initial_pressure_2d' WHERE skill_id=?",(call["skill_id"],))
    proof={k:call[k] for k in ("skill_id","version","observation_document","stdout","stderr")}
    proof["approval_document"]=skill_reference_policy(key,skill_id=call["skill_id"],version=1,
        parameters_sha256=payload["parameters_sha256"],observation_sha256=payload["observation_sha256"],
        revision=1,valid_from_ns=90_000_000_000,valid_until_ns=180_000_000_000)
    bad=skill_retrieval.generation_context("pulse",scenario_type="initial_pressure_2d",
        parameters_json=call["parameters_json"],reference_evidence=proof)
    assert bad["context"]=="pulse" and bad["skill_usage"]["skills"]==[]
    proof["approval_document"]=skill_reference_policy(key,skill_id=call["skill_id"],version=1,
        parameters_sha256=payload["parameters_sha256"],observation_sha256=payload["observation_sha256"],
        revision=1,valid_from_ns=90_000_000_000,valid_until_ns=180_000_000_000,scenario_type="initial_pressure_2d")
    good=skill_retrieval.generation_context("pulse",scenario_type="initial_pressure_2d",
        parameters_json=call["parameters_json"],reference_evidence=proof)
    assert not good["skill_usage"]["fallback"]
