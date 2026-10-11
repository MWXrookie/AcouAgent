import copy
import json
import pytest
from scripts.workflow import skill_stage1_graph as stage
from tests.unit.test_skill_retrieval_graph import TEMPLATE, code
from tests.unit.test_skill_parameter_pipeline import graph, BRANCH

def source():
    g=graph()
    g["nodes"][0]["data"]["prompt_template"]=[{"role":"user","text":"Extract user parameters"}]
    g["nodes"][1]["data"]["prompt_template"]=[{"role":"system","text":"PR6 timing contract unchanged"},
        {"role":"user","text":"Generate {{#params.text#}}"}]
    return g

def branch():
    return dict(BRANCH,prefix="stage-main")

def test_retrieval_can_run_without_code_injection_and_preserves_existing_contract():
    original=source(); saved=copy.deepcopy(original)
    result=stage.compose(original,[branch()],TEMPLATE,retrieval_enabled=True)
    nodes={n["id"]:n for n in result["nodes"]}
    assert original==saved
    assert code(nodes["stage-main-gate"],ready=True)["route"]=="search"
    assert code(nodes["stage-main-gate"],ready="true")["route"]=="skip"
    guarded=code(nodes["stage-main-guard"],original_context="input",
                 retrieval_result_json=json.dumps({"skills":[{"code":"untrusted"}]}))
    assert guarded["generation_context"]=="input" and not guarded["skill_injected"]
    assert nodes["generate"]["data"]["prompt_template"][0]==saved["nodes"][1]["data"]["prompt_template"][0]
    assert nodes["unrelated"]==saved["nodes"][3]
    assert nodes["execute"]["data"]["tool_parameters"]["code"]==saved["nodes"][2]["data"]["tool_parameters"]["code"]

def test_both_branches_use_their_own_extraction_and_execution_context():
    g=source()
    g["nodes"] += [
        {"id":"retry-p","data":{"type":"llm","prompt_template":[{"role":"user","text":"Retry parameters"}]}},
        {"id":"retry-g","data":{"type":"llm","prompt_template":[{"role":"user","text":"Retry {{#params.text#}}"}]}},
        {"id":"retry-e","data":{"type":"tool","tool_name":"run_jwave_code","tool_parameters":{}}}]
    g["edges"].append({"id":"retry","source":"retry-p","target":"retry-g"})
    retry=dict(parameter="retry-p",generator="retry-g",executor="retry-e",
               adapter="retry-a",scenario_type="",prefix="stage-retry")
    with pytest.raises(ValueError,match="selector"):
        stage.compose(g,[branch(),retry],TEMPLATE)
    result=stage.compose(g,[branch(),retry],TEMPLATE,
                        selector_corrections=[dict(generator="retry-g",old_parameter="params")])
    nodes={n["id"]:n for n in result["nodes"]}
    assert code(nodes["stage-retry-gate"],ready=True)["route"]=="skip"
    assert nodes["stage-retry-guard"]["data"]["variables"][0]["value_selector"]==["retry-p","text"]
    assert "retry-a.candidate_parameters_json" in nodes["retry-e"]["data"]["tool_parameters"]["params_json"]["value"]
    assert len([e for e in result["edges"] if e["target"]=="retry-g"])==1

def test_cli_writes_reviewable_graph_and_refuses_overwrite(tmp_path):
    src=tmp_path/"graph.json"; binding=tmp_path/"bindings.json"; out=tmp_path/"after.json"
    src.write_text(json.dumps(source()))
    binding.write_text(json.dumps(dict(branches=[branch()],search_template=TEMPLATE,selector_corrections=[])))
    argv=["--graph",str(src),"--bindings",str(binding),"--output",str(out)]
    stage.main(argv)
    assert json.loads(out.read_text())==stage.compose(source(),[branch()],TEMPLATE)
    before=out.read_bytes()
    with pytest.raises(FileExistsError):stage.main(argv)
    assert out.read_bytes()==before

@pytest.mark.parametrize("fault",["provider","duplicate_branch","switch","existing_graph"])
def test_invalid_or_already_patched_input_is_not_overwritten(fault):
    g=source(); branches=[branch()]; template=copy.deepcopy(TEMPLATE); kwargs={}
    if fault=="provider":template["data"].pop("provider_id")
    if fault=="duplicate_branch":branches.append(branch())
    if fault=="switch":kwargs["retrieval_enabled"]="true"
    if fault=="existing_graph":g=stage.compose(g,branches,template)
    saved=copy.deepcopy(g)
    with pytest.raises(ValueError):stage.compose(g,branches,template,**kwargs)
    assert g==saved

def test_duplicate_json_keys_are_rejected(tmp_path):
    p=tmp_path/"bad.json";p.write_text('{"nodes":[],"nodes":[]}')
    with pytest.raises(ValueError,match="duplicate"):stage.load_json(p)

def test_actual_dify_json_array_transport_is_normalized_without_trust():
    g=stage.compose(source(),[branch()],TEMPLATE,retrieval_enabled=True)
    nodes={n["id"]:n for n in g["nodes"]}
    value={"skills":[], "fallback":True, "reason":"no_applicable_skill",
           "trusted_run":False,"runtime_approved":False,"parameters_bound":False,"run_bound":False}
    result=code(nodes["stage-main-response"],result=[value])
    assert json.loads(result["text"])==value
    assert nodes["stage-main-response"]["data"]["variables"][0]["value_selector"]==["stage-main-search","json"]
    for malformed in ([],None,[value,value],"caller JSON"):
        assert json.loads(code(nodes["stage-main-response"],result=malformed)["text"])["reason"]=="retrieval_failed"
    guarded=code(nodes["stage-main-guard"],original_context="original",retrieval_result_json=result["text"])
    assert guarded["generation_context"]=="original" and guarded["skill_injected"] is False
