import copy
import json
import pytest
from app import config, skill_retrieval, candidate_intake
from app.skill_store import normalize_parameters
from scripts.workflow import skill_parameter_adapter as adapter, candidate_intake_graph as graph_patch
from scripts.workflow.skill_generation_guard import main as guard
from tests.unit.test_candidate_intake import env, PARAMS, CODE
from tests.unit.test_controlled_validation import history

def graph():
    return dict(nodes=[
        {"id":"params","position":{"x":100,"y":200},"data":{"type":"llm","title":"extract"}},
        {"id":"generate","data":{"type":"llm","title":"generate"}},
        {"id":"execute","data":{"type":"tool","tool_name":"run_jwave_code_with_retry",
            "tool_parameters":{"code":{"type":"mixed","value":"{{#generate.text#}}"}}}},
        {"id":"unrelated","data":{"type":"llm","prompt_template":"keep draft changes"}}],
        edges=[dict(id="generation",source="params",target="generate",sourceHandle="source",
                    targetHandle="target",data={"sourceType":"llm","targetType":"llm"}),
               dict(id="execution",source="generate",target="execute")],viewport={"x":10})

BRANCH=dict(parameter="params",generator="generate",executor="execute",
            adapter="skill-params",scenario_type="point_source_2d")

def test_units_shared_by_retrieval_and_candidate_intake():
    result=adapter.main(json.dumps(dict(PARAMS,scenario_type="point_source_2d")))
    assert result["ready"] and result["reason"]=="declared_parameters_only"
    normalized=normalize_parameters({("density_kg_m3" if k=="density" else k):v for k,v in PARAMS.items()})
    normalized["dimensions"]=len(PARAMS["domain_N"])
    assert json.loads(result["retrieval_parameters_json"])==normalized
    assert json.loads(result["candidate_parameters_json"])==PARAMS

@pytest.mark.parametrize("payload",[
    "not json",'{"source_frequency":1,"source_frequency":2}',json.dumps(dict(PARAMS,runtime_approved=True)),
    json.dumps(dict(PARAMS,domain_dx=True)),json.dumps(dict(PARAMS,source_index=[1,1])),
    json.dumps(dict(PARAMS,sound_speed=float("nan"))),json.dumps(dict(PARAMS,solver_mode="unknown")),
    json.dumps(dict(PARAMS,medium_type="unknown")),json.dumps(dict(PARAMS,domain_N=[32,32,32])),
    json.dumps(PARAMS)+" "*4097])
def test_bad_extraction_bypasses_learning(payload):
    result=adapter.main(payload,"point_source_2d")
    assert not result["ready"] and result["scenario_type"]=="" and result["candidate_parameters_json"]=="{}"

def test_missing_or_conflicting_scenario_is_not_guessed():
    assert not adapter.main(json.dumps(PARAMS))["ready"]
    assert not adapter.main(json.dumps(dict(PARAMS,scenario_type="plane_wave_2d")),"point_source_2d")["ready"]

def test_offline_graph_patch_is_narrow_idempotent_and_executable():
    original=graph();saved=copy.deepcopy(original)
    patched=graph_patch.patch(original,[BRANCH])
    assert original==saved
    assert patched==graph_patch.patch(patched,[BRANCH])
    assert patched["nodes"][3]==original["nodes"][3]
    assert len(patched["nodes"])==len(original["nodes"])+1
    code=patched["nodes"][-1]["data"]["code"]
    namespace={};exec(compile(code,"test-dify-adapter","exec"),namespace)
    assert namespace["main"](json.dumps(PARAMS))["ready"]
    parameters=patched["nodes"][2]["data"]["tool_parameters"]
    assert parameters["code"]==original["nodes"][2]["data"]["tool_parameters"]["code"]
    assert parameters["params_json"]["value"]=="{{#skill-params.candidate_parameters_json#}}"
    assert {edge["id"] for edge in patched["edges"]}=={"params-to-skill-params","skill-params-to-generate","execution"}

@pytest.mark.parametrize("fault",["missing_edge","collision","wrong_tool","existing_selector"])
def test_graph_patch_refuses_ambiguous_layout_and_preserves_input(fault):
    value=graph()
    if fault=="missing_edge":value["edges"]=value["edges"][1:]
    if fault=="collision":value["nodes"].append(dict(id="skill-params",data={}))
    if fault=="wrong_tool":value["nodes"][2]["data"]["tool_name"]="other"
    if fault=="existing_selector":value["nodes"][2]["data"]["tool_parameters"]["params_json"]={}
    saved=copy.deepcopy(value)
    with pytest.raises(ValueError):graph_patch.patch(value,[BRANCH])
    assert value==saved

def test_extraction_retrieval_generation_execution_feedback_chain(env,monkeypatch):
    monkeypatch.setattr(config,"SKILL_RETRIEVAL_ENABLED",True)
    prepared=adapter.main(json.dumps(PARAMS),"point_source_2d")
    usage=skill_retrieval.search(prepared["scenario_type"],prepared["retrieval_parameters_json"])
    original_context="preserve the original generation context"
    context=guard(original_context,json.dumps(usage),True,True)
    assert context["generation_context"]==original_context and not context["skill_injected"]
    generated=[]
    def generate(text):
        generated.append(text)
        return CODE
    result=env[3]["run_jwave_code"](generate(context["generation_context"]),
        scenario_type=prepared["scenario_type"],params_json=prepared["candidate_parameters_json"])
    assert generated==[original_context] and len(env[2])==1
    assert result["candidate_accumulation"]["recorded"]
    sid=result["candidate_accumulation"]["skill_id"]
    confirmed=env[1].record_feedback_statement(sid,result["run_id"],confirmed=True,request_id="pipeline-confirm",
                                               actor="internal-system",reason="manual review recorded")
    assert confirmed["recorded"] and not confirmed["trusted_run"]
    feedback=env[1].record_feedback_statement(sid,result["run_id"],confirmed=False,request_id="pipeline-review",
                                              actor="internal-system",reason="manual review withdrawn")
    assert feedback["recorded"] and not feedback["trusted_run"]
    assert candidate_intake.complete(result["run_id"])["skill_id"]==sid
    # Even after a recorded candidate and user statement, no code is injected.
    after=skill_retrieval.search(prepared["scenario_type"],prepared["retrieval_parameters_json"])
    assert after["skills"]==[] and after["fallback"] is True


def test_initial_pressure_adapter_preserves_source_scale_without_fake_frequency():
    params=dict(PARAMS);params.pop("source_frequency")
    params.update(source_kind="gaussian_initial_pressure",initial_pressure_pa=1,
                  initial_sigma_m=.001,initial_velocity="zero")
    prepared=adapter.main(json.dumps(params),"initial_pressure_2d")
    assert prepared["ready"]
    assert "source_frequency" not in json.loads(prepared["candidate_parameters_json"])
    assert json.loads(prepared["retrieval_parameters_json"])["source_type"]=="gaussian_initial_pressure"


def test_fixed_source_full_physical_query_matches_workflow_adapter_exactly():
    from app.controlled_dispatch import fixed_retrieval_parameters
    parameters=fixed_retrieval_parameters()
    inverse={v:k for k,v in adapter.ALIASES.items()}
    declared={inverse.get(k,k):v for k,v in parameters.items() if k!="dimensions"}
    got=adapter.main(json.dumps(declared),"initial_pressure_2d")
    assert got["ready"]
    assert json.loads(got["retrieval_parameters_json"])==parameters
    assert parameters["initial_sigma_m"]==.003
    assert parameters["source_geometry"]=="planar_x_uniform_y"
    assert parameters["source_index"]==[160,160]
    assert parameters["sensor_indices"]==[[208,160],[256,160]]
    assert not parameters["smooth_initial"]


@pytest.mark.parametrize("mutation",[{"source_geometry":"unknown"},{"sensor_indices":[[0,0]]},{"smooth_initial":"false"}])
def test_complete_physical_fields_never_accept_ambiguous_or_pml_geometry(mutation):
    from app.controlled_dispatch import fixed_retrieval_parameters
    parameters=fixed_retrieval_parameters()
    inverse={v:k for k,v in adapter.ALIASES.items()}
    declared={inverse.get(k,k):v for k,v in parameters.items() if k!="dimensions"}
    declared.update(mutation)
    assert not adapter.main(json.dumps(declared),"initial_pressure_2d")["ready"]

def test_description_is_bounded_metadata_not_physical_query():
    from scripts.workflow.skill_parameter_adapter import main
    from app.controlled_dispatch import fixed_retrieval_parameters
    query=fixed_retrieval_parameters()
    inverse={"sound_speed_m_s":"sound_speed","density_kg_m3":"density",
        "t_end_s":"t_end","grid_shape":"domain_N","dx_m":"domain_dx","source_type":"source_kind"}
    raw={inverse.get(k,k):v for k,v in query.items() if k!="dimensions"}
    plain=main(json.dumps(raw),"initial_pressure_2d")
    with_note=main(json.dumps(dict(raw,description="声明的仿真目标")),"initial_pressure_2d")
    assert plain["ready"] and with_note==plain
    assert not main(json.dumps(dict(raw,description="x"*513)),"initial_pressure_2d")["ready"]

def test_offline_parameter_contract_preserves_original_and_rejects_drift():
    import copy
    from scripts.workflow.skill_parameter_contract import patch,CONTRACT
    graph={"nodes":[{"id":"params","data":{"type":"llm","prompt_template":[
        {"role":"user","text":"original {{#user.text#}}"}]}}],"edges":[]}
    original=copy.deepcopy(graph)
    result=patch(graph,["params"])
    assert graph==original
    assert result["nodes"][0]["data"]["prompt_template"][0]["text"]=="original {{#user.text#}}"+CONTRACT
    assert patch(result,["params"])==result
    result["nodes"][0]["data"]["prompt_template"][0]["text"]+=" drift"
    with pytest.raises(ValueError,match="drift"):patch(result,["params"])
