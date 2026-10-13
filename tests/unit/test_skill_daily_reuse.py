import copy,json
import pytest
from scripts.workflow import skill_parameter_adapter as adapter,skill_daily_reuse_graph as graph
from scripts.workflow.skill_parameter_adapter import PERIODIC_PARAMETERS
def parameters(scope):return dict(PERIODIC_PARAMETERS[scope])
from tests.unit.test_periodic_skill_library import evidence
from app import analysis

SCOPE="managed-local-periodic-water-axial.v1"
def declared():
    return dict(parameters(SCOPE),scenario_type="initial_pressure_2d")
def test_actual_deepseek_transport_and_old_aliases():
    p=declared()
    for old,new in adapter.ALIASES.items():
        if new in p:p[old]=p.pop(new)
    p["source_frequency"]=None
    raw="<think>one closed transport block</think>"+json.dumps(p)
    got=adapter.main(raw,original_query=json.dumps(parameters(SCOPE)))
    assert got["ready"] and json.loads(got["retrieval_parameters_json"])==parameters(SCOPE)
@pytest.mark.parametrize("fault",["open_reasoning","two_reasoning","trailing_prose","duplicate","alias_collision","changed_boundary","missing","frequency","extra"])
def test_extractor_fails_closed_for_ambiguous_or_changed_input(fault):
    p=declared();raw=None
    if fault=="open_reasoning":raw="<think>"+json.dumps(p)
    if fault=="two_reasoning":raw="<think>a<think>b</think>"+json.dumps(p)
    if fault=="trailing_prose":raw=json.dumps(p)+" use it"
    if fault=="duplicate":raw=json.dumps(p)[:-1]+',"cfl":0.05}'
    if fault=="alias_collision":p["sound_speed"]=999
    if fault=="changed_boundary":p["boundary_condition"]="open"
    if fault=="missing":p.pop("boundary_condition")
    if fault=="frequency":p["source_frequency"]=10
    if fault=="extra":p["trusted_run"]=True
    original=parameters(SCOPE)
    if fault=="missing":original.pop("boundary_condition")
    assert not adapter.main(raw or json.dumps(p),original_query=json.dumps(original))["ready"]
def test_fixed_display_checks_all_fields_and_does_not_grant_trust():
    raw=json.dumps(evidence(SCOPE))
    payload,sensor,diagnostic=analysis._periodic_view(raw,"",0)
    assert payload["shape"]==[64,64] and len(sensor["pressure"])==len(evidence(SCOPE)["raw_evidence"]["time"])
    assert diagnostic["numeric_pass"] and not diagnostic["trusted_run"]
    corrupted=evidence(SCOPE);corrupted["raw_evidence"]["snapshots"][2][-1]+=0.1
    assert analysis._periodic_view(json.dumps(corrupted),"",0) is None
    assert analysis._periodic_view(raw,"failed",0) is None
    assert analysis._periodic_view(raw,"",1) is None
    assert analysis._periodic_view(raw+raw,"",0) is None
def fixture_graph():
    nodes=[];bindings=[]
    for branch in ("main","retry"):
        parameter="params-"+branch;ad="adapter-"+branch;gen="generator-"+branch;context="context-"+branch
        nodes += [{"id":parameter,"data":{"type":"llm","model":{"completion_params":{"temperature":.3}},"prompt_template":[{"role":"user","text":"extract"}]}},
            {"id":ad,"data":{"type":"code","variables":[{"variable":"parameters_json","value_selector":[parameter,"text"]}],"code":"old"}},
            {"id":gen,"data":{"type":"llm","prompt_template":[{"role":"user","text":"{{#"+context+".output#}}"}]}}]
        bindings.append(dict(parameter=parameter,adapter=ad,generator=gen,context=context))
    nodes += [{"id":"workflow_start","data":{"type":"start"}},{"id":"context-main","data":{"type":"variable-aggregator"}},
              {"id":"context-retry","data":{"type":"variable-aggregator"}}]
    return {"nodes":nodes,"edges":[]},bindings
def test_both_branches_patch_without_mutation_or_model_swap():
    original,bindings=fixture_graph();before=copy.deepcopy(original)
    got=graph.patch(original,bindings)
    assert original==before and graph.patch(got,bindings)==got
    for n in got["nodes"]:
        if n["id"].startswith("params"):
            assert n["data"]["model"]["completion_params"]=={"temperature":.3,"thinking":False}
@pytest.mark.parametrize("fault",["crossed_parameters","crossed_context","partial_prompt","duplicate_branch"])
def test_rejects_crossed_or_drifted_graph_before_platform_write(fault):
    g,b=fixture_graph()
    if fault=="crossed_parameters":g["nodes"][4]["data"]["variables"][0]["value_selector"]=["params-main","text"]
    if fault=="crossed_context":b[1]["context"]="context-main"
    if fault=="partial_prompt":g["nodes"][0]["data"]["prompt_template"][0]["text"]=graph.PARAMETER_SUFFIX+"drift"
    if fault=="duplicate_branch":b.append(b[0])
    with pytest.raises(ValueError):graph.patch(g,b)

def test_installed_dify_template_grammar_has_no_unresolved_hyphen_ids():
    import re
    original,bindings=fixture_graph()
    got=graph.normalize_template_ids(graph.patch(original,bindings))
    assert graph.normalize_template_ids(got)==got
    assert next(n for n in got["nodes"] if n["id"]=="adapter_retry")["data"]["variables"][0]["value_selector"]==["params_retry","text"]
    template=next(n for n in got["nodes"] if n["id"]=="generator_main")["data"]["prompt_template"][0]["text"]
    assert "{{#context_main.output#}}" in template
    pattern=re.compile(r"\{\{#([a-zA-Z0-9_]{1,50}(?:\.[a-zA-Z_][a-zA-Z0-9_]{0,29}){1,10})#\}\}")
    assert pattern.findall(template)==["context_main.output"]
def test_normalized_ids_must_not_collide():
    with pytest.raises(ValueError,match="collision"):
        graph.normalize_template_ids({"nodes":[{"id":"a-b"},{"id":"a_b"}],"edges":[]})

def test_report_projection_is_bounded_without_losing_original_evidence():
    from app.execution import _build_report
    raw=json.dumps(evidence(SCOPE))
    result=dict(stdout=raw,stderr="",exit_code=0,timed_out=False,total_attempts=1)
    before=copy.deepcopy(result)
    report=_build_report(result,"code")
    assert len(report.encode())<15000 and result==before
    assert "完整原始输出保留" in report


def test_explicit_query_prevents_missing_boundary_guess():
    p=parameters("managed-local-periodic-air-axial.v1");extracted=dict(p,scenario_type="initial_pressure_2d")
    missing=dict(p);missing.pop("boundary_condition")
    assert not adapter.main(json.dumps(extracted),original_query="请仿真："+json.dumps(missing))["ready"]
    incomplete=dict(extracted);incomplete.pop("dimensions")
    assert adapter.main(json.dumps(incomplete),original_query="请仿真："+json.dumps(p))["ready"]
    changed=dict(p,sound_speed_m_s=344)
    assert not adapter.main(json.dumps(extracted),original_query=json.dumps(changed))["ready"]

def test_analysis_prompt_projection_preserves_verdict_not_arrays():
    from scripts.workflow.skill_daily_reuse_graph import COMPACT_ANALYSIS
    ns={};exec(COMPACT_ANALYSIS,ns)
    raw={"verdict":"normal","max_pressure":1.,"field_data":[[1.]*64]*64,
         "diagnostics":{"numeric_pass":True},"skill_usage":{"reference_delivered":True}}
    result=json.loads(ns["main"]([raw])["summary"])
    assert result["verdict"]=="normal" and result["diagnostics"]["numeric_pass"]
    assert "field_data" not in result and len(raw["field_data"])==64
    assert json.loads(ns["main"]([raw,raw])["summary"])["verdict"]=="abnormal"
    fixed=dict(raw,fixed_numeric_diagnostic={"numeric_pass":True,"profile":SCOPE})
    fixed_result=json.loads(ns["main"]([fixed])["summary"])
    assert fixed_result["fixed_numeric_diagnostic"]["numeric_pass"] is True


def test_missing_platform_original_binding_is_closed():
    p=parameters("managed-local-periodic-air-axial.v1")
    assert not adapter.main(json.dumps(dict(p,scenario_type="initial_pressure_2d")),original_query=None)["ready"]
    aliased=dict(p,sound_speed=p["sound_speed_m_s"],scenario_type="initial_pressure_2d")
    assert adapter.main(json.dumps(aliased),original_query=json.dumps(p))["ready"]
    aliased["sound_speed"]=999
    assert not adapter.main(json.dumps(aliased),original_query=json.dumps(p))["ready"]


def test_empty_original_query_cannot_admit_model_catalog_parameters():
    assert not adapter.main(json.dumps(declared()),original_query="")["ready"]
    assert not adapter.main(json.dumps(declared()),original_query="   ")["ready"]


def test_verified_fixed_recipe_routes_directly_to_runner_without_generator():
    from app import skill_local_catalog as catalog
    from scripts.workflow.skill_handoff_wire import LOCAL_PREFIX
    code_text=catalog.recipe(SCOPE)
    spec=catalog.specification(SCOPE)
    original="original"
    reference={"skill_id":"11111111-1111-4111-8111-111111111111","version":1,
        "code":code_text,"code_sha256":spec["code_sha256"],
        "source_run_id":"22222222-2222-4222-8222-222222222222",
        "scenario_type":spec["scene"],"scope":SCOPE,"quality_level":"Q4",
        "status":"active","parameters_sha256":"a"*64,"actual_use_authenticated":False}
    context=original+LOCAL_PREFIX+json.dumps(reference,separators=(",",":"))
    wire_source=open("scripts/workflow/skill_handoff_wire.py",encoding="utf-8").read()
    base={"nodes":[
        {"id":"stage1_main_guard","data":{"type":"code"}},
        {"id":"local_basic_main_wire","data":{"type":"code","code":wire_source}},
        {"id":"local_basic_main_merge","data":{"type":"variable-aggregator"}},
        {"id":"1783991079665","data":{"type":"llm"}},
        {"id":"5655556822808","data":{"type":"tool","tool_parameters":{
            "code":{"type":"mixed","value":"{{#1783991079665.text#}}"}}}}],
        "edges":[{"id":"merge-gen","source":"local_basic_main_merge","target":"1783991079665"},
                 {"id":"gen-run","source":"1783991079665","target":"5655556822808"}]}
    patched=graph.fixed_recipe_route(base,[dict(branch="main",context="local_basic_main_merge",
        generator="1783991079665",runner="5655556822808")])
    nodes={node["id"]:node for node in patched["nodes"]}
    ns={};exec(nodes["daily_main_recipe"]["data"]["code"],ns)
    assert ns["main"](original,context)=={"ready":True,"code":code_text}
    assert ns["main"](original,original)=={"ready":False,"code":""}
    assert nodes["daily_main_code"]["data"]["variables"]==[
        ["1783991079665","text"],["daily_main_recipe","code"]]
    assert nodes["5655556822808"]["data"]["tool_parameters"]["code"]["value"]=="{{#daily_main_code.output#}}"
    edges={(edge["source"],edge["target"],edge.get("sourceHandle")) for edge in patched["edges"]}
    assert ("local_basic_main_merge","daily_main_recipe",None) in edges
    assert ("daily_main_route","1783991079665","false") in edges
    assert ("daily_main_route","daily_main_code","recipe") in edges


def test_direct_result_route_requires_normal_numeric_analysis_and_skips_llms():
    base={"nodes":[
        {"id":"daily_main_recipe","data":{"type":"code"}},
        {"id":"daily_analysis_summary","data":{"type":"code"}},
        {"id":"1786091111002","data":{"type":"tool"}},
        {"id":"5655556822808","data":{"type":"tool"}},
        {"id":"1786091111003","data":{"type":"llm"}},
        {"id":"1786091111004","data":{"type":"llm"}},
        {"id":"1786278074940","data":{"type":"code"}},
        {"id":"1783992518452","data":{"type":"end","outputs":[{
            "value_selector":["1786278074940","result"]}]}}],
        "edges":[
            {"id":"summary-review","source":"daily_analysis_summary","target":"1786091111003"},
            {"id":"explain-merge","source":"1786091111004","target":"1786278074940"},
            {"id":"merge-end","source":"1786278074940","target":"1783992518452"}]}
    patched=graph.fixed_result_route(base)
    nodes={node["id"]:node for node in patched["nodes"]}
    assert "fixed_numeric_diagnostic" in nodes["daily_analysis_summary"]["data"]["code"]
    ns={};exec(nodes["daily_main_result_guard"]["data"]["code"],ns)
    normal=json.dumps({"verdict":"normal","fixed_numeric_diagnostic":{"numeric_pass":True}})
    assert ns["main"](True,normal)=={"ready":True}
    assert ns["main"](False,normal)=={"ready":False}
    assert ns["main"](True,json.dumps({"verdict":"normal","diagnostics":{"numeric_pass":False}}))=={"ready":False}
    edges={(edge["source"],edge["target"],edge.get("sourceHandle")) for edge in patched["edges"]}
    assert ("daily_main_result_route","daily_main_direct_result","direct") in edges
    assert ("daily_main_result_route","1786091111003","false") in edges
    assert ("daily_main_direct_result","daily_main_result_merge","source") in edges
    assert nodes["1783992518452"]["data"]["outputs"][0]["value_selector"]==[
        "daily_main_result_merge","output"]
