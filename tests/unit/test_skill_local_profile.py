import copy,hashlib,json,sqlite3,uuid
from pathlib import Path
import pytest
from app import config,dashboard,executor_evidence,skill_library_service as service
from app import skill_local_profile as local,skill_retrieval,skill_generation_decision as decisions
from app.skill_store import SkillStore
from scripts.validation.host_forward_signature import canonical
from tests.unit.test_plane_wave_evidence import evidence
from tests.unit.test_executor_evidence import RUNTIME

def test_executor_source_pin_matches_packaged_executor():
    source=Path(__file__).resolve().parents[2]/"executor"/"executor.py"
    packaged=source.read_text(encoding="utf-8").encode("utf-8")
    assert local.EXECUTOR_SOURCE_SHA256==hashlib.sha256(packaged).hexdigest()

@pytest.fixture
def ready(tmp_path,monkeypatch):
    store=SkillStore(tmp_path/"skills.db");store.migrate()
    monkeypatch.setattr(service,"STORE",store)
    monkeypatch.setattr(dashboard,"DB_PATH",str(tmp_path/"history.db"));dashboard.init_db()
    for flag in ("SKILL_LIBRARY_ENABLED","SKILL_RETRIEVAL_ENABLED","SKILL_INJECTION_ENABLED","SKILL_MANUAL_INTAKE_ENABLED"):
        monkeypatch.setattr(config,flag,True)
    monkeypatch.setattr(config,"SKILL_LEARNING_ENABLED",False)
    code=local._recipe();stdout=json.dumps(dict(evidence(),case="1 平面波振幅守恒"),separators=(",",":"));nonce=uuid.uuid4().hex
    runtime=copy.deepcopy(RUNTIME);runtime["executor_source_sha256"]=local.EXECUTOR_SOURCE_SHA256
    receipt=dict(version="executor-receipt.v1",request_nonce=nonce,code_sha256=local.pins.RECIPE_SHA256,
        stdout_sha256=local.pins.digest(stdout),stderr_sha256=local.pins.digest(""),exit_code=0,timed_out=False,
        timeout_seconds=30,runtime=runtime,runtime_sha256=local.pins.digest(canonical(runtime).decode()))
    dispatch=dict(version="local-dispatch.v1",source_kind="managed_executor_transport",
        request_sha256=local.pins.digest(canonical(dict(code=code,timeout_seconds=30,request_nonce=nonce)).decode()),
        code_sha256=local.pins.RECIPE_SHA256,request_nonce=nonce,timeout_seconds=30,
        target_url="http://jwave-executor:8010/execute",trust_boundary="gateway_and_internal_executor",
        independent_host_authenticated=False,receipt_correlated=True)
    run=str(uuid.uuid4())
    dashboard.record_execution("run_jwave_code",code,0,False,1,stdout,"",run_id=run,
        execution_evidence=dict(receipt=receipt,local_dispatch=dispatch))
    return store,run

def generate():
    return skill_retrieval.generation_context("original",scenario_type=local.SCENE,
        parameters_json=canonical(local.controlled_dispatch.fixed_retrieval_parameters()).decode(),_existing_store=True)

def test_no_signing_setup_local_accept_search_generate_and_withdraw(ready):
    store,run=ready
    result=local.record(run);assert result["admitted"]
    assert not config.SKILL_LEARNING_ENABLED
    params=canonical(local.controlled_dispatch.fixed_retrieval_parameters()).decode()
    found=skill_retrieval.search(local.SCENE,params)
    assert len(found["skills"])==1 and found["skills"][0]["scope"]==local.PROFILE
    generated=generate();usage=generated["skill_usage"]
    assert not usage["fallback"]
    reference=json.loads(generated["context"].split("\n",3)[-1])
    assert reference["code"]==local._recipe()
    args=dict(decision_id=usage["decision_id"],generation_run_id=usage["generation_intent_id"],
        scenario_type=local.SCENE,parameters_json=params,original_context="original")
    assert local.read_context(**args)==generated["context"]
    from app.skill_reference_consumer import read_committed_context
    assert read_committed_context(**args)==generated["context"]
    store.record_negative_feedback(result["skill_id"],run,actor="portal-admin",reason="incorrect result")
    assert skill_retrieval.search(local.SCENE,params)["skills"]==[]
    assert local.read_context(**args)=="original"
    assert generate()["context"]=="original"

@pytest.mark.parametrize("fault",["wrong_code","wrong_nonce","physics","runtime","other_endpoint","duplicate_run"])
def test_record_rejects_faults_even_with_exit_zero(ready,fault):
    store,run=ready
    with sqlite3.connect(dashboard.DB_PATH) as db:
        row=db.execute("SELECT code,stdout,executor_evidence_json FROM executions WHERE run_id=?",(run,)).fetchone()
        code,stdout,evidence_json=row;proof=json.loads(evidence_json)
        if fault=="wrong_code":db.execute("UPDATE executions SET code='print(1)' WHERE run_id=?",(run,))
        elif fault=="wrong_nonce":
            proof["dispatch"]={} if "dispatch" in proof else {}
            proof["local_dispatch"]["request_nonce"]="a"*32
        elif fault=="physics":
            raw=json.loads(stdout);raw["raw_evidence"]["pressure_x2"]=[0]*3400
            stdout=json.dumps(raw,separators=(",",":"))
            proof["receipt"]["stdout_sha256"]=local.pins.digest(stdout)
            db.execute("UPDATE executions SET stdout=? WHERE run_id=?",(stdout,run))
        elif fault=="runtime":
            proof["receipt"]["runtime"]["executor_source_sha256"]="0"*64
            proof["receipt"]["runtime_sha256"]=local.pins.digest(canonical(proof["receipt"]["runtime"]).decode())
        elif fault=="other_endpoint":proof["local_dispatch"]["target_url"]="http://external/execute"
        elif fault=="duplicate_run":
            db.execute("""INSERT INTO executions(timestamp,tool_name,code,exit_code,timed_out,stdout,stderr,attempt_count,run_id)
                SELECT timestamp,tool_name,code,exit_code,timed_out,stdout,stderr,attempt_count,run_id FROM executions WHERE run_id=?""",(run,))
        db.execute("UPDATE executions SET executor_evidence_json=? WHERE run_id=?",(json.dumps(proof),run))
    assert not local.record(run)["recorded"]
    with store._read_db() as db:assert db.execute("SELECT count(*) FROM skill_local_sources").fetchone()[0]==0

def test_idempotent_and_append_only(ready):
    store,run=ready
    assert local.record(run)["admitted"]
    assert local.record(run)["duplicate"]
    with store._db_existing_write() as db:
        assert db.execute("SELECT count(*) FROM skill_local_sources").fetchone()[0]==1
        with pytest.raises(sqlite3.Error):db.execute("UPDATE skill_local_sources SET capsule_sha256='bad'")
        with pytest.raises(sqlite3.Error):db.execute("DELETE FROM skill_local_sources")

def test_atomic_failure_no_active_or_partial_candidate(ready):
    store,run=ready
    with store._db_existing_write() as db:
        db.execute("CREATE TRIGGER failure BEFORE INSERT ON skill_local_sources BEGIN SELECT RAISE(ABORT,'failure'); END")
    assert not local.record(run)["recorded"]
    with store._read_db() as db:
        assert db.execute("SELECT count(*) FROM simulation_skills").fetchone()[0]==0
        assert db.execute("SELECT count(*) FROM skill_events").fetchone()[0]==0

def test_decision_one_use(ready,monkeypatch):
    store,run=ready;assert local.record(run)["admitted"]
    params=canonical(local.controlled_dispatch.fixed_retrieval_parameters()).decode()
    args=dict(scenario_type=local.SCENE,parameters_json=params,original_context="original")
    prepared=decisions.prepare(**args,_existing_store=True)
    call=dict(args,decision_id=prepared["decision_id"],nonce=prepared["nonce"],generation_run_id=str(uuid.uuid4()))
    assert local.consume(**call)["consumed"]
    assert not local.consume(**call)["consumed"]
    assert not local.consume(**dict(call,nonce="0"*32))["consumed"]

def test_disabled_does_not_create_missing_store(tmp_path,monkeypatch):
    store=SkillStore(tmp_path/"missing.db");monkeypatch.setattr(service,"STORE",store)
    monkeypatch.setattr(config,"SKILL_LIBRARY_ENABLED",False)
    assert not local.record("missing")["recorded"]
    assert not store.path.exists()

def test_expiry_before_commit_rolls_back_consumption(ready,monkeypatch):
    store,run=ready;assert local.record(run)["admitted"]
    params=canonical(local.controlled_dispatch.fixed_retrieval_parameters()).decode()
    args=dict(scenario_type=local.SCENE,parameters_json=params,original_context="original")
    prepared=decisions.prepare(**args,_existing_store=True)
    with store._read_db() as db:
        row=db.execute("SELECT created_ns,expires_ns FROM skill_generation_decisions WHERE decision_id=?",(prepared["decision_id"],)).fetchone()
    clock=iter([row["created_ns"]+1,row["expires_ns"]])
    monkeypatch.setattr(local.time,"time_ns",lambda:next(clock))
    result=local.consume(**args,decision_id=prepared["decision_id"],nonce=prepared["nonce"],generation_run_id=str(uuid.uuid4()))
    assert not result["consumed"]
    with store._read_db() as db:
        assert db.execute("SELECT count(*) FROM skill_generation_consumptions").fetchone()[0]==0

def test_fixed_output_rejects_arbitrary_display_text(ready):
    store,run=ready
    with sqlite3.connect(dashboard.DB_PATH) as db:
        row=db.execute("SELECT stdout,executor_evidence_json FROM executions WHERE run_id=?",(run,)).fetchone()
        raw=json.loads(row[0]);raw["case"]="arbitrary secret text"
        stdout=json.dumps(raw);proof=json.loads(row[1]);proof["receipt"]["stdout_sha256"]=local.pins.digest(stdout)
        db.execute("UPDATE executions SET stdout=?,executor_evidence_json=? WHERE run_id=?",(stdout,json.dumps(proof),run))
    assert not local.record(run)["admitted"]

def test_operator_init_never_migrates_existing_database(ready):
    from scripts.skill_library.local_basic import initialize
    store,run=ready
    before=store.path.read_bytes()
    assert initialize()["ready"] and not initialize()["created"]
    assert before==store.path.read_bytes()

def test_operator_seed_does_not_rerun_available_or_withdrawn(ready,monkeypatch):
    from scripts.skill_library.local_basic import seed
    store,run=ready;accepted=local.record(run);assert accepted["admitted"]
    monkeypatch.setattr("app.execution._execute_code",lambda *args:pytest.fail("must not execute again"))
    assert seed()["ready"] and not seed()["executed"]
    store.record_negative_feedback(accepted["skill_id"],run,actor="portal-admin",reason="withdraw")
    assert not seed()["ready"] and not seed()["executed"]

def test_operator_init_disabled_never_creates_store(tmp_path,monkeypatch):
    from scripts.skill_library.local_basic import initialize
    monkeypatch.setattr(config,"SKILL_LIBRARY_ENABLED",False)
    store=SkillStore(tmp_path/"missing.db");monkeypatch.setattr(service,"STORE",store)
    assert not initialize()["ready"] and not store.path.exists()

def test_local_handoff_current_dify_transport(ready):
    from scripts.workflow.skill_handoff_wire import main as wire
    from scripts.workflow.skill_handoff_pointer import main as pointer
    store,run=ready;assert local.record(run)["admitted"]
    generated=generate()
    assert wire("original",generated["context"])["generation_context"]==generated["context"]
    ids=pointer([generated]);assert ids["decision_id"]==generated["skill_usage"]["decision_id"]
    assert pointer([generated,generated])["decision_id"]==""
    assert wire("changed",generated["context"])["generation_context"]=="changed"

@pytest.mark.parametrize("fault",[None,"auth","request","output","invalid_executor_receipt"])
def test_managed_legacy_transport_is_separate_from_executor_receipt(ready,fault):
    store,run=ready
    with sqlite3.connect(dashboard.DB_PATH) as db:
        row=db.execute("SELECT stdout,executor_evidence_json FROM executions WHERE run_id=?",(run,)).fetchone()
        proof=json.loads(row[1]);proof.pop("receipt")
        dispatch=proof["local_dispatch"];dispatch["receipt_correlated"]=False
        receipt=dict(version="gateway-transport-receipt.v1",request_sha256=dispatch["request_sha256"],
            request_nonce=dispatch["request_nonce"],stdout_sha256=local.pins.digest(row[0]),
            stderr_sha256=local.pins.digest(""),exit_code=0,timed_out=False,
            executor_receipt_absent=True,authentication_configured=True,independent_host_authenticated=False)
        if fault=="auth":receipt["authentication_configured"]=False
        if fault=="request":receipt["request_sha256"]="0"*64
        if fault=="output":receipt["stdout_sha256"]="0"*64
        if fault=="invalid_executor_receipt":receipt["executor_receipt_absent"]=False
        proof["local_transport_receipt"]=receipt
        db.execute("UPDATE executions SET executor_evidence_json=? WHERE run_id=?",(json.dumps(proof),run))
    assert local.record(run)["admitted"] is (fault is None)
