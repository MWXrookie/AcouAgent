"""Automatic first solves remain distinct from later admission and actual reuse."""
import asyncio
import copy
import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace

import pytest

import app
from app import config, local_workflow as workflow, skill_retrieval


def spec():
    return json.loads((Path(__file__).resolve().parents[2] /
                       "docs/skills-next/examples/manual-skill.json").read_text(encoding="utf-8"))


@pytest.fixture
def flow(monkeypatch):
    model_calls, source_calls, reports = [], [], []
    declared = spec()
    extracted = dict(scenario_type=declared["scenario_type"], simulation_params=declared["parameters"],
                     result_spec=declared["result_spec"], skill_query=None)
    async def model(profile, task, prompt, run):
        model_calls.append((task, prompt))
        assert task != "code_generation"
        return {"content": json.dumps(extracted) if task == "parameter_extraction" else "server facts explained",
                "profile_id": "test", "model": "test"}
    monkeypatch.setattr(workflow, "_model", model)
    monkeypatch.setattr(skill_retrieval, "generation_context", lambda *a, **kw:
                        {"context": "unchanged", "skill_usage": {"skills": [], "fallback": True}})
    monkeypatch.setattr(workflow, "_direct_reference", lambda *a: (None, None))
    def forbidden(*a, **kw):
        raise AssertionError("The controlled first solve must not use the legacy generation, analysis or retry path")
    monkeypatch.setattr(workflow, "_tool_functions", forbidden)
    monkeypatch.setattr(workflow.analysis, "analyze_and_record", forbidden)
    monkeypatch.setattr(workflow.llm, "fix_code", forbidden)
    source = ModuleType("app.skill_auto_source")
    source.enabled = lambda: True
    monkeypatch.setitem(sys.modules, "app.skill_auto_source", source)
    monkeypatch.setattr(app, "skill_auto_source", source, raising=False)
    monkeypatch.setattr(config, "SKILL_LEARNING_ENABLED", True)
    raw_stdout = '__SKILL_PROBE_START__{"npy_zlib_base64":"full-binary-field"}__SKILL_PROBE_END__'
    history = dict(exit_code=0, timed_out=False, stdout=raw_stdout, stderr="", run_id="source-run", duration_ms=1)
    outcome = dict(source_execution_verified=True, run_id="source-run",
                   analysis=dict(verdict="normal", quality_level="Q2", max_pressure=1., rms_pressure=.2,
                                 field_shape=[128, 128], has_signal=True, units="numerical_source_normalization",
                                 summary="Controlled first solve; independent admission is separate.", heatmap_png_base64="png"),
                   learning=dict(recorded=True, queued=True, validated=True, admitted=True, reason="automatic_admission_complete"))
    def execute_source(**kwargs):
        source_calls.append(kwargs)
        return copy.deepcopy(outcome)
    source.execute_source = execute_source
    monkeypatch.setattr(workflow.dashboard, "get_execution_by_run_id", lambda run: copy.deepcopy(history) if run == history["run_id"] else None)
    def report(result, code):
        reports.append(copy.deepcopy(result))
        assert "full-binary-field" not in result["stdout"]
        return "source execution report"
    monkeypatch.setattr(workflow.execution, "_build_report", report)
    return dict(source=source, outcome=outcome, history=history, extracted=extracted,
                source_calls=source_calls, model_calls=model_calls, reports=reports, raw_stdout=raw_stdout)


def collect(query="完整明确的二维频域输入"):
    async def run():
        return [event async for event in workflow.workflow_events(query, "session", "test")]
    return asyncio.run(run())


def test_explicit_contract_survives_realistic_model_result_omission(flow):
    declared = spec()
    del flow["extracted"]["result_spec"]
    query = "请仿真以下明确物理输入\n" + json.dumps(dict(scenario_type=declared["scenario_type"],
        simulation_params=declared["parameters"], result_spec=declared["result_spec"]))
    result = collect(query)[-1]
    assert result["workflow_status"] == "succeeded" and result["quality_level"] == "Q2"
    assert result["execution_reuse_verified"] is False
    assert len(flow["source_calls"]) == 1


def test_explicit_frequency_contract_survives_unavailable_extraction(flow, monkeypatch):
    declared = spec()
    original = workflow._model
    async def model(profile, task, prompt, run):
        if task == "parameter_extraction":
            raise workflow.model_client.ModelCallError("model_transport_failed")
        return await original(profile, task, prompt, run)
    monkeypatch.setattr(workflow, "_model", model)
    monkeypatch.setattr(workflow.model_profiles, "resolve", lambda *args:
        (SimpleNamespace(model="test", audit_profile_id=lambda: "test"), None))
    query = "仿真\n" + json.dumps(dict(scenario_type=declared["scenario_type"],
        simulation_params=declared["parameters"], result_spec=declared["result_spec"]))
    result = collect(query)[-1]
    assert result["workflow_status"] == "succeeded" and result["quality_level"] == "Q2"
    assert result["model_parameter_extraction_available"] is False
    assert "未补猜参数" in result["report"] and len(flow["source_calls"]) == 1


def test_model_geometry_name_cannot_replace_time_excitation_type(flow):
    flow["extracted"]["simulation_params"]["source_type"] = "point_source"
    result = collect()[-1]
    assert result["workflow_status"] == "error"
    assert not flow["source_calls"]


def test_first_source_is_q2_even_when_its_learned_skill_is_admitted(flow):
    # Defensive: even a future source helper must not retroactively label this run Q4.
    flow["outcome"]["analysis"]["quality_level"] = "Q4"
    events = collect()
    result = events[-1]
    assert result["workflow_status"] == "succeeded"
    assert result["quality_level"] == "Q2" and result["run_id"] == "source-run"
    assert result["learning"]["admitted"] is True
    assert "首次执行结果仍为 Q2" in result["report"]
    assert "numerical_source_normalization" in result["report"]
    assert "full-binary-field" not in result["report"]
    assert "full-binary-field" in flow["history"]["stdout"]
    assert len(flow["source_calls"]) == 1
    call = flow["source_calls"][0]
    assert call["request_key"] == result["request_id"]
    assert json.loads(call["parameters_json"])["parameters"] == flow["extracted"]["simulation_params"]
    assert [task for task, _ in flow["model_calls"]] == ["parameter_extraction", "result_review", "result_explanation"]
    assert any(event["event"] == "skill_usage" and event["skill_usage"]["stage"] == "automatic_learning" for event in events)


def test_learning_auxiliary_failure_preserves_successful_source_and_redacts_details(flow):
    flow["outcome"]["learning"] = dict(admitted=False, reason="SQL failure with secret body", error="sensitive-key",
                                         counts={"candidates": 1, "validated": True, "admitted": 0, "raw": "secret"})
    result = collect()[-1]
    assert result["workflow_status"] == "succeeded" and result["quality_level"] == "Q2"
    assert result["learning"]["admitted"] is False
    assert result["learning"]["reason"] == "learning_status_unavailable"
    assert result["learning"]["counts"] == {"candidates": 1, "admitted": 0}
    assert "未接纳" in result["report"]
    assert "sensitive-key" not in json.dumps(result)
    assert "SQL failure" not in json.dumps(result)


def test_completed_tick_overrides_earlier_observe_queue_status(flow):
    flow["outcome"]["learning"] = dict(observed=True, queued=True, admitted=False, state="queued", job_id="job",
        reason="automatic_validation_queued", progress=dict(observed=True, queued=False, admitted=True,
        state="admitted", job_id="job", reason="automatic_admission_complete"), counts=dict(candidates=1, validated=1, admitted=1, reused=0))
    result = collect()[-1]
    assert result["learning"] == dict(recorded=True, queued=False, validated=True, admitted=True,
        reason="automatic_admission_complete", counts=dict(candidates=1, validated=1, admitted=1, reused=0))
    assert "后续精确请求可复用" in result["report"]


def test_terminal_failed_tick_never_inherits_an_earlier_admission_flag(flow):
    flow["outcome"]["learning"] = dict(observed=True, admitted=True, job_id="job",
        progress=dict(state="rejected", job_id="job", reason="automatic_job_terminal"))
    result = collect()[-1]
    assert result["learning"]["admitted"] is False and result["learning"]["validated"] is False
    assert result["workflow_status"] == "succeeded" and result["quality_level"] == "Q2"


def test_scheduler_success_for_another_job_does_not_claim_this_source_admitted(flow):
    flow["outcome"]["learning"] = dict(observed=True, queued=True, admitted=False, state="queued", job_id="this-job",
        reason="automatic_validation_queued", progress=dict(state="admitted", admitted=True, job_id="older-job", reason="automatic_admission_complete"))
    result = collect()[-1]
    assert result["learning"]["queued"] is True and result["learning"]["admitted"] is False
    assert "待独立验证与接纳" in result["report"]


def test_no_job_tick_does_not_erase_already_recorded_admission(flow):
    flow["outcome"]["learning"] = dict(observed=True, state="admitted", admitted=True, job_id="this-job",
        reason="automatic_source_replayed", progress=dict(observed=False, admitted=False, reason="no_automatic_job"))
    result = collect()[-1]
    assert result["learning"]["admitted"] is True and result["learning"]["validated"] is True


@pytest.mark.parametrize("gate", ["learning_flag", "source_enabled"])
def test_disabled_learning_keeps_stage3_no_reference_behavior(flow, monkeypatch, gate):
    if gate == "learning_flag":
        monkeypatch.setattr(config, "SKILL_LEARNING_ENABLED", False)
    else:
        flow["source"].enabled = lambda: False
    result = collect()[-1]
    assert result["workflow_status"] == "error"
    assert result["error_code"] == "validated_general_reference_required"
    assert not flow["source_calls"]


def test_previously_dispatched_reference_failure_never_starts_another_source(flow, monkeypatch):
    monkeypatch.setattr(workflow, "_direct_reference", lambda *a: (None, {"run_id": "already-dispatched"}))
    result = collect()[-1]
    assert result["workflow_status"] == "error" and result["run_id"] == "already-dispatched"
    assert not flow["source_calls"]


def test_existing_verified_reference_remains_the_only_execution(flow, monkeypatch):
    usage = dict(reason="general_validated_reference_delivery", skills=[])
    monkeypatch.setattr(skill_retrieval, "generation_context", lambda *a, **kw: {"context": "server reference", "skill_usage": usage})
    outcome = dict(execution_reuse_verified=True, analysis=copy.deepcopy(flow["outcome"]["analysis"]))
    outcome["analysis"]["quality_level"] = "Q4"
    monkeypatch.setattr(workflow, "_direct_reference", lambda *a: (copy.deepcopy(flow["history"]), outcome))
    result = collect()[-1]
    assert result["workflow_status"] == "succeeded" and result["quality_level"] == "Q4"
    assert not flow["source_calls"]
    assert "learning" not in result


@pytest.mark.parametrize("failure", ["unverified", "missing_history", "missing_analysis"])
def test_source_cannot_report_success_without_verified_unique_history(flow, monkeypatch, failure):
    if failure == "unverified":
        flow["outcome"]["source_execution_verified"] = False
    elif failure == "missing_analysis":
        flow["outcome"].pop("analysis")
    else:
        monkeypatch.setattr(workflow.dashboard, "get_execution_by_run_id", lambda run: None)
    result = collect()[-1]
    assert result["workflow_status"] == "error" and result["run_id"] == "source-run"
    assert len(flow["source_calls"]) == 1
    assert [task for task, _ in flow["model_calls"]] == ["parameter_extraction"]


def test_explicit_frequency_contract_prompt_never_supplies_defaults_or_reference_arrays():
    prompt = workflow._extraction_prompt("user request")
    for field in ("result_spec", "material_regions", "helmholtz_source_coefficient", "background_attenuation_db_mhz2_cm", "frequency_hz"):
        assert field in prompt
    assert "不能填默认值" in prompt and "参考数组" in prompt
    assert "initial_pressure_pa" in prompt  # existing initial-pressure declaration remains
