import copy
import json
import uuid
import pytest
from scripts.workflow import skill_context_graph as patcher
from scripts.workflow import skill_handoff_pointer as pointer, skill_handoff_wire as wire
from tests.unit.test_skill_retrieval_graph import prepared as graph_prepared,BINDING,code

BUILDER={"data":{"type":"tool","tool_name":"build_simulation_generation_context","provider_id":"fixture"}}
READER={"data":{"type":"tool","tool_name":"read_simulation_generation_context","provider_id":"fixture"}}

def test_closed_bypasses_both_tools_and_preserves_original():
    original=graph_prepared();saved=copy.deepcopy(original)
    result=patcher.patch(original,[BINDING],BUILDER,READER)
    assert original==saved
    nodes={n["id"]:n for n in result["nodes"]}
    assert code(nodes["skill-main-gate"],ready=True)=={"route":"skip"}
    assert code(nodes["skill-main-fallback"],original_context="raw")=={"generation_context":"raw"}
    assert len(result["nodes"])==len(original["nodes"])+8
    assert nodes["execute"]==next(n for n in original["nodes"] if n["id"]=="execute")
    assert nodes["skill-main-wire"]["data"]["variables"][1]["value_selector"]==["skill-main-reader","text"]
    assert patcher.patch(result,[BINDING],BUILDER,READER)==result

def test_enabled_routes_strictly_and_errors_preserve_context():
    result=patcher.patch(graph_prepared(),[BINDING],BUILDER,READER,retrieval_enabled=True,injection_enabled=True)
    nodes={n["id"]:n for n in result["nodes"]}
    for ready in [False,None,1,"true"]:
        assert code(nodes["skill-main-gate"],ready=ready)=={"route":"skip"}
    assert code(nodes["skill-main-gate"],ready=True)=={"route":"use"}
    for role in ["builder","reader"]:
        assert nodes["skill-main-"+role]["data"]["retry_config"]["retry_enabled"] is False
    invalid=nodes["skill-main-builder"]["data"]["default_value"][0]["value"]
    assert code(nodes["skill-main-pointer"],result_json=invalid)=={"decision_id":"","generation_run_id":""}
    assert code(nodes["skill-main-wire"],original_context="raw",server_context="")=={"generation_context":"raw"}

@pytest.mark.parametrize("fault",["provider","context","partial","drift"])
def test_rejects_ambiguous_bindings_without_overwrite(fault):
    original=graph_prepared();binding=copy.deepcopy(BINDING);reader=copy.deepcopy(READER)
    if fault=="provider":reader["data"]["provider_id"]="other"
    if fault=="context":binding["context_selector"]=["params","other"]
    if fault=="partial":original["nodes"].append({"id":"skill-main-reader","data":{}})
    if fault=="drift":
        original=patcher.patch(original,[binding],BUILDER,reader)
        next(n for n in original["nodes"] if n["id"]=="skill-main-wire")["data"]["variables"][1]["value_selector"]=["caller","text"]
    saved=copy.deepcopy(original)
    with pytest.raises(ValueError):patcher.patch(original,[binding],BUILDER,reader)
    assert original==saved

def test_pointer_extracts_ids_only_never_code_or_trust():
    first,second=str(uuid.uuid4()),str(uuid.uuid4())
    body={"skill_usage":{"decision_id":first,"generation_intent_id":second},"trusted_run":True,"code":"unsafe"}
    assert pointer.main(json.dumps(body))=={"decision_id":first,"generation_run_id":second}
    for value in ["[]","null",'{"skill_usage":{},"skill_usage":{}}',"x"*150001]:
        assert pointer.main(value)=={"decision_id":"","generation_run_id":""}

def test_format_guard_is_not_json_trust_authorizer():
    for value in ['{"trusted_run":true,"code":"unsafe"}',"raw"+wire.PREFIX+'{"code":"unsafe"}',"other"]:
        assert wire.main("raw",value)=={"generation_context":"raw"}


from tests.unit.test_skill_reference_registry import proof
from tests.unit.test_skill_reference_consumer import available
from tests.unit.test_skill_host_intake import ready
from tests.unit.test_skill_authority import prepared

@pytest.mark.parametrize("branch",["main","retry"])
def test_graph_code_handoff_uses_real_server_decision_and_reader(proof,branch):
    from app import skill_reference_registry
    from tests.unit.test_skill_generation_tool import registered
    store,key,call,evidence=proof
    assert skill_reference_registry.register(evidence)["recorded"]
    binding=dict(BINDING,prefix="handoff-"+branch)
    patched=patcher.patch(graph_prepared(),[binding],BUILDER,READER,retrieval_enabled=True,injection_enabled=True)
    nodes={n["id"]:n for n in patched["nodes"]}
    funcs=registered();original=branch+" original"
    built=funcs["build_simulation_generation_context"](original,"plane_wave_2d",call["parameters_json"])
    pointer_args=code(nodes["handoff-"+branch+"-pointer"],result_json=json.dumps(built))
    server=funcs["read_simulation_generation_context"](original,"plane_wave_2d",call["parameters_json"],**pointer_args)
    delivered=code(nodes["handoff-"+branch+"-wire"],original_context=original,server_context=server)
    assert delivered["generation_context"]==built["context"] and delivered["generation_context"]!=original
    assert not built["skill_usage"]["actual_use_authenticated"]

def test_withdrawal_between_builder_and_reader_preserves_original(proof):
    from app import skill_reference_registry
    from tests.unit.test_skill_generation_tool import registered
    from tests.unit.test_skill_authority import root
    store,key,call,evidence=proof
    assert skill_reference_registry.register(evidence)["recorded"]
    funcs=registered()
    built=funcs["build_simulation_generation_context"]("original","plane_wave_2d",call["parameters_json"])
    ids=pointer.main(json.dumps(built))
    assert skill_reference_registry.consumer.authority.record_root_statement(root(key,2,"revoke"))["recorded"]
    result=funcs["read_simulation_generation_context"]("original","plane_wave_2d",call["parameters_json"],**ids)
    assert wire.main("original",result)=={"generation_context":"original"}

def test_existing_guard_predecessor_preserves_query_and_context_binding():
    graph=graph_prepared()
    graph["nodes"].append({"id":"old-guard","data":{"type":"code"}})
    edge=next(e for e in graph["edges"] if e["source"]==BINDING["adapter"] and e["target"]==BINDING["generator"])
    edge["source"]="old-guard"
    generator=next(n for n in graph["nodes"] if n["id"]==BINDING["generator"])
    old="{{#"+".".join(BINDING["context_selector"])+"#}}"
    for prompt in generator["data"]["prompt_template"]:
        prompt["text"]=prompt["text"].replace(old,"{{#old-guard.generation_context#}}")
    binding=dict(BINDING,predecessor="old-guard",parameter_selector=BINDING["context_selector"],
                 context_selector=["old-guard","generation_context"])
    result=patcher.patch(graph,[binding],BUILDER,READER,retrieval_enabled=True,injection_enabled=True)
    nodes={n["id"]:n for n in result["nodes"]}
    assert nodes["skill-main-pointer"]["data"]["variables"][0]["value_selector"]==["skill-main-builder","json"]
    assert nodes["skill-main-gate"]["data"]["variables"][0]["value_selector"]==[BINDING["adapter"],"ready"]
    assert patcher.patch(result,[binding],BUILDER,READER,retrieval_enabled=True,injection_enabled=True)==result


@pytest.mark.parametrize("selector",[None,[],["old-guard"],"old-guard",{},[0,"context"],["old-guard",""]])
def test_invalid_context_selector_fails_before_predecessor_indexing(selector):
    graph=graph_prepared()
    graph["nodes"].append({"id":"old-guard","data":{"type":"code"}})
    saved=copy.deepcopy(graph)
    binding=dict(BINDING,predecessor="old-guard",
        parameter_selector=BINDING["context_selector"],context_selector=selector)
    with pytest.raises(ValueError,match="invalid_branch"):
        patcher.patch(graph,[binding],BUILDER,READER)
    assert graph==saved
