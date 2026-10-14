import json
import pytest
from app import config,skill_authority as authority,skill_retrieval
from host_observer import source_runner as runner
from tests.unit.test_skill_reference_consumer import available
from tests.unit.test_skill_host_intake import ready
from tests.unit.test_skill_authority import prepared,root
from tests.unit.test_host_forwarder import endpoint,TOKEN
def test_actual_loopback_source_can_reach_fixed_library_then_reference_metadata(available,endpoint,monkeypatch):
    store,key,call,_=available
    clock=[100_500_000_000]
    def now():
        clock[0]+=1_000_000;return clock[0]
    monkeypatch.setattr(authority.time,"time_ns",now)
    target,server,calls=endpoint
    server.raw=json.dumps(dict(stdout=call["stdout"],stderr="",exit_code=0,timed_out=False)).encode()
    got=runner.observe_fixed_source(target,key=key,shared_token=TOKEN,revision=1,
        reference_policy_window=(90_000_000_000,180_000_000_000))
    assert got["recorded"] and got["admitted"] and got["reference_policy_registered"]
    assert not got["actual_use_authenticated"] and len(calls)==1
    result=skill_retrieval.search("initial_pressure_2d",call["parameters_json"])
    assert len(result["skills"])==1 and result["skills"][0]["skill_id"]==got["skill_id"]
    with store._read_db() as db:
        assert db.execute("SELECT count(*) FROM skill_host_dispatches WHERE run_id=?",(got["run_id"],)).fetchone()[0]==1
        assert db.execute("SELECT count(*) FROM skill_host_verifications WHERE run_id=?",(got["run_id"],)).fetchone()[0]==1
@pytest.mark.parametrize("fault",["off","root","window","scene"])
def test_source_runner_never_forwards_before_exact_approval(available,endpoint,monkeypatch,fault):
    store,key,call,_=available;target,server,calls=endpoint
    if fault=="off":monkeypatch.setattr(config,"SKILL_LEARNING_ENABLED",False)
    if fault=="root":assert authority.record_root_statement(root(key,2,"revoke"))["recorded"]
    got=runner.observe_fixed_source(target,key=key,shared_token=TOKEN,revision=1,
        scenario_type="interface_2d" if fault=="scene" else "initial_pressure_2d",
        reference_policy_window=(1,2) if fault=="window" else None)
    assert not got["admitted"] and not got["recorded"] and len(calls)==0


def test_connected_source_generation_execution_and_withdrawal(available,endpoint,monkeypatch):
    """Two actual loopback requests; analytic outputs and synthetic identity only."""
    from host_observer import reference_runner
    store,key,call,_=available
    clock=[100_500_000_000]
    def now():
        clock[0]+=1_000_000
        return clock[0]
    monkeypatch.setattr(authority.time,"time_ns",now)
    target,server,calls=endpoint
    server.raw=json.dumps(dict(stdout=call["stdout"],stderr="",exit_code=0,timed_out=False)).encode()
    source=runner.observe_fixed_source(target,key=key,shared_token=TOKEN,revision=1,
        reference_policy_window=(90_000_000_000,180_000_000_000))
    assert source["admitted"] and source["reference_policy_registered"]
    built=skill_retrieval.generation_context("original",scenario_type="initial_pressure_2d",
        parameters_json=call["parameters_json"])
    assert built["context"]!="original"
    info=built["skill_usage"]
    reference=dict(decision_id=info["decision_id"],generation_run_id=info["generation_intent_id"],
        original_context="original",scenario_type="initial_pressure_2d",
        parameters_json=call["parameters_json"])
    execution=reference_runner.execute_bound_reference(target,reference=reference,
        key=key,shared_token=TOKEN,revision=1)
    assert execution["recorded"] and execution["execution_reuse_verified"]
    assert not execution["actual_use_authenticated"]
    assert execution["run_id"]!=source["run_id"] and len(calls)==2
    # The generation intent cannot be assigned to another execution.
    repeated=reference_runner.execute_bound_reference(target,reference=reference,
        key=key,shared_token=TOKEN,revision=1)
    assert not repeated["recorded"] and len(calls)==2
    sid=source["skill_id"]
    assert store.record_feedback_statement(sid,execution["run_id"],confirmed=True,
        request_id="connected-confirm",actor="portal-admin",reason="analytic fixture")["recorded"]
    assert store.record_feedback_statement(sid,execution["run_id"],confirmed=False,
        request_id="connected-withdraw",actor="portal-admin",reason="withdraw fixture")["recorded"]
    assert store.get_skill(sid)["status"]=="questioned"
    assert skill_retrieval.search("initial_pressure_2d",call["parameters_json"])["skills"]==[]
    assert skill_retrieval.generation_context("after",scenario_type="initial_pressure_2d",
        parameters_json=call["parameters_json"])["context"]=="after"
    with store._read_db() as db:
        assert db.execute("SELECT count(*) FROM skill_reference_executions").fetchone()[0]==1
