import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
import pytest
from app import config,skill_host_intake as intake,skill_authority as authority
from scripts.validation import host_forward_signature as signature
from scripts.validation import plane_wave_run_binding as pins
from tests.unit.test_skill_authority import prepared,root,context,approval
from tests.unit.test_plane_wave_evidence import evidence
from tests.unit.test_host_forwarder import IDENTITY

@pytest.fixture
def ready(prepared,monkeypatch):
    store,key=prepared;ctx=context()
    monkeypatch.setattr(config,"SKILL_LEARNING_ENABLED",True)
    assert authority.record_root_statement(root(key))["recorded"]
    assert authority.record_dispatch(ctx,approval(key,ctx))["recorded"]
    code=(pins.DIRECTORY/"recipes/val1_plane_wave_v2.py").read_text().strip()
    sid=store.create_candidate(scenario_type="plane_wave_2d",state_text="native fixture",
        normalized_params=intake.controlled_dispatch.fixed_retrieval_parameters(),code_template=code,postconditions={},
        preconditions={"dimensions":2,"dx_m":[0.00025,0.00025]},physics_evidence={},
        source_run_id=ctx["run_id"],validator_version="fixture",quality_level="Q2")
    stdout=json.dumps(evidence(),separators=(",",":"))
    obs=dict(version="host-forward-observation.v1",run_id=ctx["run_id"],nonce=ctx["nonce"],
        profile=authority.approved.PROFILE,code_sha256=pins.RECIPE_SHA256,
        configuration_sha256=authority.fixed_scope()["configuration_sha256"],
        request_sha256=hashlib.sha256(signature.canonical({"code":code,"timeout_seconds":30,"request_nonce":ctx["nonce"]})).hexdigest(),
        stdout_sha256=pins.digest(stdout),stderr_sha256=pins.digest(""),response_sha256="d"*64,
        exit_code=0,timed_out=False,target_ip=ctx["target_ip"],target_port=ctx["target_port"],
        peer_ip=ctx["target_ip"],identity=dict(IDENTITY),
        observed_before_ns=100_100_000_000,request_start_ns=100_200_000_000,
        request_end_ns=100_300_000_000,observed_after_ns=100_400_000_000,
        authenticated=False,request_observed=False,parameters_bound=False,trusted_run=False)
    monkeypatch.setattr(authority.time,"time_ns",lambda:100_500_000_000)
    def document():
        payload={"root_id":ctx["root_id"],"revision":ctx["revision"],"observation":obs}
        return signature.canonical({"payload":payload,"signature":key.sign(signature.DOMAIN+signature.canonical(payload)).hex()})
    args=dict(skill_id=sid,version=1,observation_document=document(),stdout=stdout,stderr="")
    return store,key,ctx,obs,args,document

def test_native_candidate_link_is_once_and_never_promotes(ready):
    store,key,ctx,obs,args,doc=ready
    result=intake.record(**args)
    assert result["recorded"] and result["quality_level"]=="Q2"
    assert not any(result[k] for k in intake.TRUST)
    assert not intake.record(**args)["recorded"]
    assert store.get_skill(args["skill_id"])["status"]=="candidate"
    with store._read_db() as db:
        assert db.execute("SELECT count(*) FROM skill_host_verifications").fetchone()[0]==1
        assert db.execute("SELECT count(*) FROM skill_events WHERE event_type='host_candidate_evidence'").fetchone()[0]==1

def test_native_root_revocation_rechecked_in_consumption(ready):
    store,key,ctx,obs,args,doc=ready
    assert authority.record_root_statement(root(key,2,"revoke"))["recorded"]
    assert not intake.record(**args)["recorded"]
    with store._read_db() as db:assert db.execute("SELECT count(*) FROM skill_host_verifications").fetchone()[0]==0

@pytest.mark.parametrize("field,value",[
    ("peer_ip","127.0.0.2"),("code_sha256","0"*64),("configuration_sha256","0"*64),
    ("timed_out",True),("request_start_ns",99_000_000_000),("profile","other")])
def test_signed_observation_still_requires_actual_bindings(ready,field,value):
    store,key,ctx,obs,args,doc=ready
    obs[field]=value;args["observation_document"]=doc()
    assert not intake.record(**args)["recorded"]

def test_numerical_failure_preserves_source_and_has_no_receipt(ready):
    store,key,ctx,obs,args,doc=ready
    raw=json.loads(args["stdout"]);raw["raw_evidence"]["pressure_x2"]=[0]*3400
    args["stdout"]=json.dumps(raw,separators=(",",":"));obs["stdout_sha256"]=pins.digest(args["stdout"])
    args["observation_document"]=doc()
    before=store.get_skill(args["skill_id"])
    assert not intake.record(**args)["recorded"]
    assert store.get_skill(args["skill_id"])==before

def test_second_insert_failure_rolls_back_event_and_receipt(ready):
    store,key,ctx,obs,args,doc=ready
    with store._db_existing_write() as db:
        db.execute("CREATE TRIGGER injected BEFORE INSERT ON skill_host_verifications BEGIN SELECT RAISE(ABORT,'injected'); END")
    assert not intake.record(**args)["recorded"]
    with store._db_existing_write() as db:
        assert db.execute("SELECT count(*) FROM skill_events WHERE event_type='host_candidate_evidence'").fetchone()[0]==0
        assert db.execute("SELECT count(*) FROM skill_host_verifications").fetchone()[0]==0
        db.execute("DROP TRIGGER injected")
    assert intake.record(**args)["recorded"]

def test_native_consumption_concurrent_exactly_once(ready):
    store,key,ctx,obs,args,doc=ready
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda _:intake.record(**args),range(2)))
    assert sum(r["recorded"] for r in results)==1

def test_disabled_intake_no_database(tmp_path,monkeypatch):
    from app.skill_store import SkillStore, SCHEMA_VERSION
    store=SkillStore(tmp_path/"missing.db")
    monkeypatch.setattr(intake.skill_library_service,"STORE",store)
    monkeypatch.setattr(config,"SKILL_LIBRARY_ENABLED",False)
    assert not intake.record(skill_id="x",version=1,observation_document=b"",stdout="",stderr="")["recorded"]
    assert not store.path.exists()

def test_feedback_links_recorded_host_digest_but_not_actual_use(ready):
    store,key,ctx,obs,args,doc=ready
    assert intake.record(**args)["recorded"]
    sid=args["skill_id"]
    assert store.record_feedback_statement(sid,ctx["run_id"],confirmed=True,
        request_id="confirmed",actor="portal-admin",reason="test")["recorded"]
    assert authority.record_root_statement(root(key,2,"revoke"))["recorded"]
    # Withdrawal can still describe historic evidence after root revocation;
    # it cannot turn that historical receipt into a current authorization.
    assert store.record_feedback_statement(sid,ctx["run_id"],confirmed=False,
        request_id="withdrawn",actor="portal-admin",reason="test")["recorded"]
    with store._read_db() as db:
        rows=db.execute("SELECT detail FROM skill_events WHERE event_type='feedback_statement'").fetchall()
    for row in rows:
        linked=json.loads(row[0])["source_association"]
        assert linked["host_recorded_diagnostic"]["stdout_sha256"]==pins.digest(args["stdout"])
        assert not linked["host_recorded_diagnostic"]["trust_verified"]
        assert not linked["actual_use_authenticated"]
    assert store.get_skill(sid)["status"]=="candidate"

def test_old_confirmation_retry_does_not_rewrite_new_host_evidence(ready):
    store,key,ctx,obs,args,doc=ready;sid=args["skill_id"]
    first=store.record_feedback_statement(sid,ctx["run_id"],confirmed=True,
        request_id="before",actor="portal-admin",reason="test")
    assert intake.record(**args)["recorded"]
    assert store.record_feedback_statement(sid,ctx["run_id"],confirmed=True,
        request_id="before",actor="portal-admin",reason="test")["duplicate"]
    with store._read_db() as db:
        old=json.loads(db.execute("SELECT detail FROM skill_events WHERE id=?",(first["event_id"],)).fetchone()[0])
    assert "host_recorded_diagnostic" not in old["source_association"]

def test_candidate_to_fallback_withdrawal_backup_is_one_diagnostic_chain(ready,tmp_path,monkeypatch):
    import sqlite3
    from app import skill_retrieval
    from app.skill_store import SkillStore, SCHEMA_VERSION
    store,key,ctx,obs,args,doc=ready
    assert intake.record(**args)["recorded"]
    for flag in ("SKILL_RETRIEVAL_ENABLED","SKILL_INJECTION_ENABLED"):
        monkeypatch.setattr(config,flag,True)
    params=json.dumps({"dimensions":2,"dx_m":0.00025,"domain_N":[384,384]})
    generated=skill_retrieval.generation_context("unchanged generation input",
        scenario_type="plane_wave_2d",parameters_json=params)
    assert generated["context"]=="unchanged generation input"
    assert generated["skill_usage"]["skills"]==[]
    assert generated["skill_usage"]["audit_recorded"]
    assert not any(generated["skill_usage"][field] for field in intake.TRUST)
    sid=args["skill_id"]
    assert store.record_feedback_statement(sid,ctx["run_id"],confirmed=True,
        request_id="chain-confirm",actor="portal-admin",reason="test")["recorded"]
    backup=tmp_path/"snapshot.db"
    store.backup_to(backup)
    assert store.record_feedback_statement(sid,ctx["run_id"],confirmed=False,
        request_id="chain-withdraw",actor="portal-admin",reason="test")["recorded"]
    restored=SkillStore(backup)
    assert restored.get_skill(sid)["status"]=="candidate"
    with restored._read_db() as db:
        assert db.execute("PRAGMA quick_check").fetchone()[0]=="ok"
        assert db.execute("SELECT max(version) FROM schema_migrations").fetchone()[0]==SCHEMA_VERSION
        assert db.execute("SELECT count(*) FROM skill_host_verifications").fetchone()[0]==1
        assert db.execute("SELECT count(*) FROM skill_generation_consumptions").fetchone()[0]==1
        assert db.execute("SELECT count(*) FROM skill_events WHERE event_type='feedback_statement'").fetchone()[0]==1
        old=json.loads(db.execute("SELECT detail FROM skill_events WHERE event_type='feedback_statement'").fetchone()[0])
        assert not old["source_association"]["actual_use_authenticated"]
        with pytest.raises(sqlite3.DatabaseError):
            db.execute("DELETE FROM skill_generation_consumptions")
    with store._read_db() as db:
        assert db.execute("SELECT count(*) FROM skill_events WHERE event_type='feedback_statement'").fetchone()[0]==2
    with pytest.raises(FileExistsError):store.backup_to(backup)
    assert store.get_skill(sid)["status"]=="candidate"

@pytest.mark.parametrize("failure",["expired","disabled","manual_disabled"])
def test_receipt_expiry_or_gate_change_rolls_back_both_writes(ready,monkeypatch,failure):
    store,key,ctx,obs,args,doc=ready
    if failure=="manual_disabled":
        monkeypatch.setattr(config,"SKILL_MANUAL_INTAKE_ENABLED",True)
        monkeypatch.setattr(config,"SKILL_LEARNING_ENABLED",False)
    original=intake.verify_connection
    def change_after_verification(*a,**kw):
        checked=original(*a,**kw)
        if failure=="expired":
            monkeypatch.setattr(intake.time,"time_ns",lambda:180_000_000_000)
        elif failure=="manual_disabled":
            monkeypatch.setattr(config,"SKILL_MANUAL_INTAKE_ENABLED",False)
        else:
            monkeypatch.setattr(config,"SKILL_LEARNING_ENABLED",False)
        return checked
    monkeypatch.setattr(intake,"verify_connection",change_after_verification)
    assert not intake.record(**args)["recorded"]
    with store._read_db() as db:
        assert db.execute("SELECT count(*) FROM skill_host_verifications").fetchone()[0]==0
        assert db.execute("SELECT count(*) FROM skill_events WHERE event_type='host_candidate_evidence'").fetchone()[0]==0
