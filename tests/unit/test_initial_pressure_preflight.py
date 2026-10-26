"""Regressions for periodic initial conditions blocked before skill retrieval."""
import asyncio
import copy
import json

import pytest

from app import local_workflow, skill_local_catalog, skill_retrieval
from app.initial_pressure_validation import validate_initial
from app.physics_gate import assess_quality


@pytest.fixture
def validate():
    return local_workflow._tool_functions()["validate_simulation_params"]


@pytest.mark.parametrize("scope", skill_local_catalog.SCOPES)
def test_reviewed_initial_contracts_pass_without_driven_frequency(validate, scope):
    params = skill_local_catalog.specification(scope)["parameters"]
    before = copy.deepcopy(params)
    assert validate(json.dumps(params))["valid"]
    assert params == before and "source_frequency" not in params


def air():
    return copy.deepcopy(skill_local_catalog.specification(
        "managed-local-periodic-air-axial.v1")["parameters"])


def test_mcp_legacy_aliases_preserve_modal_contract(validate):
    p = air()
    for old, new in (("sound_speed_m_s", "sound_speed"), ("density_kg_m3", "density"),
                     ("grid_shape", "domain_N"), ("dx_m", "domain_dx"),
                     ("t_end_s", "t_end"), ("source_type", "source_kind")):
        p[new] = p.pop(old)
    assert validate(json.dumps(p))["valid"]


@pytest.mark.parametrize("change", [
    {"boundary_condition": "open"}, {"pml_size": 10}, {"cfl": .31},
    {"source_frequency": 1000}, {"source_frequency": None},
    {"modal_wavenumber_rad_per_m": [[1., 0.]]},
    {"modal_wavenumber_rad_per_m": [[8000., 0.]]},
    {"modal_amplitudes_pa": [0.]}, {"modal_amplitudes_pa": [True]},
    {"modal_amplitudes_pa": [float("nan")]},
    {"domain_N": [32, 32]}, {"domain_dx": [float("inf")]},
    {"sound_speed": 1500}, {"sensor_indices": [[64, 0]]},
    {"solver_mode": "frequency_domain"}, {"initial_velocity": "nonzero"},
])
def test_modal_contract_changes_fail_closed(validate, change):
    assert not validate(json.dumps(dict(air(), **change)))["valid"]


@pytest.mark.parametrize("field", ["boundary_condition", "modal_amplitudes_pa",
                                  "modal_wavenumber_rad_per_m", "t_end_s", "initial_pressure_pa"])
def test_missing_initial_fields_are_not_filled_from_catalog(validate, field):
    p = air()
    p.pop(field)
    assert not validate(json.dumps(p))["valid"]


def test_driven_source_still_requires_frequency(validate):
    p = {"sound_speed": 1500., "density": 1000., "domain_N": [64, 64],
         "domain_dx": [.0002, .0002]}
    result = validate(json.dumps(p))
    assert not result["valid"] and result["errors"][0]["field"] == "source_frequency"


def test_initial_preflight_does_not_promote_failed_or_unverified_output():
    good = dict(verdict="normal", has_signal=True, max_pressure=1., rms_pressure=.5, field_shape=[64, 64])
    kwargs = dict(exit_code=0, timed_out=False, analysis=good, params_json=json.dumps(air()))
    assert assess_quality(**kwargs)["quality_level"] == "Q2"
    assert assess_quality(**dict(kwargs, exit_code=1))["quality_level"] == "Q0"
    assert assess_quality(**dict(kwargs, analysis=dict(good, field_shape=[32, 32])))["quality_level"] == "Q1"
    assert assess_quality(**dict(kwargs, params_json=json.dumps(dict(air(), boundary_condition="open"))))["quality_level"] == "Q1"


def test_local_workflow_reaches_retrieval_with_actual_preflight(monkeypatch, validate):
    p = air()
    async def model(profile_id, task, prompt, run_id):
        if task == "parameter_extraction":
            return {"content": json.dumps({"scenario_type": "initial_pressure_2d", "simulation_params": p,
                                          "skill_query": None}), "profile_id": "test"}
        raise local_workflow.LocalWorkflowError("stop_after_retrieval")
    monkeypatch.setattr(local_workflow, "_model", model)
    async def collect():
        return [e async for e in local_workflow.workflow_events("periodic", "session", "test")]
    events = asyncio.run(collect())
    assert any(e.get("stage") == "本地编排：检索技能" for e in events)
    assert events[-1]["error_code"] == "stop_after_retrieval"

@pytest.mark.parametrize("boundary", ["periodic", "open", None])
def test_explicit_user_modal_contract_wins_over_model_defaults(monkeypatch, boundary):
    p = air()
    if boundary is None:
        p.pop("boundary_condition")
    else:
        p["boundary_condition"] = boundary
    async def model(profile_id, task, prompt, run_id):
        if task == "parameter_extraction":
            return {"content": json.dumps({"scenario_type": "initial_pressure_2d", "simulation_params": {
                "sound_speed": 343., "density": 1.225, "domain_N": [64,64], "domain_dx": [.0005,.0005]
            }, "skill_query": None}), "profile_id": "test"}
        raise local_workflow.LocalWorkflowError("stop_after_retrieval")
    observed = []
    actual = local_workflow._tool_functions()["validate_simulation_params"]
    monkeypatch.setattr(local_workflow, "_tool_functions", lambda: {
        "validate_simulation_params": lambda raw: (observed.append(json.loads(raw)) or actual(raw))
    })
    monkeypatch.setattr(local_workflow, "_model", model)
    def generation(original, **kwargs):
        assert json.loads(kwargs["parameters_json"]) == p
        return {"context": original, "skill_usage": {"skills": [], "fallback": True}}
    monkeypatch.setattr(skill_retrieval, "generation_context", generation)
    async def collect():
        return [e async for e in local_workflow.workflow_events("explicit: "+json.dumps(p), "session", "test")]
    events = asyncio.run(collect())
    assert observed == [p]
    reached = any(e.get("stage") == "本地编排：检索技能" for e in events)
    assert reached is (boundary == "periodic")


@pytest.mark.parametrize("amplitude", [1., 2.])
@pytest.mark.parametrize("extracted_scenario", ["initial_pressure_2d", "plane_wave_2d"])
@pytest.mark.parametrize("scope", ["managed-local-plane-wave.v1", "managed-local-interface-wide.v1"])
def test_copied_gaussian_contract_reaches_lookup_unchanged(monkeypatch, validate, amplitude, extracted_scenario, scope):
    spec = skill_local_catalog.specification(scope)
    p = copy.deepcopy(spec["parameters"])
    p["initial_pressure_pa"] = amplitude
    async def model(profile_id, task, prompt, run_id):
        if task == "parameter_extraction":
            return {"content": json.dumps({"scenario_type": extracted_scenario,
                "simulation_params": {"sound_speed": 1500., "density": 1000.},
                "skill_query": None}), "profile_id": "test"}
        raise local_workflow.LocalWorkflowError("stop_after_retrieval")
    observed = []
    def generation(original, **kwargs):
        observed.append(json.loads(kwargs["parameters_json"]))
        return {"context": original, "skill_usage": {"skills": [], "fallback": True}}
    monkeypatch.setattr(local_workflow, "_model", model)
    monkeypatch.setattr(skill_retrieval, "generation_context", generation)
    async def collect():
        query = "请按以下完整参数仿真：\n" + json.dumps(dict(p, scenario_type=spec["scene"]))
        return [e async for e in local_workflow.workflow_events(query, "session", "test")]
    events = asyncio.run(collect())
    assert observed == [p]
    assert events[-1]["error_code"] == "stop_after_retrieval"
    assert p["initial_pressure_pa"] == amplitude


@pytest.mark.parametrize("kind", ["duplicate", "nonfinite", "wrong_scenario"])
def test_bad_explicit_gaussian_declaration_fails_closed(kind):
    p = copy.deepcopy(skill_local_catalog.specification("managed-local-plane-wave.v1")["parameters"])
    p["scenario_type"] = "point_source_2d" if kind == "wrong_scenario" else "initial_pressure_2d"
    raw = json.dumps(p)
    if kind == "duplicate":
        raw = raw[:-1] + ', "initial_pressure_pa": 2}'
    elif kind == "nonfinite":
        p["initial_pressure_pa"] = float("nan")
        raw = json.dumps(p)
    with pytest.raises(local_workflow.LocalWorkflowError):
        local_workflow._explicit_modal_parameters("explicit: " + raw)


def test_copied_gaussian_omission_is_not_filled_from_catalog(validate):
    p = copy.deepcopy(skill_local_catalog.specification("managed-local-plane-wave.v1")["parameters"])
    del p["initial_sigma_m"]
    declared = local_workflow._explicit_modal_parameters(json.dumps(dict(p, scenario_type="initial_pressure_2d")))
    assert declared == p and "initial_sigma_m" not in declared
    assert not validate(json.dumps(declared))["valid"]

def test_fixed_reference_report_uses_analyzed_heatmap_without_mutating_history(monkeypatch):
    p = air()
    history = {"exit_code": 0, "timed_out": False, "run_id": "fixed-run", "stdout": "raw",
               "stderr": "", "image_base64": None}
    before = copy.deepcopy(history)
    async def model(profile_id, task, prompt, run_id):
        content = json.dumps({"scenario_type": "initial_pressure_2d", "simulation_params": p}) if task == "parameter_extraction" else "review"
        return {"content": content, "profile_id": "test", "model": "test"}
    monkeypatch.setattr(local_workflow, "_model", model)
    monkeypatch.setattr(skill_retrieval, "generation_context", lambda original, **kwargs: {
        "context": original, "skill_usage": {"skills": [], "fallback": False}})
    monkeypatch.setattr(local_workflow, "_direct_reference", lambda *args: (history, {}))
    monkeypatch.setattr(local_workflow.analysis, "analyze_and_record", lambda *args: {
        "verdict": "normal", "quality_level": "Q2", "summary": "checked", "heatmap_png_base64": "test-heatmap"})
    observed = []
    monkeypatch.setattr(local_workflow.execution, "_build_report",
                        lambda result, code: (observed.append(result) or "report"))
    async def collect():
        return [e async for e in local_workflow.workflow_events("explicit: "+json.dumps(p), "session", "test")]
    assert asyncio.run(collect())[-1]["workflow_status"] == "succeeded"
    assert observed[0]["image_base64"] == "test-heatmap"
    assert history == before
