import copy
import hashlib
import json
import sqlite3
import uuid
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding,PublicFormat
from app import config,skill_library_service as service,skill_consistency_source as source
from app.skill_store import SkillStore
from scripts.validation.host_forward_signature import canonical,DOMAIN as OBSERVATION_DOMAIN
from host_observer.consistency_signing import policy,dispatch
from tests.unit.test_controlled_consistency import records
from tests.unit.test_host_forwarder import IDENTITY,endpoint,TOKEN,RUN,NONCE

NOW=100_000_000_000


def observation(key,ctx,stdout,**changes):
    pins=source.manifest()["recipes"][ctx["case"]]
    code=source.numerical.recipe(*source.numerical.PROFILES[ctx["case"]])
    o=dict(version="host-forward-observation.v1",run_id=ctx["run_id"],nonce=ctx["nonce"],
        profile=source.PROFILE+"."+ctx["case"],request_sha256=hashlib.sha256(canonical(
            dict(code=code,timeout_seconds=30,request_nonce=ctx["nonce"]))).hexdigest(),
        **pins,response_sha256="a"*64,stdout_sha256=hashlib.sha256(stdout.encode()).hexdigest(),
        stderr_sha256=hashlib.sha256(b"").hexdigest(),exit_code=0,timed_out=False,
        target_ip=ctx["target_ip"],target_port=ctx["target_port"],peer_ip=ctx["target_ip"],
        identity=dict(IDENTITY),observed_before_ns=NOW+1,request_start_ns=NOW+2,
        request_end_ns=NOW+3,observed_after_ns=NOW+4,
        authenticated=False,request_observed=False,parameters_bound=False,trusted_run=False)
    o.update(changes)
    body=dict(root_id=source.ROOT_ID,revision=ctx["revision"],observation=o)
    return canonical(dict(payload=body,signature=key.sign(OBSERVATION_DOMAIN+canonical(body)).hex())).decode()


@pytest.fixture
def prepared(tmp_path,monkeypatch,records):
    from app.physics_planar_initial import reference_profile
    for case,(size,_,_) in source.numerical.PROFILES.items():
        raw=json.loads(records[case]["stdout"])
        raw["pressure"]=[[value]*size for value in reference_profile(size,raw["sample_time_s"])]
        records[case]["stdout"]=json.dumps(raw)
    store=SkillStore(tmp_path/"skills.db");store.migrate()
    monkeypatch.setattr(service,"STORE",store)
    monkeypatch.setattr(config,"SKILL_LIBRARY_ENABLED",True)
    monkeypatch.setattr(config,"SKILL_LEARNING_ENABLED",True)
    key=Ed25519PrivateKey.generate()
    public=key.public_key().public_bytes(Encoding.Raw,PublicFormat.Raw)
    monkeypatch.setattr(source.approved,"approved_public_key",lambda:public)
    monkeypatch.setattr(source.time,"time_ns",lambda:NOW)
    assert source.record_policy(policy(key,revision=1,action="approve",
        valid_from_ns=NOW-1,valid_until_ns=NOW+600_000_000_000))["recorded"]
    batch=str(uuid.uuid4());contexts={};signed={}
    for case in source.numerical.PROFILES:
        ctx=dict(root_id=source.ROOT_ID,revision=1,batch_id=batch,case=case,
            run_id=str(uuid.uuid4()),nonce=uuid.uuid4().hex,
            target_ip="127.0.0.1",target_port=8010,
            container_id=IDENTITY["container_id"],image_id=IDENTITY["image_id"],
            started_at=IDENTITY["started_at"],dispatch_not_before_ns=NOW-1,
            dispatch_not_after_ns=NOW+60_000_000_000)
        assert source.record_dispatch(ctx,dispatch(key,context=ctx))["recorded"]
        contexts[case]=ctx
        signed[case]=dict(observation_document=observation(key,ctx,records[case]["stdout"]),
                          stdout=records[case]["stdout"],stderr="")
    monkeypatch.setattr(source.time,"time_ns",lambda:NOW+5)
    return store,key,batch,contexts,signed


def test_complete_same_db_signed_batch_only_verifies_prerequisites(prepared):
    store,key,batch,contexts,signed=prepared
    before=store.path.read_bytes()
    result=source.inspect_batch(batch_id=batch,records=signed)
    assert result["source_prerequisites_passed"] and result["numeric_pass"]
    assert result["quality_level"]=="Q2" and set(result["source"])==set(source.numerical.PROFILES)
    assert all(result[k] is False for k in source.FALSE_TRUST)
    with store._read_db() as db:
        assert db.execute("SELECT COUNT(*) FROM simulation_skills").fetchone()[0]==0
        assert db.execute("SELECT COUNT(*) FROM skill_events").fetchone()[0]==0
        assert db.execute("SELECT COUNT(*) FROM skill_host_verifications").fetchone()[0]==0


@pytest.mark.parametrize("fault",["missing","mixed_batch","swap","raw_change","signature","wrong_target",
 "wrong_profile","parameters","unobserved","early","stdout_budget"])
def test_missing_mixed_unbound_or_self_reported_case_cannot_pass(prepared,fault):
    store,key,batch,contexts,signed=prepared
    if fault=="missing":signed.pop("time")
    elif fault=="mixed_batch":batch=str(uuid.uuid4())
    elif fault=="swap":signed["base"],signed["grid"]=signed["grid"],signed["base"]
    elif fault=="raw_change":signed["base"]["stdout"]+=" "
    elif fault=="signature":signed["base"]["observation_document"]=signed["base"]["observation_document"].replace('"signature":"','"signature":"0',1)
    elif fault=="stdout_budget":signed["base"]["stdout"]=" "*500001
    else:
        changes={"wrong_target":dict(peer_ip="127.0.0.2"),"wrong_profile":dict(profile="val1-plane-wave-v2"),
            "parameters":dict(configuration_sha256="0"*64),"unobserved":dict(authenticated=True),
            "early":dict(observed_before_ns=NOW-1)}[fault]
        signed["base"]["observation_document"]=observation(key,contexts["base"],signed["base"]["stdout"],**changes)
    result=source.inspect_batch(batch_id=batch,records=signed)
    assert not result["source_prerequisites_passed"] and not result["numeric_pass"]


def test_numerical_failure_is_retained_not_dropped_from_authenticated_batch(prepared):
    store,key,batch,contexts,signed=prepared
    raw=json.loads(signed["time"]["stdout"]);raw["pressure"]=[[.002]*64 for _ in range(64)]
    signed["time"]["stdout"]=json.dumps(raw)
    signed["time"]["observation_document"]=observation(key,contexts["time"],signed["time"]["stdout"])
    result=source.inspect_batch(batch_id=batch,records=signed)
    assert result["source_prerequisites_passed"] and not result["numeric_pass"]
    assert len(result["numeric"]["checks"])==4
    assert any(not check["passed"] for check in result["numeric"]["checks"])


def test_root_revocation_expiry_manifest_change_and_fixed_namespace_refuse(prepared,monkeypatch):
    store,key,batch,contexts,signed=prepared
    monkeypatch.setattr(source.time,"time_ns",lambda:NOW+600_000_000_000)
    assert not source.inspect_batch(batch_id=batch,records=signed)["source_prerequisites_passed"]
    monkeypatch.setattr(source.time,"time_ns",lambda:NOW+5)
    original=source.manifest
    monkeypatch.setattr(source,"manifest",lambda:dict(original(),sha256="0"*64))
    assert not source.inspect_batch(batch_id=batch,records=signed)["source_prerequisites_passed"]
    monkeypatch.setattr(source,"manifest",original)
    assert source.record_policy(policy(key,revision=2,action="revoke",
        valid_from_ns=NOW-1,valid_until_ns=NOW+180_000_000_000))["recorded"]
    assert not source.inspect_batch(batch_id=batch,records=signed)["source_prerequisites_passed"]
    assert not source.record_policy(policy(key,revision=3,action="approve",
        valid_from_ns=NOW-1,valid_until_ns=NOW+180_000_000_000))["recorded"]
    from app import skill_authority as native
    with store._db_existing_write() as db:
        db.execute("BEGIN IMMEDIATE")
        ctx=contexts["base"]
        with pytest.raises(ValueError):native.inspect_connection(db,ctx["run_id"],ctx["nonce"],NOW+5)


def test_second_case_dispatch_and_transaction_failure_never_replace_old_proof(prepared):
    store,key,batch,contexts,signed=prepared
    ctx=dict(contexts["base"],run_id=str(uuid.uuid4()),nonce=uuid.uuid4().hex)
    assert not source.record_dispatch(ctx,dispatch(key,context=ctx))["recorded"]
    ctx=dict(ctx,batch_id=str(uuid.uuid4()))
    with store._db_existing_write() as db:
        db.execute("CREATE TRIGGER fault_consistency BEFORE INSERT ON skill_host_dispatches BEGIN SELECT RAISE(ABORT,'test'); END")
    assert not source.record_dispatch(ctx,dispatch(key,context=ctx))["recorded"]
    with store._db_existing_write() as db:
        assert db.execute("SELECT COUNT(*) FROM skill_host_dispatches").fetchone()[0]==4
        db.execute("DROP TRIGGER fault_consistency")
    assert source.record_dispatch(ctx,dispatch(key,context=ctx))["recorded"]


def test_disabled_and_missing_database_never_initialize(prepared,monkeypatch,tmp_path):
    store,key,batch,contexts,signed=prepared
    for flag in (False,1,"true",None):
        monkeypatch.setattr(config,"SKILL_LIBRARY_ENABLED",flag)
        assert not source.inspect_batch(batch_id=batch,records=signed)["source_prerequisites_passed"]
    monkeypatch.setattr(config,"SKILL_LIBRARY_ENABLED",True)
    missing=SkillStore(tmp_path/"missing.db");monkeypatch.setattr(service,"STORE",missing)
    assert not source.inspect_batch(batch_id=batch,records=signed)["source_prerequisites_passed"]
    assert not missing.path.exists()


@pytest.mark.parametrize("case",["base","grid","time","equivalent"])
def test_consistency_forwarder_actual_loopback_only_sends_reviewed_case(endpoint,case):
    from host_observer.forwarder import forward_consistency_request
    target,server,calls=endpoint
    result=forward_consistency_request(target,case=case,run_id=RUN,nonce=NONCE,shared_token=TOKEN)
    code=source.numerical.recipe(*source.numerical.PROFILES[case])
    assert json.loads(calls[0][1])["code"]==code
    assert result["observation"]["profile"]==source.PROFILE+"."+case
    assert result["observation"]["code_sha256"]==hashlib.sha256(code.encode()).hexdigest()
    assert all(result["observation"][k] is False for k in ("trusted_run","parameters_bound","authenticated"))


def test_arbitrary_case_is_rejected_before_network(endpoint):
    from host_observer.forwarder import forward_consistency_request
    target,server,calls=endpoint
    with pytest.raises(ValueError):
        forward_consistency_request(target,case="custom-code",run_id=RUN,nonce=NONCE,shared_token=TOKEN)
    assert calls==[]


def test_batch_case_query_has_indexed_bound_and_database_unique_guard(prepared):
    store,key,batch,contexts,signed=prepared
    sql="""SELECT 1 FROM skill_host_dispatches WHERE root_id=? AND json_valid(context_json)
        AND json_type(context_json,'$.batch_id')='text' AND json_type(context_json,'$.case')='text'
        AND json_extract(CASE WHEN json_valid(context_json) THEN context_json ELSE '{}' END,'$.batch_id')=?
        AND json_extract(CASE WHEN json_valid(context_json) THEN context_json ELSE '{}' END,'$.case')=? LIMIT 1"""
    with store._db_existing_write() as db:
        db.executemany("INSERT INTO skill_host_dispatches VALUES (?,?,?,?,?,?,?,?)",
            [(str(uuid.uuid4()),uuid.uuid4().hex,source.ROOT_ID,1,
              json.dumps(dict(batch_id=str(uuid.uuid4()),case="base")),"0"*64,"{}",NOW)
             for _ in range(10000)])
        plan=db.execute("EXPLAIN QUERY PLAN "+sql,(source.ROOT_ID,batch,"base")).fetchall()
        assert "idx_skill_dispatch_batch_case" in str([tuple(x) for x in plan])
        steps=[0]
        def progress():
            steps[0]+=100
            return int(steps[0]>1000)
        db.set_progress_handler(progress,100)
        assert db.execute(sql,(source.ROOT_ID,batch,"base")).fetchone()
        assert not db.execute(sql,(source.ROOT_ID,str(uuid.uuid4()),"base")).fetchone()
        db.set_progress_handler(None,0)
        ctx=dict(contexts["base"],run_id=str(uuid.uuid4()),nonce=uuid.uuid4().hex)
        with pytest.raises(sqlite3.IntegrityError,match="UNIQUE"):
            db.execute("INSERT INTO skill_host_dispatches VALUES (?,?,?,?,?,?,?,?)",
                (ctx["run_id"],ctx["nonce"],source.ROOT_ID,1,canonical(ctx).decode(),"a"*64,"{}",NOW))
        assert db.execute("SELECT COUNT(*) FROM skill_host_dispatches").fetchone()[0]==10004
