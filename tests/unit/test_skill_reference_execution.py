import copy
import hashlib
import json
import sqlite3
import pytest
from app import config,skill_reference_execution as usage,skill_authority as authority
from app import skill_reference_registry as registry,skill_retrieval
from scripts.validation.host_forward_signature import canonical,DOMAIN
from tests.unit.test_skill_reference_registry import proof
from tests.unit.test_skill_reference_consumer import available
from tests.unit.test_skill_host_intake import ready
from tests.unit.test_skill_authority import prepared,context,approval,root

def request(proof):
    store,key,call,evidence=proof
    assert registry.register(evidence)["recorded"]
    built=skill_retrieval.generation_context("original",scenario_type="plane_wave_2d",parameters_json=call["parameters_json"])
    info=built["skill_usage"]
    reference=dict(decision_id=info["decision_id"],generation_run_id=info["generation_intent_id"],
        original_context="original",scenario_type="plane_wave_2d",parameters_json=call["parameters_json"])
    ctx=context()
    return ctx,reference

def observed(proof,ctx):
    store,key,call,evidence=proof
    payload=json.loads(evidence["observation_document"])["payload"]
    obs=payload["observation"];obs.update(run_id=ctx["run_id"],nonce=ctx["nonce"],
        observed_before_ns=100_600_000_000,request_start_ns=100_700_000_000,
        request_end_ns=101_000_000_000,observed_after_ns=101_100_000_000)
    code=(usage.intake.pins.DIRECTORY/"recipes/val1_plane_wave_v2.py").read_text().strip()
    obs["request_sha256"]=hashlib.sha256(canonical(dict(code=code,timeout_seconds=30,request_nonce=ctx["nonce"]))).hexdigest()
    document=canonical(dict(payload=payload,signature=key.sign(DOMAIN+canonical(payload)).hex()))
    return dict(observation_document=document,stdout=evidence["stdout"],stderr="")

def test_reference_is_bound_before_new_execution_and_recorded_once(proof,monkeypatch):
    store,key,_,_=proof;ctx,reference=request(proof)
    assert authority.record_dispatch(ctx,approval(key,ctx),generation_reference=reference)["recorded"]
    # Execution may finish after the generation intent's 30s lifetime. Its
    # immutable pre-dispatch association proves initial scope; root stays current.
    monkeypatch.setattr(authority.time,"time_ns",lambda:135_000_000_000)
    args=observed(proof,ctx)
    result=usage.observe(**args)
    assert result["recorded"] and result["execution_reuse_verified"]
    assert not result["actual_use_authenticated"]
    assert usage.observe(**args)["duplicate"]
    with store._read_db() as db:
        assert db.execute("SELECT count(*) FROM skill_reference_executions").fetchone()[0]==1
        assert db.execute("SELECT count(*) FROM skill_events WHERE event_type='reference_execution_observed'").fetchone()[0]==1

@pytest.mark.parametrize("fault",["missing","context","off","revoked","output"])
def test_unbound_or_invalid_execution_cannot_claim_reuse(proof,monkeypatch,fault):
    store,key,_,_=proof;ctx,reference=request(proof)
    if fault=="context":reference["original_context"]="forged"
    if fault=="off":monkeypatch.setattr(config,"SKILL_INJECTION_ENABLED",False)
    outcome=authority.record_dispatch(ctx,approval(key,ctx),
        generation_reference=None if fault=="missing" else reference)
    if fault in {"context","off"}:
        assert not outcome["recorded"]
        with store._read_db() as db:assert not db.execute("SELECT 1 FROM skill_host_dispatches WHERE run_id=?",(ctx["run_id"],)).fetchone()
    else:assert outcome["recorded"]
    args=observed(proof,ctx)
    if fault=="revoked":assert authority.record_root_statement(root(key,2,"revoke"))["recorded"]
    if fault=="output":args["stdout"]="{}"
    monkeypatch.setattr(authority.time,"time_ns",lambda:101_200_000_000)
    assert not usage.observe(**args)["execution_reuse_verified"]
    with store._read_db() as db:assert db.execute("SELECT count(*) FROM skill_reference_executions").fetchone()[0]==0

def test_execution_audit_fault_rolls_back_and_tables_are_append_only(proof,monkeypatch):
    store,key,_,_=proof;ctx,reference=request(proof)
    assert authority.record_dispatch(ctx,approval(key,ctx),generation_reference=reference)["recorded"]
    monkeypatch.setattr(authority.time,"time_ns",lambda:101_200_000_000)
    with store._db_existing_write() as db:
        db.execute("CREATE TRIGGER injected_fault BEFORE INSERT ON skill_events WHEN NEW.event_type='reference_execution_observed' BEGIN SELECT RAISE(ABORT,'fault'); END")
    assert not usage.observe(**observed(proof,ctx))["recorded"]
    with store._db_existing_write() as db:
        assert db.execute("SELECT count(*) FROM skill_reference_executions").fetchone()[0]==0
        for sql in ["DELETE FROM skill_reference_dispatches","UPDATE skill_reference_dispatches SET registered_ns=0"]:
            with pytest.raises(sqlite3.IntegrityError,match="append-only"):db.execute(sql)


def test_host_runner_refusal_never_forwards(proof,monkeypatch):
    from host_observer import reference_runner as runner
    from host_observer.forwarder import Target
    store,key,_,_=proof;ctx,reference=request(proof)
    target=Target(ip=ctx["target_ip"],port=ctx["target_port"],container_id=ctx["container_id"],
                 image_id=ctx["image_id"],started_at=ctx["started_at"])
    reference["original_context"]="changed"
    def forbidden(*args,**kwargs):raise AssertionError("rejected dispatch forwarded")
    monkeypatch.setattr(runner,"forward_with_approved_key",forbidden)
    result=runner.execute_bound_reference(target,reference=reference,key=key,shared_token="synthetic-token"*3,revision=1)
    assert result["reason"]=="reference_dispatch_rejected"
    with store._read_db() as db:assert db.execute("SELECT count(*) FROM skill_reference_dispatches").fetchone()[0]==0

def test_host_runner_connects_bound_dispatch_to_observed_execution(proof,monkeypatch):
    from host_observer import reference_runner as runner
    from host_observer.forwarder import Target
    store,key,_,_=proof;ctx,reference=request(proof)
    target=Target(ip=ctx["target_ip"],port=ctx["target_port"],container_id=ctx["container_id"],
                 image_id=ctx["image_id"],started_at=ctx["started_at"])
    def forward(target,*,run_id,nonce,shared_token,key,revision):
        # Transport replacement only; signed arrays/authority are real test
        # objects. This is not a physical executor or production root.
        new=dict(ctx,run_id=run_id,nonce=nonce)
        args=observed(proof,new)
        monkeypatch.setattr(authority.time,"time_ns",lambda:101_200_000_000)
        return dict(signed_observation=args["observation_document"],result=dict(stdout=args["stdout"],stderr=""))
    monkeypatch.setattr(runner,"forward_with_approved_key",forward)
    result=runner.execute_bound_reference(target,reference=reference,key=key,shared_token="synthetic-token"*3,revision=1)
    assert result["execution_reuse_verified"] and not result["actual_use_authenticated"]


def test_feedback_on_observed_reference_execution_keeps_exact_version(proof,monkeypatch):
    store,key,call,_=proof;ctx,reference=request(proof)
    assert authority.record_dispatch(ctx,approval(key,ctx),generation_reference=reference)["recorded"]
    monkeypatch.setattr(authority.time,"time_ns",lambda:101_200_000_000)
    assert usage.observe(**observed(proof,ctx))["recorded"]
    sid=call["skill_id"]
    statement=store.record_feedback_statement(sid,ctx["run_id"],confirmed=True,
        request_id="execution-confirm",actor="portal-admin",reason="verified reference worked")
    assert statement["recorded"]
    with store._read_db() as db:
        raw=db.execute("SELECT detail FROM skill_events WHERE skill_id=? AND event_type='feedback_statement' ORDER BY id DESC LIMIT 1",(sid,)).fetchone()[0]
        association=json.loads(raw)["source_association"]
        assert association["kind"]=="independently_observed_reference_execution"
        assert association["version"]==1 and association["execution_reuse_verified"]
        assert not association["actual_use_authenticated"]
    store.record_feedback_statement(sid,ctx["run_id"],confirmed=False,
        request_id="execution-withdraw",actor="portal-admin",reason="withdraw confirmation")
    assert store.get_skill(sid)["status"]=="questioned"
    assert skill_retrieval.generation_context("after",scenario_type="plane_wave_2d",
        parameters_json=call["parameters_json"])["context"]=="after"


from tests.unit.test_host_forwarder import endpoint,TOKEN

def test_actual_loopback_observer_to_native_reference_execution(proof,endpoint,monkeypatch):
    from host_observer import reference_runner as runner
    store,key,call,evidence=proof
    clock=[100_500_000_000]
    def advancing_time():
        clock[0]+=1_000_000
        return clock[0]
    monkeypatch.setattr(authority.time,"time_ns",advancing_time)
    _,reference=request(proof)
    target,server,calls=endpoint
    server.raw=json.dumps(dict(stdout=evidence["stdout"],stderr="",exit_code=0,timed_out=False)).encode()
    result=runner.execute_bound_reference(target,reference=reference,key=key,shared_token=TOKEN,revision=1)
    assert result["recorded"] and result["execution_reuse_verified"]
    assert not result["actual_use_authenticated"]
    assert len(calls)==1 and calls[0][0]=="/execute"
    with store._read_db() as db:
        dispatch=db.execute("SELECT run_id FROM skill_reference_dispatches").fetchone()[0]
        assert result["run_id"]==dispatch
        assert db.execute("SELECT count(*) FROM skill_reference_executions").fetchone()[0]==1
