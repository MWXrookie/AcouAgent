import copy
import json
from scripts.validation.skill_daily_reuse import inspect_trace

def fallback():
    original='{"scenario_type":"initial_pressure_2d"}'
    ids={"stage1_params_main":dict(inputs=dict(parameters_json=original,original_query="question"),outputs={}),
         "stage1_main_guard":dict(inputs={},outputs=dict(generation_context=original,skill_injected=False)),
         "local_basic_main_merge":dict(inputs={},outputs=dict(output=original)),
         "1783991079665":dict(inputs={},outputs=dict(text="print(1)"))}
    events=[dict(event="node_finished",data=dict(node_id=k,status="succeeded",**v)) for k,v in ids.items()]
    events.insert(0,dict(event="workflow_started",data=dict(id="native-run")))
    return dict(complete=True,events=events),dict(task_id="negative",prompt="question",expected_reference=False)

def test_fallback_keeps_original():
    trace,task=fallback()
    result=inspect_trace(trace,task)
    assert result["passed"] and not result["execution_reuse_verified"]
    assert not result["model_adoption_verified"] and not result["trace_authority_checked"]
    assert not result["actual_use_authenticated"]

def test_changed_fallback_is_rejected():
    trace,task=fallback()
    trace["events"][3]["data"]["outputs"]["output"]="some other context"
    assert inspect_trace(trace,task)["reason"]=="fallback_context_changed"

def test_unbound_query_is_rejected():
    trace,task=fallback()
    trace["events"][1]["data"]["inputs"]["original_query"]=None
    assert inspect_trace(trace,task)["reason"]=="original_task_binding_changed"

def test_partial_and_duplicate_trace_are_not_adoption():
    trace,task=fallback()
    trace["complete"]=False
    assert not inspect_trace(trace,task)["passed"]
    trace["complete"]=True
    trace["events"].append(copy.deepcopy(trace["events"][1]))
    assert inspect_trace(trace,task)["reason"]=="duplicate_node"

def test_model_claim_without_service_delivery_is_not_adoption():
    trace,task=fallback();task["expected_reference"]=True
    trace["events"][-1]["data"]["outputs"]["text"]="I reused skill 1 successfully"
    result=inspect_trace(trace,task)
    assert not result["passed"] and not result["model_adoption_verified"]


def test_flags_require_native_and_same_execution_evidence():
    from scripts.validation.skill_daily_reuse import finalize_evidence
    runtime=dict(passed=True,workflow_run_id="r",model_adoption_observed=True,
                 execution_reuse_observed=True,authority_db_checked=True,numeric_pass=True,
                 reuse_mode="model_adopted_reference")
    assert not finalize_evidence(runtime,{},True)["model_adoption_verified"]
    assert not finalize_evidence(runtime,dict(trace_authority_checked=True,workflow_run_id="other"),True)["passed"]
    got=finalize_evidence(runtime,dict(trace_authority_checked=True,workflow_run_id="r"),True)
    assert got["model_adoption_verified"] and got["execution_reuse_verified"] and not got["actual_use_authenticated"]


def test_direct_recipe_execution_is_verified_without_claiming_model_adoption():
    from scripts.validation.skill_daily_reuse import finalize_evidence
    runtime=dict(passed=True,workflow_run_id="r",model_adoption_observed=False,
        model_call_avoided=True,reuse_mode="direct_fixed_recipe",
        execution_reuse_observed=True,authority_db_checked=True,numeric_pass=True)
    native=dict(trace_authority_checked=True,workflow_run_id="r",model_nodes_absent_verified=True)
    got=finalize_evidence(runtime,native,True)
    assert got["passed"] and got["execution_reuse_verified"]
    assert got["direct_recipe_execution_verified"] and not got["model_adoption_verified"]
    assert not got["actual_use_authenticated"]
    denied=finalize_evidence(runtime,dict(trace_authority_checked=True,workflow_run_id="r"),True)
    assert not denied["passed"] and not denied["direct_recipe_execution_verified"]
