import copy,json,sqlite3
import httpx,pytest
from app import config,dashboard,execution,skill_local_profile as local,skill_local_execution as runner
from tests.unit.test_skill_local_profile import ready,generate
from scripts.validation.host_forward_signature import canonical

def args():
    generated=generate();usage=generated["skill_usage"]
    return dict(decision_id=usage["decision_id"],generation_run_id=usage["generation_intent_id"],
        scenario_type=local.SCENE,parameters_json=canonical(local.controlled_dispatch.fixed_retrieval_parameters()).decode(),
        original_context="original")

def response(store,source,code,timeout,nonce):
    with store._read_db() as db:
        row=db.execute("SELECT * FROM skill_local_reference_dispatches WHERE request_nonce=?",(nonce,)).fetchone()
        assert row is not None
        assert row["run_id"]!=source and timeout==30 and code==local._recipe()
    with sqlite3.connect(dashboard.DB_PATH) as db:
        stdout,proof=db.execute("SELECT stdout,executor_evidence_json FROM executions WHERE run_id=?",(source,)).fetchone()
    proof=json.loads(proof)
    proof["receipt"]["request_nonce"]=nonce
    proof["local_dispatch"]["request_nonce"]=nonce
    proof["local_dispatch"]["request_sha256"]=local.pins.digest(canonical(dict(code=code,timeout_seconds=30,request_nonce=nonce)).decode())
    return dict(stdout=stdout,stderr="",exit_code=0,timed_out=False,duration_ms=1,execution_evidence=proof)

def test_reference_registered_before_http_then_real_output_reverified(ready,monkeypatch):
    store,source=ready;assert local.record(source)["admitted"]
    call=args()
    monkeypatch.setattr(execution,"_execute_code",lambda code,timeout,request_nonce:response(store,source,code,timeout,request_nonce))
    result=runner.execute_reference(**call)
    assert result["recorded"] and result["execution_reuse_verified"]
    assert not result["actual_use_authenticated"] and not result["model_adoption_authenticated"]
    with store._read_db() as db:
        assert db.execute("SELECT count(*) FROM skill_local_reference_dispatches").fetchone()[0]==1
        assert db.execute("SELECT outcome FROM skill_local_reference_completions").fetchone()[0]=="verified"
    monkeypatch.setattr(execution,"_execute_code",lambda *a,**k:pytest.fail("must not replay"))
    assert not runner.execute_reference(**call)["execution_reuse_verified"]
    with store._db_existing_write() as db:
        with pytest.raises(sqlite3.Error):db.execute("DELETE FROM skill_local_reference_dispatches")
        with pytest.raises(sqlite3.Error):db.execute("UPDATE skill_local_reference_completions SET outcome='failed'")

@pytest.mark.parametrize("fault",["timeout","bad_nonce","physics","withdrawn","disabled","http_failure"])
def test_post_dispatch_faults_terminal_and_never_replay(ready,monkeypatch,fault):
    store,source=ready;sid=local.record(source)["skill_id"];call=args()
    def send(code,timeout,request_nonce):
        result=response(store,source,code,timeout,request_nonce)
        if fault=="http_failure":raise httpx.ConnectError("secret text must not be stored")
        if fault=="timeout":result["timed_out"]=True
        if fault=="bad_nonce":result["execution_evidence"]["local_dispatch"]["request_nonce"]="f"*32
        if fault=="physics":
            raw=json.loads(result["stdout"]);raw["raw_evidence"]["pressure_x2"]=[0]*3400
            result["stdout"]=json.dumps(raw)
            result["execution_evidence"]["receipt"]["stdout_sha256"]=local.pins.digest(result["stdout"])
        if fault=="withdrawn":store.record_negative_feedback(sid,source,actor="portal-admin",reason="incorrect result")
        if fault=="disabled":monkeypatch.setattr(config,"SKILL_INJECTION_ENABLED",False)
        return result
    monkeypatch.setattr(execution,"_execute_code",send)
    result=runner.execute_reference(**call)
    assert result["recorded"] and not result["execution_reuse_verified"]
    with store._read_db() as db:
        assert db.execute("SELECT outcome FROM skill_local_reference_completions").fetchone()[0]=="failed"
        detail=db.execute("SELECT detail FROM skill_events WHERE event_type='local_reference_execution_failed'").fetchone()[0]
        assert "secret text" not in detail
    monkeypatch.setattr(config,"SKILL_INJECTION_ENABLED",True)
    monkeypatch.setattr(execution,"_execute_code",lambda *a,**k:pytest.fail("must not replay"))
    assert not runner.execute_reference(**call)["execution_reuse_verified"]

def test_expired_or_wrong_pointer_never_dispatches(ready,monkeypatch):
    store,source=ready;assert local.record(source)["admitted"]
    call=args()
    with store._read_db() as db:expired=db.execute("SELECT expires_ns FROM skill_generation_decisions WHERE decision_id=?",(call["decision_id"],)).fetchone()[0]
    monkeypatch.setattr(local.time,"time_ns",lambda:expired)
    monkeypatch.setattr(execution,"_execute_code",lambda *a,**k:pytest.fail("expired reference"))
    assert not runner.execute_reference(**call)["recorded"]
    with store._read_db() as db:assert db.execute("SELECT count(*) FROM skill_local_reference_dispatches").fetchone()[0]==0

def test_generation_expiry_after_prebinding_does_not_replace_latest_withdrawal(ready,monkeypatch):
    store,source=ready;assert local.record(source)["admitted"];call=args()
    def send(code,timeout,request_nonce):
        result=response(store,source,code,timeout,request_nonce)
        with store._read_db() as db:expires=db.execute("SELECT expires_ns FROM skill_generation_decisions WHERE decision_id=?",(call["decision_id"],)).fetchone()[0]
        monkeypatch.setattr(local.time,"time_ns",lambda:expires+1)
        return result
    monkeypatch.setattr(execution,"_execute_code",send)
    assert runner.execute_reference(**call)["execution_reuse_verified"]

def test_source_capsule_write_budget_prevents_unreadable_admission(ready,monkeypatch):
    store,source=ready
    original=local.canonical
    def oversize(value):
        encoded=original(value)
        if type(value) is dict and set(value)=={"stdout","receipt","dispatch"}:return encoded+b" "*600001
        return encoded
    monkeypatch.setattr(local,"canonical",oversize)
    assert not local.record(source)["recorded"]
    with store._read_db() as db:assert db.execute("SELECT count(*) FROM simulation_skills").fetchone()[0]==0

def test_schema16_migration_collision_rolls_back_both_tables(tmp_path):
    from app.skill_store import SkillStore
    store=SkillStore(tmp_path/"skills.db");store.migrate()
    with store._db_existing_write() as db:
        db.execute("DROP TABLE skill_local_reference_completions")
        db.execute("DROP TABLE skill_local_reference_dispatches")
        db.execute("DELETE FROM schema_migrations WHERE version=17")
        db.execute("CREATE TABLE skill_local_reference_completions(marker TEXT)")
        db.execute("INSERT INTO skill_local_reference_completions VALUES ('preserve')")
    with pytest.raises(sqlite3.Error):store.migrate()
    with store._read_db() as db:
        assert db.execute("SELECT max(version) FROM schema_migrations").fetchone()[0]==16
        assert db.execute("SELECT marker FROM skill_local_reference_completions").fetchone()[0]=="preserve"
        assert db.execute("SELECT count(*) FROM sqlite_master WHERE name='skill_local_reference_dispatches'").fetchone()[0]==0

def test_prebinding_audit_failure_rolls_back_and_does_not_execute(ready,monkeypatch):
    store,source=ready;assert local.record(source)["admitted"];call=args()
    with store._db_existing_write() as db:
        db.execute("CREATE TRIGGER block_local_reference BEFORE INSERT ON skill_local_reference_dispatches BEGIN SELECT RAISE(ABORT,'blocked'); END")
    monkeypatch.setattr(execution,"_execute_code",lambda *a,**k:pytest.fail("must not execute"))
    assert not runner.execute_reference(**call)["recorded"]
    with store._read_db() as db:assert db.execute("SELECT count(*) FROM skill_local_reference_dispatches").fetchone()[0]==0

def test_bind_rechecks_expiry_after_insert_before_commit(ready,monkeypatch):
    store,source=ready;assert local.record(source)["admitted"];call=args()
    with store._read_db() as db:expires=db.execute("SELECT expires_ns FROM skill_generation_decisions WHERE decision_id=?",(call["decision_id"],)).fetchone()[0]
    original=runner._reference;calls=[]
    def late(db,reference,at=None):
        calls.append(at)
        if len(calls)==3:monkeypatch.setattr(local.time,"time_ns",lambda:expires)
        return original(db,reference,at)
    monkeypatch.setattr(runner,"_reference",late)
    monkeypatch.setattr(execution,"_execute_code",lambda *a,**k:pytest.fail("must not execute"))
    assert not runner.execute_reference(**call)["recorded"]
    with store._read_db() as db:assert db.execute("SELECT count(*) FROM skill_local_reference_dispatches").fetchone()[0]==0


def test_replace_cannot_overwrite_a_different_run_with_same_generation_or_nonce(ready):
    store,source=ready;assert local.record(source)["admitted"];call=args()
    run,nonce,_=runner._bind(call)
    with store._db_existing_write() as db:
        row=db.execute("SELECT * FROM skill_local_reference_dispatches WHERE run_id=?",(run,)).fetchone()
        for field in ("generation_run_id","request_nonce"):
            values=list(row)
            values[0]=str(__import__("uuid").uuid4())
            if field=="generation_run_id":values[7]=__import__("uuid").uuid4().hex
            # Duplicate unique identities must raise before OR REPLACE can delete.
            with pytest.raises(sqlite3.IntegrityError):
                db.execute("INSERT OR REPLACE INTO skill_local_reference_dispatches VALUES (?,?,?,?,?,?,?,?,?,?)",values)
        assert db.execute("SELECT run_id FROM skill_local_reference_dispatches").fetchone()[0]==run


def test_verified_execution_is_linked_to_exact_skill_version_for_workbench(ready,monkeypatch):
    store,source=ready;admitted=local.record(source);assert admitted["admitted"]
    monkeypatch.setattr(execution,"_execute_code",lambda code,timeout,request_nonce:response(store,source,code,timeout,request_nonce))
    result=runner.execute_reference(**args())
    assert result["execution_reuse_verified"]
    link=store.read_execution_links([result["run_id"]])[result["run_id"]]
    assert link=={"skill_id":admitted["skill_id"],"version":1,
        "stage":"recipe_execution_verified","execution_reuse_verified":True,
        "actual_use_authenticated":False,"model_adoption_authenticated":False}
    organization = store.list_metadata()['organization']
    assert organization['case_count'] == organization['version_count'] == 1
    assert organization['reexecution_count'] == 1 and organization['trust_verified'] is False
    assert store.read_execution_links(["missing-run"])=={}
    with pytest.raises(ValueError):store.read_execution_links(["x"]*501)

def test_failed_execution_is_not_exposed_as_verified_workbench_evidence(ready,monkeypatch):
    store,source=ready;assert local.record(source)["admitted"]
    monkeypatch.setattr(execution,"_execute_code",lambda *a,**k:(_ for _ in ()).throw(httpx.ConnectError("offline")))
    result=runner.execute_reference(**args())
    assert result["recorded"] and not result["execution_reuse_verified"]
    assert store.read_execution_links([result["run_id"]])=={}
    assert store.list_metadata()['organization']['reexecution_count'] == 0
