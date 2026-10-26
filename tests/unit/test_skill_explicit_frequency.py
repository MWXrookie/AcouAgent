"""User-declared physical inputs survive model omission; malformed inputs fail closed."""
import copy
import json
from pathlib import Path

import pytest

from app import local_workflow as flow


def declaration():
    spec = json.loads((Path(__file__).resolve().parents[2] / "docs/skills-next/examples/manual-skill.json").read_text(encoding="utf-8"))
    return dict(scenario_type=spec["scenario_type"], simulation_params=spec["parameters"], result_spec=spec["result_spec"])


def test_complete_explicit_contract_preserved_without_defaults():
    value = declaration()
    result = flow._explicit_frequency_contract("请实际仿真并复用以下完整参数\n" + json.dumps(value))
    assert result["simulation_params"] == value["simulation_params"]
    assert result["result_spec"] == value["result_spec"]
    assert result["skill_query"] is None


@pytest.mark.parametrize("kind", ["missing", "unit", "shape", "nonfinite", "duplicate", "trust", "resolution"])
def test_invalid_explicit_inputs_cannot_drop_to_legacy(kind):
    value = declaration()
    if kind == "missing":
        del value["simulation_params"]["helmholtz_source_coefficient"]
    elif kind == "unit":
        value["result_spec"]["units"] = "Pa"
    elif kind == "shape":
        value["result_spec"]["grid_shape"] = [64, 64]
    elif kind == "nonfinite":
        value["simulation_params"]["frequency_hz"] = float("nan")
    elif kind == "trust":
        value["quality_level"] = "Q4"
    elif kind == "resolution":
        value["simulation_params"]["frequency_hz"] = 1e9
    raw = json.dumps(value)
    if kind == "duplicate":
        raw = raw.replace('"scenario_type":', '"scenario_type":"point_source_2d","scenario_type":', 1)
    with pytest.raises(flow.LocalWorkflowError, match="explicit_frequency_contract_invalid"):
        flow._explicit_frequency_contract("仿真：" + raw)


def test_plain_natural_language_and_legacy_inputs_unchanged():
    assert flow._explicit_frequency_contract("仿真 1 MHz 声波") is None
    assert flow._explicit_frequency_contract('{"source_type":"modal_initial_pressure"}') is None
