"""Synthetic signatures/analytic arrays only; never production provenance."""
import hashlib
import json
import uuid
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding,PublicFormat
from app import config,skill_library_service as service,skill_case_source as source
from app.skill_store import SkillStore
from host_observer import case_signing as signing
from scripts.validation import case_batch_registry as registry,attenuation_evidence as attenuation
from scripts.validation.host_forward_signature import canonical,DOMAIN as OBSERVATION_DOMAIN
from tests.unit.test_host_forwarder import IDENTITY,endpoint,RUN,NONCE,TOKEN

NOW=100_000_000_000
CASE="attenuation-coeff08"

def observation(key,ctx,stdout,**changes):
    cfg,code=registry.specification(ctx["case"])
    o=dict(version="host-forward-observation.v1",run_id=ctx["run_id"],nonce=ctx["nonce"],
        profile=source.PROFILE+"."+ctx["case"],
        request_sha256=hashlib.sha256(canonical(dict(code=code,timeout_seconds=30,request_nonce=ctx["nonce"]))).hexdigest(),
        **registry.pins(ctx["case"]),response_sha256="a"*64,stdout_sha256=hashlib.sha256(stdout.encode()).hexdigest(),
        stderr_sha256=hashlib.sha256(b"").hexdigest(),exit_code=0,timed_out=False,
        target_ip=ctx["target_ip"],target_port=ctx["target_port"],peer_ip=ctx["target_ip"],
        identity=dict(IDENTITY),observed_before_ns=NOW+1,request_start_ns=NOW+2,
        request_end_ns=NOW+3,observed_after_ns=NOW+4,
        authenticated=False,request_observed=False,parameters_bound=False,trusted_run=False)
    o.update(changes)
    body=dict(root_id=source.ROOT_ID,revision=ctx["revision"],observation=o)
    return canonical(dict(payload=body,signature=key.sign(OBSERVATION_DOMAIN+canonical(body)).hex())).decode()

@pytest.fixture
def prepared(tmp_path,monkeypatch):
    store=SkillStore(tmp_path/"skills.db");store.migrate()
    monkeypatch.setattr(service,"STORE",store)
    monkeypatch.setattr(config,"SKILL_LIBRARY_ENABLED",True)
    monkeypatch.setattr(config,"SKILL_CASE_OBSERVATION_ENABLED",True)
    monkeypatch.setattr(config,"SKILL_LEARNING_ENABLED",False)
    key=Ed25519PrivateKey.generate()
    public=key.public_key().public_bytes(Encoding.Raw,PublicFormat.Raw)
    monkeypatch.setattr(source.approved,"approved_public_key",lambda:public)
    monkeypatch.setattr(source.time,"time_ns",lambda:NOW)
    assert source.record_policy(signing.policy(key,revision=1,action="approve",valid_from_ns=NOW-1,
        valid_until_ns=NOW+600_000_000_000))["recorded"]
    ctx=dict(root_id=source.ROOT_ID,revision=1,batch_id=str(uuid.uuid4()),case=CASE,
        run_id=str(uuid.uuid4()),nonce=uuid.uuid4().hex,target_ip="127.0.0.1",target_port=8010,
        container_id=IDENTITY["container_id"],image_id=IDENTITY["image_id"],started_at=IDENTITY["started_at"],
        dispatch_not_before_ns=NOW-1,dispatch_not_after_ns=NOW+60_000_000_000)
    assert source.record_dispatch(ctx,signing.dispatch(key,context=ctx))["recorded"]
    cfg,_=registry.specification(CASE);ratio=attenuation.theory_ratio(cfg)
    raw=json.dumps(dict(raw_evidence=dict(profile="case-batch1-"+CASE,configuration=cfg,
        units=dict(pressure="Pa",attenuation="dB/(MHz^2 cm)"),pressure_complex=[[1.,0.],[ratio,0.]])))
    record=dict(observation_document=observation(key,ctx,raw),stdout=raw,stderr="")
    monkeypatch.setattr(source.time,"time_ns",lambda:NOW+5)
    return store,key,ctx,record

def test_same_db_signed_exact_case_does_not_grant_or_write_skill(prepared):
    store,key,ctx,record=prepared
    got=source.inspect_case(case=CASE,record=record)
    assert got["source_prerequisites_passed"] and got["numeric_pass"] and got["quality_level"]=="Q2"
    assert config.SKILL_LEARNING_ENABLED is False
    assert all(got[k] is False for k in source.FALSE_TRUST)
    # Inspection is repeatable; it does not claim one-use admission.
    assert source.inspect_case(case=CASE,record=record)["source_prerequisites_passed"]
    with store._read_db() as db:
        assert db.execute("SELECT count(*) FROM simulation_skills").fetchone()[0]==0
        assert db.execute("SELECT count(*) FROM skill_events").fetchone()[0]==0

@pytest.mark.parametrize("fault",["case","raw","stderr","signature","peer","profile","configuration",
    "request","identity","early","nonce","self_trust","boolean_exit","output_budget"])
def test_mismatch_or_self_report_cannot_pass(prepared,fault):
    store,key,ctx,record=prepared;case=CASE
    if fault=="case":case="base-attenuation"
    elif fault=="raw":record["stdout"]+=" "
    elif fault=="stderr":record["stderr"]="warning"
    elif fault=="signature":record["observation_document"]=record["observation_document"].replace('"signature":"','"signature":"0',1)
    elif fault=="output_budget":record["stdout"]=" "*500001
    else:
        changes={"peer":dict(peer_ip="127.0.0.2"),"profile":dict(profile="val1-plane-wave-v2"),
            "configuration":dict(configuration_sha256="0"*64),"request":dict(request_sha256="0"*64),
            "identity":dict(identity=dict(IDENTITY,image_id="sha256:"+"c"*64)),
            "early":dict(observed_before_ns=NOW-1),"nonce":dict(nonce="0"*32),
            "self_trust":dict(trusted_run=True),"boolean_exit":dict(exit_code=True)}[fault]
        record["observation_document"]=observation(key,ctx,record["stdout"],**changes)
    assert not source.inspect_case(case=case,record=record)["source_prerequisites_passed"]

def test_correct_signature_on_bad_physics_still_not_numeric_success(prepared):
    store,key,ctx,record=prepared
    raw=json.loads(record["stdout"]);raw["raw_evidence"]["pressure_complex"][1]=[1.,0.]
    record["stdout"]=json.dumps(raw);record["observation_document"]=observation(key,ctx,record["stdout"])
    got=source.inspect_case(case=CASE,record=record)
    assert got["source_prerequisites_passed"] and not got["numeric_pass"]
    assert got["quality_level"]=="Q1" and got["trusted_run"] is False

def test_current_revoke_expiry_and_manifest_are_rechecked(prepared,monkeypatch):
    store,key,ctx,record=prepared
    original=source.manifest
    monkeypatch.setattr(source,"manifest",lambda:dict(original(),sha256="0"*64))
    assert not source.inspect_case(case=CASE,record=record)["source_prerequisites_passed"]
    monkeypatch.setattr(source,"manifest",original)
    monkeypatch.setattr(source.time,"time_ns",lambda:NOW+600_000_000_000)
    assert not source.inspect_case(case=CASE,record=record)["source_prerequisites_passed"]
    monkeypatch.setattr(source.time,"time_ns",lambda:NOW+5)
    assert source.record_policy(signing.policy(key,revision=2,action="revoke",valid_from_ns=NOW-1,
        valid_until_ns=NOW+600_000_000_000))["recorded"]
    assert not source.inspect_case(case=CASE,record=record)["source_prerequisites_passed"]
    assert not source.record_policy(signing.policy(key,revision=3,action="approve",valid_from_ns=NOW-1,
        valid_until_ns=NOW+600_000_000_000))["recorded"]

def test_duplicate_batch_case_and_failed_write_do_not_replace(prepared):
    store,key,ctx,record=prepared
    ctx=dict(ctx,run_id=str(uuid.uuid4()),nonce=uuid.uuid4().hex)
    assert not source.record_dispatch(ctx,signing.dispatch(key,context=ctx))["recorded"]
    with store._db_existing_write() as db:
        assert db.execute("SELECT count(*) FROM skill_host_dispatches").fetchone()[0]==1
        db.execute("CREATE TRIGGER reject_case BEFORE INSERT ON skill_host_dispatches BEGIN SELECT RAISE(ABORT,'test'); END")
    ctx=dict(ctx,batch_id=str(uuid.uuid4()))
    assert not source.record_dispatch(ctx,signing.dispatch(key,context=ctx))["recorded"]
    assert source.inspect_case(case=CASE,record=record)["source_prerequisites_passed"]

@pytest.mark.parametrize("flag",[False,1,"true",None])
def test_manual_observation_gate_strict_disabled_does_not_touch_database(prepared,monkeypatch,flag):
    store,key,ctx,record=prepared
    monkeypatch.setattr(config,"SKILL_CASE_OBSERVATION_ENABLED",flag)
    monkeypatch.setattr(service,"STORE",object())
    assert not source.inspect_case(case=CASE,record=record)["source_prerequisites_passed"]
    assert not source.record_dispatch(ctx,signing.dispatch(key,context=ctx))["recorded"]

def test_missing_database_never_created(prepared,monkeypatch,tmp_path):
    store,key,ctx,record=prepared
    missing=SkillStore(tmp_path/"missing.db");monkeypatch.setattr(service,"STORE",missing)
    assert not source.inspect_case(case=CASE,record=record)["source_prerequisites_passed"]
    assert not source.record_policy(signing.policy(key,revision=1,action="approve",valid_from_ns=NOW-1,
        valid_until_ns=NOW+600_000_000_000))["recorded"]
    assert not missing.path.exists()

@pytest.mark.parametrize("case",registry.CASES)
def test_actual_loopback_transport_only_for_exact_registered_recipes(endpoint,case):
    from host_observer.forwarder import forward_case_request
    target,server,calls=endpoint
    got=forward_case_request(target,case=case,run_id=RUN,nonce=NONCE,shared_token=TOKEN)
    cfg,code=registry.specification(case)
    assert json.loads(calls[0][1])["code"]==code and len(calls)==1
    assert got["observation"]["configuration_sha256"]==registry.pins(case)["configuration_sha256"]
    assert got["observation"]["profile"]==source.PROFILE+"."+case

def test_unknown_case_refused_before_network(endpoint):
    from host_observer.forwarder import forward_case_request
    target,server,calls=endpoint
    with pytest.raises(ValueError):forward_case_request(target,case="arbitrary-code",run_id=RUN,nonce=NONCE,shared_token=TOKEN)
    assert calls==[]

@pytest.mark.parametrize("case",registry.CASES)
def test_closed_registry_never_accepts_boolean_exit(case):
    cfg,code=registry.specification(case);compile(code,"<case>","exec")
    result=registry.assess(case,dict(code_sha256=registry.pins(case)["code_sha256"],
        exit_code=True,timed_out=False,stderr="",stdout="{}"))
    assert not result["numeric_pass"] and result["actual_use_authenticated"] is False

def test_case_scope_cannot_be_consumed_as_old_fixed_authority(prepared):
    store,key,ctx,record=prepared
    with store._db_existing_write() as db:
        db.execute("BEGIN IMMEDIATE")
        with pytest.raises(ValueError):source.native.inspect_connection(db,ctx["run_id"],ctx["nonce"],NOW+5)

def test_verification_requires_authority_transaction(prepared):
    store,key,ctx,record=prepared
    import sqlite3
    with sqlite3.connect(store.path) as db:
        with pytest.raises(ValueError,match="authority_transaction"):
            source.verify_connection(db,case=CASE,record=record,now=NOW+5)

def test_dispatch_window_must_fit_current_root_policy(prepared):
    store,key,ctx,record=prepared
    document=signing.policy(key,revision=2,action="approve",valid_from_ns=NOW-1,valid_until_ns=NOW+10)
    assert source.record_policy(document)["recorded"]
    ctx=dict(ctx,revision=2,batch_id=str(uuid.uuid4()),run_id=str(uuid.uuid4()),nonce=uuid.uuid4().hex)
    assert not source.record_dispatch(ctx,signing.dispatch(key,context=ctx))["recorded"]

def test_runner_rejected_dispatch_stops_before_http(endpoint,monkeypatch):
    from host_observer.case_runner import observe_case
    target,server,calls=endpoint
    monkeypatch.setattr(config,"SKILL_LIBRARY_ENABLED",True)
    monkeypatch.setattr(config,"SKILL_CASE_OBSERVATION_ENABLED",True)
    monkeypatch.setattr(config,"SKILL_LEARNING_ENABLED",False)
    monkeypatch.setattr(signing,"dispatch",lambda *a,**k:"unsigned-test-stub")
    monkeypatch.setattr(source,"record_dispatch",lambda *a,**k:dict(recorded=False))
    result=observe_case(target,case=CASE,key=None,shared_token=TOKEN,revision=1)
    assert result["stage"]=="dispatch" and calls==[] and not result["source_prerequisites_passed"]

def test_runner_missing_policy_stops_before_http(endpoint,prepared,monkeypatch):
    from host_observer.case_runner import observe_case
    target,server,calls=endpoint
    store,key,ctx,record=prepared
    empty=SkillStore(store.path.parent/"empty.db");empty.migrate()
    monkeypatch.setattr(service,"STORE",empty)
    result=observe_case(target,case=CASE,key=key,shared_token=TOKEN,revision=1)
    assert result["stage"]=="dispatch" and calls==[]

def test_runner_execution_error_is_redacted_and_no_retry(endpoint,prepared,monkeypatch):
    from host_observer.case_runner import observe_case
    from host_observer import forwarder
    target,server,calls=endpoint
    store,key,ctx,record=prepared
    invoked=[]
    def broken(*a,**k):
        invoked.append(True);raise RuntimeError("secret-"+TOKEN)
    monkeypatch.setattr(forwarder,"forward_case_request",broken)
    result=observe_case(target,case=CASE,key=key,shared_token=TOKEN,revision=1)
    assert invoked==[True] and result["stage"]=="execution" and TOKEN not in json.dumps(result)
    assert result["dispatch_recorded"] is True
    with store._read_db() as db:
        assert db.execute("SELECT count(*) FROM skill_host_dispatches").fetchone()[0]==2

def test_unapproved_key_stops_before_any_dispatch_or_http(endpoint,prepared):
    from host_observer.case_runner import observe_case
    target,server,calls=endpoint
    store,key,ctx,record=prepared
    result=observe_case(target,case=CASE,key=Ed25519PrivateKey.generate(),shared_token=TOKEN,revision=1)
    assert result["stage"]=="dispatch" and calls==[]
    with store._read_db() as db:
        assert db.execute("SELECT count(*) FROM skill_host_dispatches").fetchone()[0]==1

@pytest.mark.parametrize("revoke_during_request",[False,True])
def test_runner_actual_tcp_signature_and_current_root_recheck(endpoint,prepared,monkeypatch,revoke_during_request):
    import itertools
    from host_observer.case_runner import observe_case
    from host_observer import forwarder
    target,server,calls=endpoint
    store,key,ctx,record=prepared
    clock=itertools.count(NOW+6,1000)
    monkeypatch.setattr(source.time,"time_ns",lambda:next(clock))
    server.raw=json.dumps(dict(stdout=record["stdout"],stderr="",exit_code=0,timed_out=False)).encode()
    original=forwarder.forward_case_request
    if revoke_during_request:
        def revoking(*args,**kwargs):
            result=original(*args,**kwargs)
            assert source.record_policy(signing.policy(key,revision=2,action="revoke",
                valid_from_ns=NOW-1,valid_until_ns=NOW+600_000_000_000))["recorded"]
            return result
        monkeypatch.setattr(forwarder,"forward_case_request",revoking)
    got=observe_case(target,case=CASE,key=key,shared_token=TOKEN,revision=1)
    assert len(calls)==1 and got["source_prerequisites_passed"] is (not revoke_during_request)
    assert got["numeric_pass"] is (not revoke_during_request)
    assert got["admitted"] is False and got["recorded"] is False and got["dispatch_recorded"] is True
    assert all(got[k] is False for k in source.FALSE_TRUST)

def test_atomic_candidate_has_exact_parameters_and_idempotent_source(prepared,monkeypatch):
    from app import skill_case_candidate as candidate
    store,key,ctx,record=prepared
    monkeypatch.setattr(store,"migrate",lambda:(_ for _ in ()).throw(AssertionError("must not migrate")))
    first=candidate.accumulate_candidate(case=CASE,record=record)
    second=candidate.accumulate_candidate(case=CASE,record=record)
    assert first["recorded"] and second["skill_id"]==first["skill_id"]
    assert first["admitted"] is False and all(first[k] is False for k in source.FALSE_TRUST)
    with store._read_db() as db:
        row=db.execute("SELECT * FROM simulation_skills").fetchone()
        version=db.execute("SELECT * FROM skill_versions").fetchone()
        assert row["status"]=="candidate" and row["quality_level"]=="Q2" and row["success_count"]==1
        assert json.loads(version["normalized_params"])==candidate.parameters(CASE)
        assert db.execute("SELECT count(*) FROM skill_events").fetchone()[0]==1

def test_candidate_insert_failure_rolls_back_entire_group(prepared):
    from app import skill_case_candidate as candidate
    store,key,ctx,record=prepared
    with store._db_existing_write() as db:
        db.execute("CREATE TRIGGER fail_case_event BEFORE INSERT ON skill_events BEGIN SELECT RAISE(ABORT,'test'); END")
    assert not candidate.accumulate_candidate(case=CASE,record=record)["recorded"]
    with store._read_db() as db:
        for table in ("simulation_skills","skill_versions","skill_events"):
            assert db.execute("SELECT count(*) FROM "+table).fetchone()[0]==0
        assert db.execute("SELECT count(*) FROM skill_host_dispatches").fetchone()[0]==1

@pytest.mark.parametrize("fault",["physics","revoked","raw_change"])
def test_source_or_physics_failure_never_creates_candidate(prepared,fault):
    from app import skill_case_candidate as candidate
    store,key,ctx,record=prepared
    if fault=="physics":
        raw=json.loads(record["stdout"]);raw["raw_evidence"]["pressure_complex"][1]=[1.,0.]
        record["stdout"]=json.dumps(raw)
        record["observation_document"]=observation(key,ctx,record["stdout"])
    elif fault=="raw_change":record["stdout"]+=" "
    else:
        assert source.record_policy(signing.policy(key,revision=2,action="revoke",valid_from_ns=NOW-1,
            valid_until_ns=NOW+600_000_000_000))["recorded"]
    assert not candidate.accumulate_candidate(case=CASE,record=record)["recorded"]
    with store._read_db() as db:assert db.execute("SELECT count(*) FROM simulation_skills").fetchone()[0]==0

def test_root_writer_cannot_interleave_source_check_and_candidate_insert(prepared,monkeypatch):
    import sqlite3
    from app import skill_case_candidate as candidate
    store,key,ctx,record=prepared
    original=store.create_candidate;blocked=[]
    def under_lock(**kwargs):
        with sqlite3.connect(store.path,timeout=0) as other:
            with pytest.raises(sqlite3.OperationalError,match="locked"):
                other.execute("BEGIN IMMEDIATE")
            blocked.append(True)
        return original(**kwargs)
    monkeypatch.setattr(store,"create_candidate",under_lock)
    assert candidate.accumulate_candidate(case=CASE,record=record)["recorded"]
    assert blocked==[True]

def test_candidate_private_connection_must_match_store(prepared,tmp_path):
    from app import skill_case_candidate as candidate
    store,key,ctx,record=prepared
    other=SkillStore(tmp_path/"other.db");other.migrate()
    with other._db_existing_write() as db:
        db.execute("BEGIN IMMEDIATE")
        with pytest.raises(ValueError,match="database_mismatch"):
            store.create_candidate(scenario_type="attenuation_helmholtz_2d",state_text="test",normalized_params={},
                code_template="pass",postconditions={},physics_evidence={},source_run_id="test",
                validator_version="test",quality_level="Q2",_existing_connection=db)

def test_root_expiry_before_candidate_commit_rolls_back(prepared,monkeypatch):
    from app import skill_case_candidate as candidate
    store,key,ctx,record=prepared
    original=store.create_candidate
    def expire_after_insert(**kwargs):
        result=original(**kwargs)
        monkeypatch.setattr(source.time,"time_ns",lambda:NOW+600_000_000_000)
        return result
    monkeypatch.setattr(store,"create_candidate",expire_after_insert)
    assert not candidate.accumulate_candidate(case=CASE,record=record)["recorded"]
    with store._read_db() as db:
        for table in ("simulation_skills","skill_versions","skill_events"):
            assert db.execute("SELECT count(*) FROM "+table).fetchone()[0]==0

@pytest.mark.parametrize("fault",["none","revoked","physics","audit_failure"])
def test_fresh_tcp_observation_to_atomic_candidate(endpoint,prepared,monkeypatch,fault):
    import itertools
    from host_observer.case_runner import observe_and_accumulate_candidate
    from host_observer import forwarder
    store,key,ctx,record=prepared
    target,server,calls=endpoint
    clock=itertools.count(NOW+6,1000)
    monkeypatch.setattr(source.time,"time_ns",lambda:next(clock))
    raw=json.loads(record["stdout"])
    if fault=="physics":raw["raw_evidence"]["pressure_complex"][1]=[1.,0.]
    server.raw=json.dumps(dict(stdout=json.dumps(raw),stderr="",exit_code=0,timed_out=False)).encode()
    if fault=="audit_failure":
        with store._db_existing_write() as db:
            db.execute("CREATE TRIGGER fail_pipeline_event BEFORE INSERT ON skill_events BEGIN SELECT RAISE(ABORT,'test'); END")
    original=forwarder.forward_case_request
    if fault=="revoked":
        def revoked_after_http(*a,**k):
            got=original(*a,**k)
            assert source.record_policy(signing.policy(key,revision=2,action="revoke",
                valid_from_ns=NOW-1,valid_until_ns=NOW+600_000_000_000))["recorded"]
            return got
        monkeypatch.setattr(forwarder,"forward_case_request",revoked_after_http)
    got=observe_and_accumulate_candidate(target,case=CASE,key=key,shared_token=TOKEN,revision=1)
    success=fault=="none"
    assert len(calls)==1 and got["recorded"] is success and got["dispatch_recorded"] is True
    assert got["stage"]==("complete" if success else "candidate")
    assert got["admitted"] is False and all(got[k] is False for k in source.FALSE_TRUST)
    assert TOKEN not in json.dumps(got) and "observation_document" not in got and "stdout" not in got
    with store._read_db() as db:
        for table in ("simulation_skills","skill_versions","skill_events"):
            assert db.execute("SELECT count(*) FROM "+table).fetchone()[0]==int(success)
        assert db.execute("SELECT count(*) FROM skill_host_dispatches").fetchone()[0]==2

@pytest.mark.parametrize("fault",["disabled","missing_db","wrong_key"])
def test_candidate_coordinator_preflight_never_calls_http(endpoint,prepared,monkeypatch,tmp_path,fault):
    from host_observer.case_runner import observe_and_accumulate_candidate
    store,key,ctx,record=prepared
    target,server,calls=endpoint
    if fault=="disabled":monkeypatch.setattr(config,"SKILL_CASE_OBSERVATION_ENABLED",False)
    elif fault=="missing_db":monkeypatch.setattr(service,"STORE",SkillStore(tmp_path/"never-created.db"))
    else:key=Ed25519PrivateKey.generate()
    got=observe_and_accumulate_candidate(target,case=CASE,key=key,shared_token=TOKEN,revision=1)
    assert not got["recorded"] and not got["dispatch_recorded"] and calls==[]
    assert not (tmp_path/"never-created.db").exists()

@pytest.mark.parametrize("fault",["archived","observation_disabled"])
def test_candidate_pipeline_rechecks_lifecycle_and_gate_after_http(endpoint,prepared,monkeypatch,fault):
    import itertools
    from host_observer.case_runner import observe_and_accumulate_candidate
    from host_observer import forwarder
    from app import skill_case_candidate as candidate
    store,key,ctx,record=prepared
    target,server,calls=endpoint
    first=candidate.accumulate_candidate(case=CASE,record=record)
    assert first["recorded"]
    clock=itertools.count(NOW+6,1000)
    monkeypatch.setattr(source.time,"time_ns",lambda:next(clock))
    server.raw=json.dumps(dict(stdout=record["stdout"],stderr="",exit_code=0,timed_out=False)).encode()
    original=forwarder.forward_case_request
    def changed_after_http(*a,**k):
        got=original(*a,**k)
        if fault=="archived":
            with store._read_db() as db:
                stamp=db.execute("SELECT updated_at FROM simulation_skills WHERE skill_id=?",(first["skill_id"],)).fetchone()[0]
            store.set_archived(first["skill_id"],archived=True,expected_updated_at=stamp,
                actor="internal-system",reason="test owner withdrawal")
        else:monkeypatch.setattr(config,"SKILL_CASE_OBSERVATION_ENABLED",False)
        return got
    monkeypatch.setattr(forwarder,"forward_case_request",changed_after_http)
    got=observe_and_accumulate_candidate(target,case=CASE,key=key,shared_token=TOKEN,revision=1)
    assert len(calls)==1 and not got["recorded"] and got["dispatch_recorded"] and got["stage"]=="candidate"
    with store._read_db() as db:
        assert db.execute("SELECT count(*) FROM simulation_skills").fetchone()[0]==1
        assert db.execute("SELECT count(*) FROM skill_versions").fetchone()[0]==1
        assert db.execute("SELECT success_count FROM simulation_skills").fetchone()[0]==1
        assert db.execute("SELECT count(*) FROM skill_events").fetchone()[0]==(2 if fault=="archived" else 1)

def test_case_candidate_feedback_keeps_original_identity_after_root_revoke(prepared):
    from app.skill_case_candidate import accumulate_candidate
    store,key,ctx,record=prepared
    result=accumulate_candidate(case=CASE,record=record)
    assert result["recorded"]
    sid=result["skill_id"]
    with store._read_db() as db:
        row=db.execute("SELECT physics_evidence FROM skill_versions WHERE skill_id=?",(sid,)).fetchone()
        evidence=json.loads(row[0])
    assert evidence["root_id"]==source.ROOT_ID and evidence["revision"]==ctx["revision"]
    assert evidence["run_id"]==ctx["run_id"] and evidence["nonce"]==ctx["nonce"]
    assert source.record_policy(signing.policy(key,revision=2,action="revoke",valid_from_ns=NOW-1,valid_until_ns=NOW+600_000_000_000))["recorded"]
    assert store.record_feedback_statement(sid,ctx["run_id"],confirmed=True,
        request_id="case-confirmation",actor="portal-admin",reason="confirm historical evidence")["recorded"]
    response=store.record_feedback_statement(sid,ctx["run_id"],confirmed=False,
        request_id="case-withdrawal",actor="portal-admin",reason="withdraw historical evidence")
    assert response["recorded"]
    with store._read_db() as db:
        event=db.execute("SELECT detail FROM skill_events WHERE skill_id=? AND event_type='feedback_statement' ORDER BY id DESC LIMIT 1",(sid,)).fetchone()
    linked=json.loads(event[0])["source_association"]
    assert linked["version"]==1 and linked["actual_use_authenticated"] is False
    assert store.get_skill(sid)["status"]!="active"
