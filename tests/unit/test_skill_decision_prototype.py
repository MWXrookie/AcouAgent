import hashlib
import json
import sqlite3
import time
from scripts.validation import host_dispatch_ledger as dispatch
from concurrent.futures import ThreadPoolExecutor
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding,PublicFormat
from app.skill_store import SkillStore
from scripts.validation import skill_decision_prototype as decision
from scripts.validation import host_consumption_prototype as ledger
from scripts.validation import host_forward_signature as signature
from scripts.validation import plane_wave_run_binding as binding
from scripts.validation.plane_wave_evidence import EXPECTED_CONFIGURATION
from tests.unit.test_plane_wave_evidence import evidence

def prepared(tmp_path, bad_physics=False):
    path=tmp_path/"joint.db";store=SkillStore(path)
    code=(binding.DIRECTORY/"recipes/val1_plane_wave_v2.py").read_text().strip()
    skill=store.create_candidate(scenario_type="plane_wave_2d",state_text="test fixed",
        normalized_params={"dx_m":0.00025,"domain_N":[384,384]},code_template=code,postconditions={},
        preconditions={"dimensions":2,"dx_m":[0.00025,0.00025]},
        physics_evidence={},source_run_id="fixture",validator_version="fixture",quality_level="Q2")
    # Test fixture only: production cannot promote by SQL or by this test consumer.
    with sqlite3.connect(path) as db:
        db.execute("UPDATE simulation_skills SET status='active',quality_level='Q3' WHERE skill_id=?",(skill,))
    ledger.initialize_test_ledger(path);decision.initialize_test_decisions(path)
    key=Ed25519PrivateKey.generate();public=key.public_key().public_bytes(Encoding.Raw,PublicFormat.Raw)
    ledger.append_test_root(path,"test-root",1,public)
    raw=evidence()
    if bad_physics:raw["raw_evidence"]["pressure_x2"]=[0]*3400
    stdout=json.dumps(raw,separators=(",",":"));nonce="a"*32
    request={"code":code,"timeout_seconds":30,"request_nonce":nonce}
    obs={"run_id":"12345678-1234-4234-8234-123456789012","nonce":nonce,"profile":"val1-plane-wave-v2",
        "code_sha256":binding.RECIPE_SHA256,
        "configuration_sha256":hashlib.sha256(signature.canonical(EXPECTED_CONFIGURATION)).hexdigest(),
        "request_sha256":hashlib.sha256(signature.canonical(request)).hexdigest(),
        "stdout_sha256":binding.digest(stdout),"stderr_sha256":binding.digest(""),
        "exit_code":0,"timed_out":False}
    from tests.unit.test_host_forwarder import IDENTITY
    obs.update(version="host-forward-observation.v1",target_ip="127.0.0.1",target_port=8010,
        peer_ip="127.0.0.1",identity=dict(IDENTITY),response_sha256="d"*64,
        observed_before_ns=1000000000,request_start_ns=2000000000,request_end_ns=3000000000,
        observed_after_ns=4000000000,authenticated=False,request_observed=False,
        parameters_bound=False,trusted_run=False)
    now=time.time_ns()
    obs.update(observed_before_ns=now-500000000,request_start_ns=now-400000000,
        request_end_ns=now-200000000,observed_after_ns=now-100000000)
    context=dict(root_id="test-root",revision=1,run_id=obs["run_id"],nonce=nonce,
        target_ip="127.0.0.1",target_port=8010,container_id=IDENTITY["container_id"],
        image_id=IDENTITY["image_id"],started_at=IDENTITY["started_at"],
        dispatch_not_before_ns=now-1000000000,dispatch_not_after_ns=now+59000000000)
    dispatch.initialize_test_dispatches(path)
    dispatch.record_test_dispatch(path,context)
    observed=time.time_ns()
    obs.update(observed_before_ns=observed,request_start_ns=observed+1,
        request_end_ns=observed+2,observed_after_ns=observed+3)
    payload={"root_id":"test-root","revision":1,"observation":obs}
    document=signature.canonical({"payload":payload,"signature":key.sign(signature.DOMAIN+signature.canonical(payload)).hex()})
    args=dict(skill_id=skill,version=1,root_id="test-root",revision=1,
        document=document,expected_payload=payload,stdout=stdout,stderr="")
    return path,public,args

def test_joint_decision_serializes_one_consumption(tmp_path):
    path,public,args=prepared(tmp_path)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda _:decision.consume_test_decision(path,**args),range(2)))
    assert sum(r["consumed"] for r in results)==1,results
    assert all(not r["authenticated"] and not r["trusted_run"] for r in results)
    assert decision.consume_test_decision(path,**args)["reason"]=="already_consumed"

@pytest.mark.parametrize("change",["revocation","questioned","archive","stdout","physics","boundary"])
def test_joint_rechecks_root_lifecycle_and_raw_physics(tmp_path,change):
    path,public,args=prepared(tmp_path,bad_physics=change=="physics")
    if change=="revocation":ledger.append_test_root(path,"test-root",2,public,revoked=True)
    if change in ("questioned","archive"):
        with sqlite3.connect(path) as db:
            db.execute("UPDATE simulation_skills SET "+("status='questioned'" if change=="questioned" else "archived=1"))
    if change=="boundary":
        with sqlite3.connect(path) as db:
            db.execute("UPDATE simulation_skills SET preconditions=?",('{"dimensions":3}',))
    if change=="stdout":args["stdout"]+=" "
    result=decision.consume_test_decision(path,**args)
    assert not result["consumed"],result
    if change=="physics":assert result["reason"]=="physics_failed"
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT COUNT(*) FROM consumption").fetchone()[0]==0
        assert db.execute("SELECT COUNT(*) FROM test_skill_decisions").fetchone()[0]==0

def test_joint_audit_failure_rolls_back_consumption(tmp_path):
    path,public,args=prepared(tmp_path)
    with sqlite3.connect(path) as db:
        db.execute("CREATE TRIGGER fail_test BEFORE INSERT ON test_skill_decisions BEGIN SELECT RAISE(ABORT,'test'); END")
    assert not decision.consume_test_decision(path,**args)["consumed"]
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT COUNT(*) FROM consumption").fetchone()[0]==0
        db.execute("DROP TRIGGER fail_test")
    assert decision.consume_test_decision(path,**args)["consumed"]
    with sqlite3.connect(path) as db:
        with pytest.raises(sqlite3.IntegrityError):db.execute("DELETE FROM test_skill_decisions")

def test_joint_missing_database_is_not_created(tmp_path):
    path,public,args=prepared(tmp_path);missing=tmp_path/"missing.db"
    assert not decision.consume_test_decision(missing,**args)["consumed"]
    assert not missing.exists()


def test_joint_latest_parent_withdrawal_rejected(tmp_path):
    path,public,args=prepared(tmp_path)
    store=SkillStore(path)
    from tests.unit.test_skill_store import candidate,set_test_status
    parent=candidate(store);set_test_status(store,parent,"active")
    # Set up a versioned dependency using the normal candidate API.
    code=(binding.DIRECTORY/"recipes/val1_plane_wave_v2.py").read_text().strip()
    child=store.create_candidate(scenario_type="plane_wave_2d",state_text="derived",
        normalized_params={"dx_m":0.00025,"domain_N":[384,384]},code_template=code,postconditions={},
        physics_evidence={},source_run_id="child-fixture",validator_version="fixture",
        quality_level="Q2",preconditions={"dimensions":2,"dx_m":[0.00025,0.00025]},
        reference_skill_ids=[parent])
    with sqlite3.connect(path) as db:
        db.execute("UPDATE simulation_skills SET status='active',quality_level='Q3' WHERE skill_id=?",(child,))
        args["version"]=db.execute("SELECT MAX(version) FROM skill_versions WHERE skill_id=?",(child,)).fetchone()[0]
    args["skill_id"]=child;set_test_status(store,parent,"questioned")
    result=decision.consume_test_decision(path,**args)
    assert not result["consumed"] and result["reason"]=="derived_lifecycle_unavailable",result


@pytest.mark.parametrize("fault",["missing","other_run","other_target","expired","bool_clock"])
def test_joint_signed_observation_requires_independent_context(tmp_path,monkeypatch,fault):
    path,public,args=prepared(tmp_path)
    if fault=="missing":
        with sqlite3.connect(path) as db:db.execute("DROP TABLE test_host_dispatches")
    if fault=="other_run":args["expected_payload"]["observation"]["run_id"]="12345678-1234-4234-8234-123456789013"
    if fault=="other_target":args["expected_payload"]["observation"]["identity"]["image_id"]="sha256:"+("c"*64)
    if fault=="expired":
        now=time.time_ns();monkeypatch.setattr(decision.time,"time_ns",lambda:now+400000000000)
    if fault=="bool_clock":monkeypatch.setattr(decision.time,"time_ns",lambda:True)
    assert not decision.consume_test_decision(path,**args)["consumed"]
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT COUNT(*) FROM consumption").fetchone()[0]==0

def test_dispatch_context_is_append_only_and_root_withdrawal_blocks_new_record(tmp_path):
    path,public,args=prepared(tmp_path)
    with sqlite3.connect(path) as db:
        raw=db.execute("SELECT context_json FROM test_host_dispatches").fetchone()[0]
        for sql in ("UPDATE test_host_dispatches SET context_sha256='forged'",
                    "DELETE FROM test_host_dispatches",
                    "INSERT OR REPLACE INTO test_host_dispatches SELECT * FROM test_host_dispatches"):
            with pytest.raises(sqlite3.IntegrityError):db.execute(sql)
    context=json.loads(raw)
    with pytest.raises(sqlite3.IntegrityError):dispatch.record_test_dispatch(path,context)
    ledger.append_test_root(path,"test-root",2,public,revoked=True)
    context.update(run_id="12345678-1234-4234-8234-123456789013",nonce="b"*32)
    with pytest.raises(ValueError,match="unavailable_test_root"):dispatch.record_test_dispatch(path,context)
