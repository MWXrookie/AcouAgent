import copy
import json
import pytest
from scripts.workflow import candidate_intake_graph,skill_retrieval_graph as patcher
from tests.unit.test_skill_parameter_pipeline import graph,BRANCH

TEMPLATE={"id":"observed-search-template","data":{"type":"tool","tool_name":"search_simulation_skill","provider_type":"mcp","provider_id":"fixture-provider"}}
BINDING={"adapter":"skill-params","generator":"generate","prefix":"skill-main","context_selector":["params","text"]}

def prepared():
    value=graph();value["nodes"][1]["data"]["prompt_template"]=[{"role":"user","text":"Generate once from {{#params.text#}}; keep other instructions."}]
    return candidate_intake_graph.patch(value,[BRANCH])

def code(node,**kwargs):
    namespace={};exec(compile(node["data"]["code"],"dify-guard-fixture","exec"),namespace)
    return namespace["main"](**kwargs)

def test_disabled_subgraph_bypasses_search_and_preserves_input():
    original=prepared();saved=copy.deepcopy(original)
    result=patcher.patch(original,[BINDING],TEMPLATE)
    assert original==saved
    nodes={n["id"]:n for n in result["nodes"]}
    assert code(nodes["skill-main-gate"],ready=True)=={"route":"skip"}
    closed=code(nodes["skill-main-fallback"])["text"]
    guarded=code(nodes["skill-main-guard"],original_context="raw context",retrieval_result_json=closed)
    assert guarded["generation_context"]=="raw context" and not guarded["skill_injected"]
    assert "skill-main-guard.generation_context" in nodes["generate"]["data"]["prompt_template"][0]["text"]
    assert nodes["unrelated"]==saved["nodes"][3]
    assert nodes["execute"]==saved["nodes"][2]
    assert len(result["nodes"])==len(saved["nodes"])+6

def test_enabled_tool_errors_fallback_without_retry_or_injection():
    value=patcher.patch(prepared(),[BINDING],TEMPLATE,retrieval_enabled=True,injection_enabled=True)
    nodes={n["id"]:n for n in value["nodes"]}
    assert code(nodes["skill-main-gate"],ready=False)=={"route":"skip"}
    assert code(nodes["skill-main-gate"],ready=True)=={"route":"search"}
    tool=nodes["skill-main-search"]["data"]
    assert tool["retry_config"]["retry_enabled"] is False
    assert tool["error_strategy"]=="default-value" and tool["tool_parameters"]["limit"]["value"]==3
    for response in [tool["default_value"][0]["value"],"not JSON",json.dumps({"skills":[{"code":"unsafe"}],"trusted_run":True})]:
        out=code(nodes["skill-main-guard"],original_context="original",retrieval_result_json=response)
        assert out["generation_context"]=="original" and not out["skill_injected"]

@pytest.mark.parametrize("fault",["context","collision","missing_edge","wrong_template","switch"])
def test_ambiguous_patch_refuses_without_mutation(fault):
    value=prepared();binding=copy.deepcopy(BINDING);template=copy.deepcopy(TEMPLATE);kwargs={}
    if fault=="context":binding["context_selector"]=["params","other"]
    if fault=="collision":binding["prefix"]="skill";value["nodes"].append({"id":"skill-guard","data":{}})
    if fault=="missing_edge":value["edges"]=[e for e in value["edges"] if e["target"]!="generate"]
    if fault=="wrong_template":template["data"]["tool_name"]="other"
    if fault=="switch":kwargs["retrieval_enabled"]="true"
    saved=copy.deepcopy(value)
    with pytest.raises(ValueError):patcher.patch(value,[binding],template,**kwargs)
    assert value==saved

def test_main_retry_branches_have_separate_query_and_context():
    value=prepared()
    value["nodes"] += [
        {"id":"retry-params","data":{"type":"llm"}},
        {"id":"retry-generate","data":{"type":"llm","prompt_template":[{"role":"user","text":"Retry {{#retry-params.text#}}"}]}},
        {"id":"retry-execute","data":{"type":"tool","tool_name":"run_jwave_code","tool_parameters":{}}}]
    value["edges"].append({"id":"retry-edge","source":"retry-params","target":"retry-generate"})
    value=candidate_intake_graph.patch(value,[dict(parameter="retry-params",generator="retry-generate",
        executor="retry-execute",adapter="retry-adapter",scenario_type="point_source_2d")])
    bindings=[BINDING,dict(adapter="retry-adapter",generator="retry-generate",prefix="skill-retry",context_selector=["retry-params","text"])]
    result=patcher.patch(value,bindings,TEMPLATE)
    nodes={n["id"]:n for n in result["nodes"]}
    assert nodes["skill-main-guard"]["data"]["variables"][0]["value_selector"]==["params","text"]
    assert nodes["skill-retry-guard"]["data"]["variables"][0]["value_selector"]==["retry-params","text"]
    assert "retry-adapter.retrieval_parameters_json" in nodes["skill-retry-search"]["data"]["tool_parameters"]["parameters_json"]["value"]
    assert len([e for e in result["edges"] if e["target"]=="generate"])==1
    assert len([e for e in result["edges"] if e["target"]=="retry-generate"])==1

def test_wrong_branch_parameter_context_is_rejected():
    value=prepared();value["nodes"].append({"id":"other-params","data":{"type":"llm"}})
    value["nodes"][1]["data"]["prompt_template"][0]["text"]="{{#other-params.text#}}"
    binding=dict(BINDING,context_selector=["other-params","text"])
    with pytest.raises(ValueError,match="parameters_must_match"):
        patcher.patch(value,[binding],TEMPLATE)

def test_truthy_adapter_output_never_opens_search():
    result=patcher.patch(prepared(),[BINDING],TEMPLATE,retrieval_enabled=True,injection_enabled=True)
    gate=next(n for n in result["nodes"] if n["id"]=="skill-main-gate")
    for ready in ["true",1,None,False]:
        assert code(gate,ready=ready)["route"]=="skip"

def test_repeat_patch_idempotent_but_drift_is_not_silently_overwritten():
    original=patcher.patch(prepared(),[BINDING],TEMPLATE)
    assert patcher.patch(original,[BINDING],TEMPLATE)==original
    changed=copy.deepcopy(original)
    node=next(n for n in changed["nodes"] if n["id"]=="skill-main-search")
    node["data"]["tool_parameters"]["limit"]["value"]=30
    saved=copy.deepcopy(changed)
    with pytest.raises(ValueError,match="drifted"):patcher.patch(changed,[BINDING],TEMPLATE)
    assert changed==saved
