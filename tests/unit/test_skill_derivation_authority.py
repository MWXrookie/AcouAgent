"""Independent child observation and signed upstream policy, all synthetic."""
import copy
import hashlib
import json
import uuid
import pytest
from app import skill_authority as authority,skill_host_intake as intake
from app import skill_reference_registry as registry,skill_fixed_activation as activation
from tests.unit.test_skill_reference_registry import proof
from tests.unit.test_skill_reference_consumer import available
from tests.unit.test_skill_host_intake import ready
from tests.unit.test_skill_authority import prepared,context,approval
from scripts.validation.host_forward_signature import canonical,DOMAIN

def child(proof,monkeypatch):
    store,key,call,parent_proof=proof
    assert registry.register(parent_proof)["recorded"]
    ctx=context();ctx.update(run_id=str(uuid.uuid4()),nonce=uuid.uuid4().hex)
    assert authority.record_dispatch(ctx,approval(key,ctx))["recorded"]
    payload=json.loads(parent_proof["observation_document"])["payload"]
    obs=payload["observation"]
    obs.update(run_id=ctx["run_id"],nonce=ctx["nonce"],
        observed_before_ns=100_600_000_000,request_start_ns=100_700_000_000,
        request_end_ns=101_000_000_000,observed_after_ns=101_100_000_000)
    monkeypatch.setattr(authority.time,"time_ns",lambda:101_200_000_000)
    code=(intake.pins.DIRECTORY/"recipes/val1_plane_wave_v2.py").read_text().strip()
    obs["request_sha256"]=hashlib.sha256(canonical(dict(code=code,timeout_seconds=30,request_nonce=ctx["nonce"]))).hexdigest()
    sid=store.create_candidate(scenario_type="initial_pressure_2d",state_text="independent derived fixture",
        normalized_params=json.loads(call["parameters_json"]),code_template=code,
        postconditions={},preconditions={"dimensions":2},physics_evidence={},
        source_run_id=ctx["run_id"],validator_version="fixture",quality_level="Q2",
        reference_skill_ids=[call["skill_id"]])
    document=canonical(dict(payload=payload,signature=key.sign(DOMAIN+canonical(payload)).hex()))
    args=dict(skill_id=sid,version=1,observation_document=document,stdout=call["stdout"],stderr="")
    assert intake.record(**args)["recorded"]
    return args

def test_child_needs_own_receipt_and_current_signed_upstream(proof,monkeypatch):
    store,key,call,evidence=proof
    args=child(proof,monkeypatch)
    assert activation.admit(**args)["admitted"]
    assert store.get_skill(args["skill_id"])["status"]=="active"
    # Withdrawal of the upstream invalidates the child's source immediately.
    store.record_feedback_statement(call["skill_id"],json.loads(evidence["observation_document"])["payload"]["observation"]["run_id"],
        confirmed=True,request_id="parent-confirm",actor="portal-admin",reason="fixture")
    store.record_feedback_statement(call["skill_id"],json.loads(evidence["observation_document"])["payload"]["observation"]["run_id"],
        confirmed=False,request_id="parent-withdraw",actor="portal-admin",reason="fixture")
    with store._read_db() as db:
        with pytest.raises(ValueError):
            intake.verify_connection(store,db,**args,now=authority.time.time_ns(),
                recorded_evidence=True,require_independent_root=True)

@pytest.mark.parametrize("fault",["capsule_missing","expired","signature","cycle","parameters"])
def test_active_label_without_full_upstream_authority_does_not_admit(proof,monkeypatch,fault):
    store,key,call,evidence=proof
    args=child(proof,monkeypatch)
    if fault=="capsule_missing":
        # Append-only production tables cannot be deleted. A missing-table
        # simulation tests the unavailable-storage boundary instead.
        with store._db_existing_write() as db:db.execute("DROP TABLE skill_reference_capsules")
    elif fault=="expired":
        monkeypatch.setattr(authority.time,"time_ns",lambda:180_000_000_000)
    elif fault=="signature":
        monkeypatch.setattr(registry.consumer,"_approval",lambda _:(_ for _ in ()).throw(ValueError("invalid signature")))
    elif fault=="parameters":
        with store._db_existing_write() as db:
            db.execute("UPDATE skill_versions SET normalized_params=? WHERE skill_id=?",
                ('{"dimensions":2}',call["skill_id"]))
    else:
        with store._db_existing_write() as db:
            db.execute("UPDATE skill_versions SET reference_skill_ids=? WHERE skill_id=?",
                (json.dumps([args["skill_id"]]),call["skill_id"]))
            db.execute("INSERT INTO skill_references VALUES (?,?,?)",(call["skill_id"],1,args["skill_id"]))
    assert not activation.admit(**args)["admitted"]
    assert store.get_skill(args["skill_id"])["status"]=="candidate"


def test_parent_policy_expiry_is_distinct_from_root_expiry(proof,monkeypatch):
    from host_observer.authority_signing import skill_reference_policy
    store,key,call,evidence=proof
    payload=json.loads(evidence["approval_document"])["payload"]
    evidence["approval_document"]=skill_reference_policy(key,skill_id=call["skill_id"],version=1,
        parameters_sha256=payload["parameters_sha256"],observation_sha256=payload["observation_sha256"],
        revision=1,valid_from_ns=90_000_000_000,valid_until_ns=101_150_000_000)
    args=child(proof,monkeypatch)
    assert not activation.admit(**args)["admitted"]
    assert store.get_skill(args["skill_id"])["status"]=="candidate"


def test_derived_handoff_rechecks_upstream_after_prepare_and_consumption(proof,monkeypatch):
    from host_observer.authority_signing import skill_reference_policy
    from app import skill_retrieval,skill_reference_consumer as consumer
    store,key,call,evidence=proof
    args=child(proof,monkeypatch)
    assert activation.admit(**args)["admitted"]
    document=json.loads(args["observation_document"])["payload"]
    grant=skill_reference_policy(key,skill_id=args["skill_id"],version=1,
        parameters_sha256=hashlib.sha256(canonical(json.loads(call["parameters_json"]))).hexdigest(),
        observation_sha256=hashlib.sha256(canonical(document)).hexdigest(),revision=1,
        valid_from_ns=90_000_000_000,valid_until_ns=180_000_000_000,
        scenario_type="initial_pressure_2d")
    assert registry.register(dict(args,approval_document=grant))["recorded"]
    got=skill_retrieval.generation_context("derived",scenario_type="initial_pressure_2d",
        parameters_json=call["parameters_json"])
    assert got["context"]!="derived" and not got["skill_usage"]["fallback"]
    assert not got["skill_usage"]["actual_use_authenticated"]
    info=got["skill_usage"]
    reader=dict(decision_id=info["decision_id"],generation_run_id=info["generation_intent_id"],
        scenario_type="initial_pressure_2d",parameters_json=call["parameters_json"],original_context="derived")
    assert consumer.read_committed_context(**reader)==got["context"]
    # This SQL models a withdrawn upstream lifecycle while keeping all child
    # evidence and signed policies untouched.
    with store._db_existing_write() as db:
        db.execute("UPDATE simulation_skills SET status='questioned' WHERE skill_id=?",(call["skill_id"],))
    assert consumer.read_committed_context(**reader)=="derived"
    assert skill_retrieval.search("initial_pressure_2d",call["parameters_json"])["skills"]==[]


def test_removing_declared_parent_cannot_bypass_its_withdrawal(proof,monkeypatch):
    store,key,call,evidence=proof
    args=child(proof,monkeypatch)
    assert activation.admit(**args)["admitted"]
    with store._db_existing_write() as db:
        db.execute("DELETE FROM skill_references WHERE skill_id=?",(args["skill_id"],))
        db.execute("UPDATE skill_versions SET reference_skill_ids='[]' WHERE skill_id=?",(args["skill_id"],))
    with store._read_db() as db:
        with pytest.raises(ValueError,match="source_derivation_changed"):
            intake.verify_connection(store,db,**args,now=authority.time.time_ns(),
                recorded_evidence=True,require_independent_root=True)
