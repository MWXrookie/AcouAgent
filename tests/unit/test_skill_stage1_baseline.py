"""Stage-one artifact contract tests; synthetic inventory rows never grant runtime trust."""
import copy
import pytest
from app import skill_local_catalog as catalog
from scripts.validation import skill_stage1_baseline as stage

@pytest.fixture
def specs():
    return [catalog.specification(scope) for scope in catalog.SCOPES]

@pytest.fixture
def inputs(specs):
    rows=[dict(scope=s["scope"],name=s["state_text"],skill_id="id-"+str(i),version=1,
        status="active",quality_level="Q4",archived=0,latest_source_rechecked=True,
        numeric=dict(numeric_pass=True),parameters=s["parameters"],
        code_sha256=s["code_sha256"],validator_sha256=s["validator_sha256"],
        source_run_id="run-"+str(i),capsule_sha256="a"*64) for i,s in enumerate(specs)]
    return dict(specs=specs,observed=dict(items=rows,schema=list(range(1,18)),learning_enabled=False),
        runtime=dict(gateway={"id":"gateway"},executor={"id":"executor"},
            flags={"SKILL_LEARNING_ENABLED":"false"},deployed_source_sha256={"app/a.py":"a"*64}),
        workflows=[dict(role=r,node_count=1,edge_count=1) for r in ("draft","published")],
        sources={"app/a.py":"a"*64},head="b"*40,git_status="existing changes",
        observed_at="2026-10-12T00:00:00Z")

def test_freeze_has_seventeen_complete_rows_and_thirty_tasks(inputs):
    baseline,plan=stage.build(**inputs)
    stage.verify_seal(baseline);stage.verify_tasks(plan)
    assert baseline["inventory_count"]==17
    assert len(baseline["inventory"])==17
    assert all(r["applicable_boundary"]=="exact parameters only" for r in baseline["inventory"])
    assert baseline["task_plan_sha256"]==plan["sha256"]
    assert plan["future_ab_runs"]==180 and plan["executed_ab_runs"]==0
    assert baseline["formal_product_acceptance"] is False
    assert baseline["model_adoption_authenticated"] is False

def test_tasks_have_15_distinct_configs_and_five_paraphrases(specs):
    plan=stage.make_tasks(specs)
    exact={t["scope"]:t for t in plan["tasks"] if t["kind"]=="exact"}
    assert len(exact)==15
    assert len({t["parameters_sha256"] for t in exact.values()})==15
    for t in plan["tasks"]:
        source=exact[t["scope"]]
        if t["kind"]=="rephrase":
            assert t["parameters"]==source["parameters"]
            assert t["prompt"]!=source["prompt"]
        elif t["kind"]=="outside":
            changed=dict(source["parameters"],boundary_condition="open")
            assert t["parameters"]==changed and not t["expected_reference"]
        elif t["kind"]=="missing":
            changed=dict(source["parameters"]);changed.pop("boundary_condition")
            assert t["parameters"]==changed
            assert t["expected_behavior"]=="clarify_missing_boundary"

def test_all_three_media_in_negative_and_paraphrase_cases(specs):
    plan=stage.make_tasks(specs)
    for kind in ("rephrase","outside","missing"):
        scopes=[t["scope"] for t in plan["tasks"] if t["kind"]==kind]
        assert any("-water-" in s for s in scopes)
        assert any("-air-" in s for s in scopes)
        assert any("-soft_tissue-" in s for s in scopes)

def test_catalog_recipes_and_validator_pins_are_current(specs):
    for spec in specs:
        assert catalog.recipe(spec["scope"])
        assert spec["validator_pins"]

@pytest.mark.parametrize("field,value",[
    ("status","questioned"),("quality_level","Q2"),("archived",1),
    ("latest_source_rechecked",False),("source_run_id",""),("capsule_sha256",""),
    ("parameters",{}),("code_sha256","c"*64),("validator_sha256","c"*64)])
def test_reject_lifecycle_missing_source_or_drift(inputs,field,value):
    inputs["observed"]["items"][0][field]=value
    with pytest.raises(ValueError): stage.build(**inputs)

def test_failed_physical_recheck_cannot_freeze(inputs):
    inputs["observed"]["items"][0]["numeric"]["numeric_pass"]=False
    with pytest.raises(ValueError):stage.build(**inputs)

@pytest.mark.parametrize("mutation",["schema","learning","duplicate","deleted","missing_source_file",
                                    "deployed_hash","graph_role","empty_graph"])
def test_reject_environment_or_inventory_mismatch(inputs,mutation):
    if mutation=="schema":inputs["observed"]["schema"].pop()
    elif mutation=="learning":inputs["observed"]["learning_enabled"]=True
    elif mutation=="duplicate":inputs["observed"]["items"][1]=inputs["observed"]["items"][0]
    elif mutation=="deleted":inputs["observed"]["items"].pop()
    elif mutation=="missing_source_file":inputs["sources"]={}
    elif mutation=="deployed_hash":inputs["sources"]["app/a.py"]="c"*64
    elif mutation=="graph_role":inputs["workflows"][1]["role"]="draft"
    else:inputs["workflows"][0]["node_count"]=0
    with pytest.raises(ValueError):stage.build(**inputs)

def test_baseline_cannot_be_silently_edited(inputs):
    baseline,_=stage.build(**inputs);baseline["inventory"].pop()
    with pytest.raises(ValueError,match="snapshot_changed"):stage.verify_seal(baseline)

@pytest.mark.parametrize("mutation",["count","id","parameters","metric","tolerance","expectation"])
def test_plan_contract_rejects_changed_rows_even_if_resealed(specs,mutation):
    plan=stage.make_tasks(specs);plan.pop("sha256")
    if mutation=="count":plan["tasks"].pop()
    elif mutation=="id":plan["tasks"][1]["task_id"]=plan["tasks"][0]["task_id"]
    elif mutation=="parameters":plan["tasks"][0]["parameters"]["cfl"]=0.2
    elif mutation=="metric":plan["tasks"][0]["physical_contract"]["required_metrics"].pop()
    elif mutation=="tolerance":plan["tasks"][0]["physical_contract"]["tolerance_exclusive"]=0.02
    else:plan["tasks"][0]["expected_reference"]=False
    with pytest.raises(ValueError):stage.verify_tasks(stage.seal(plan))
