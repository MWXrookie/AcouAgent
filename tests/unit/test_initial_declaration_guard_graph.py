import copy
import json
from pathlib import Path

import pytest

from scripts.workflow.initial_declaration_guard_graph import GUARD_ID, guard_source, patch

ROOT = Path(__file__).resolve().parents[2]
TASKS = json.loads((ROOT / "docs/validation/stage1/tasks.json").read_text(encoding="utf-8"))["tasks"]


def guard():
    namespace = {}
    exec(compile(guard_source(), "<trusted-shared-validator>", "exec"), namespace)
    return namespace["main"]


@pytest.mark.parametrize("task", [t for t in TASKS if t["kind"] != "outside"], ids=lambda t: t["task_id"])
def test_original_fixed_contracts_and_missing_declarations(task):
    run = guard()
    if task["kind"] == "missing":
        with pytest.raises(ValueError, match="explicit_initial_contract_invalid:boundary_condition"):
            run(task["prompt"])
    else:
        assert run(task["prompt"]) == {"checked": True}


@pytest.mark.parametrize("field", ["t_end_s", "cfl", "density_kg_m3", "grid_shape", "dx_m",
                                   "modal_amplitudes_pa", "modal_wavenumber_rad_per_m", "initial_velocity"])
def test_user_omissions_are_not_filled_from_model_or_catalog(field):
    params = json.loads(TASKS[0]["prompt"][TASKS[0]["prompt"].find("{"):])
    params.pop(field)
    with pytest.raises(ValueError, match="explicit_initial_contract_invalid"):
        guard()("明确声明：" + json.dumps(params))


@pytest.mark.parametrize("query", [
    '{"source_type":"modal_initial_pressure","boundary_condition":"periodic","boundary_condition":"periodic"}',
    '{"source_type":"modal_initial_pressure","cfl":NaN}',
    '{"source_type":"modal_initial_pressure"} trailing',
    '{"source_type":"modal_initial_pressure",',
    '{"source_type":"modal_initial_pressure","pad":"' + 'x' * 8192 + '"}',
])
def test_malformed_explicit_initial_declarations_stop(query):
    with pytest.raises(ValueError, match="explicit_initial_declaration_invalid"):
        guard()(query)


def test_wrapped_initial_declaration_and_nondeclaration_behavior():
    run = guard()
    params = json.loads(TASKS[0]["prompt"][TASKS[0]["prompt"].find("{"):])
    assert run(json.dumps({"scenario_type": "initial_pressure_2d", "simulation_params": params})) == {"checked": True}
    for query in ("请仿真一个二维声学点源", '{"frequency_hz":2000000}', "约束 x { y }"):
        assert run(query) == {"checked": True}  # existing downstream validation still required
    with pytest.raises(ValueError, match="initial_declaration_query_budget"):
        run("x" * 65537)


def graph():
    return {
        "nodes": [
            {"id": "start", "data": {"type": "start", "variables": [{"variable": "query"}]}},
            {"id": "a", "data": {"type": "llm", "model": {"name": "deepseek-v4-flash", "provider": "langgenius/deepseek/deepseek"}}},
            {"id": "b", "data": {"type": "end", "outputs": [{"variable": "answer", "value_selector": ["a", "text"]}]}},
        ],
        "edges": [{"id": "s-a", "source": "start", "target": "a", "data": {"sourceType": "start", "targetType": "llm"}},
                  {"id": "s-b", "source": "start", "target": "b", "data": {"sourceType": "start", "targetType": "end"}}],
        "viewport": {"x": 10, "y": 20},
    }


def test_all_start_paths_guarded_without_changing_existing_selectors_or_nodes():
    original = graph()
    before = copy.deepcopy(original)
    result = patch(original)
    assert original == before
    assert result["nodes"][:3] == before["nodes"]
    assert result["viewport"] == before["viewport"]
    assert [e["target"] for e in result["edges"] if e["source"] == "start"] == [GUARD_ID]
    assert {e["target"] for e in result["edges"] if e["source"] == GUARD_ID} == {"a", "b"}
    assert patch(result) == result
    assert result["nodes"][-1]["data"].get("error_strategy") is None


@pytest.mark.parametrize("change", ["duplicate", "no-query", "incoming-start", "guard-source", "bypass"])
def test_graph_drift_is_rejected(change):
    value = graph()
    if change == "duplicate":
        value["nodes"].append(copy.deepcopy(value["nodes"][0]))
    elif change == "no-query":
        value["nodes"][0]["data"]["variables"] = []
    elif change == "incoming-start":
        value["edges"].append({"id": "back", "source": "a", "target": "start"})
    else:
        value = patch(value)
        if change == "guard-source":
            value["nodes"][-1]["data"]["code"] += "\n# drift"
        else:
            value["edges"].append({"id": "bypass", "source": "start", "target": "a"})
    with pytest.raises(ValueError):
        patch(value)


def test_native_model_migration_requires_explicit_flag_and_known_provider():
    value = graph()
    assert patch(value)["nodes"][1]["data"]["model"]["name"] == "deepseek-v4-flash"
    migrated = patch(value, native_deepseek_flash=True)
    assert migrated["nodes"][1]["data"]["model"]["name"] == "deepseek-flash"
    assert patch(migrated, native_deepseek_flash=True) == migrated
    value["nodes"][1]["data"]["model"]["provider"] = "another-provider"
    with pytest.raises(ValueError, match="unexpected_deepseek_provider"):
        patch(value, native_deepseek_flash=True)
