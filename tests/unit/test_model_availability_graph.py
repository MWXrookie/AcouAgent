import copy
import json
from pathlib import Path

import pytest

from scripts.workflow import model_availability_graph as module

ROOT = Path(__file__).resolve().parents[2]
TASKS = json.loads((ROOT / "docs/validation/stage1/tasks.json").read_text(encoding="utf-8"))["tasks"]
BINDINGS = dict(parameter="1783991048409", review="1786091111003", explanation="1786091111004")


def run_source(source):
    namespace = {}
    exec(compile(source, "<trusted-model-routing>", "exec"), namespace)
    return namespace["main"]


@pytest.mark.parametrize("task", [t for t in TASKS if t["kind"] != "outside"], ids=lambda t: t["task_id"])
def test_extraction_failure_only_reuses_validated_user_physics(task):
    fallback = run_source(module.guard_source() + module.FALLBACK_WRAPPER)
    if task["kind"] == "missing":
        with pytest.raises(ValueError, match="explicit_initial_contract_invalid"):
            fallback(task["prompt"])
    else:
        result = fallback(task["prompt"])["packet"]
        expected = json.loads(task["prompt"][task["prompt"].find("{"):])
        actual = json.loads(result["parameters_json"])
        assert actual.pop("scenario_type") == "initial_pressure_2d"
        assert actual == expected and result["model_available"] is False


@pytest.mark.parametrize("query", ["请仿真水中的点声源", '{"frequency_hz":2000000}', '{"source_type":"modal_initial_pressure"}'])
def test_no_guessing_on_unavailable_extraction(query):
    with pytest.raises(ValueError):
        run_source(module.guard_source() + module.FALLBACK_WRAPPER)(query)


def test_success_is_unchanged_and_status_note_cannot_raise_quality():
    packet = run_source(module.SUCCESS_CODE)("not JSON")
    assert packet == {"packet": {"parameters_json": "not JSON", "model_available": True}}
    report = run_source(module.REPORT_CODE)
    assert report("原始物理门：abnormal / Q0", True)["report"] == "原始物理门：abnormal / Q0"
    result = report("原始物理门：abnormal / Q0", False)
    assert result["report"].startswith("原始物理门：abnormal / Q0") and "未补猜" in result["report"]
    assert result["model_parameter_extraction_available"] is False
    with pytest.raises(ValueError):
        report("report", "false")


def graph():
    return json.loads((ROOT / "docs/dify_workflow_backup/2026-10-26-initial-guard/candidate-after.json").read_text(encoding="utf-8"))


def test_real_graph_routes_success_and_error_and_labels_both_end_reports():
    original = graph()
    before = copy.deepcopy(original)
    result = module.patch(original, **BINDINGS)
    assert original == before
    nodes = {n["id"]: n["data"] for n in result["nodes"]}
    assert len(result["nodes"]) == 69 and len(result["edges"]) == 77
    edges = [e for e in result["edges"] if e["source"] == BINDINGS["parameter"]]
    assert {(e["sourceHandle"], e["target"]) for e in edges} == {
        ("source", module.SUCCESS), ("fail-branch", module.FALLBACK)}
    assert nodes[BINDINGS["parameter"]]["error_strategy"] == "fail-branch"
    for key in (BINDINGS["review"], BINDINGS["explanation"]):
        assert nodes[key]["error_strategy"] == "default-value"
        assert "暂不可用" in nodes[key]["default_value"][0]["value"]
    for node in original["nodes"]:
        if node["data"]["type"] == "llm" and node["id"] not in BINDINGS.values():
            assert nodes[node["id"]].get("error_strategy") == node["data"].get("error_strategy")
    for data in nodes.values():
        if data["type"] == "end":
            assert any(x["variable"] == "model_parameter_extraction_available" for x in data["outputs"])
    adapter = nodes["stage1_params_main"]
    assert next(x["value_selector"] for x in adapter["variables"] if x["variable"] == "parameters_json") == [module.AGGREGATOR, "output", "parameters_json"]
    assert nodes[module.FALLBACK]["variables"][0]["value_selector"][-1] == "query"
    assert nodes[module.FALLBACK].get("error_strategy") is None


@pytest.mark.parametrize("case", ["duplicate-binding", "wrong-node", "guard-drift", "error-strategy", "reapply"])
def test_ambiguous_or_drifted_graph_never_silently_changes(case):
    value = graph()
    bindings = dict(BINDINGS)
    index = {n["id"]: n for n in value["nodes"]}
    if case == "duplicate-binding":
        bindings["review"] = bindings["parameter"]
    elif case == "wrong-node":
        bindings["parameter"] = "stage1_params_main"
    elif case == "guard-drift":
        index[module.GUARD_ID]["data"]["code"] += "\n# drift"
    elif case == "error-strategy":
        index[bindings["parameter"]]["data"]["error_strategy"] = "default-value"
    else:
        value = module.patch(value, **bindings)
    before = copy.deepcopy(value)
    with pytest.raises(ValueError):
        module.patch(value, **bindings)
    assert value == before
