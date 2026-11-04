import json
import uuid

from cores.jwave_flow.workflow.config import Settings
from cores.jwave_flow.workflow.graph import SimulationWorkflow, _parse_extraction
from cores.jwave_flow.workflow.llm import CompletionReview
from cores.jwave_flow.workflow.skill_client import SkillClientError


PARAMS = {
    "sound_speed": 1500,
    "density": 1000,
    "frequency": 2_000_000,
    "grid_size": [128, 128],
    "dx": 0.0001,
    "t_end": 0.00004,
}


def test_default_settings_use_full_skill_flow():
    assert Settings().skill_mode == "full"


def test_legacy_parameter_output_remains_compatible():
    legacy = "{\"frequency\":2000000,\"grid_size\":[128,128]}"
    params_json, skill_query = _parse_extraction(legacy)
    assert params_json == legacy
    assert skill_query == ""


class LLM:
    def __init__(self):
        self.calls = 0

    def invoke(self, prompt):
        self.calls += 1
        if self.calls == 1:
            return type("Message", (), {"content": "二维点源仿真需求"})()
        envelope = {
            "params_json": PARAMS,
            "skill_query": {
                "scenario_type": "point_source_2d",
                "parameters": {
                    "sound_speed_m_s": 1500,
                    "density_kg_m3": 1000,
                    "source_frequency_hz": 2_000_000,
                    "grid_shape": [128, 128],
                    "dx_m": 0.0001,
                    "t_end_s": 0.00004,
                },
                "result_spec": None,
            },
        }
        return type("Message", (), {"content": json.dumps(envelope)})()


class Retriever:
    def invoke(self, query):
        return []


class Executor:
    def run(self, code):
        raise AssertionError("reference path must not execute generated code")

    def close(self):
        return None


class SkillPort:
    def resolve(self, **kwargs):
        return {
            "mode": "reference",
            "skill_ticket": "ticket",
            "skills": [{"skill_id": "skill-1", "version": 2, "quality_level": "Q4"}],
            "skill_usage": [{
                "stage": "reference_selected",
                "skill_id": "skill-1",
                "version": 2,
                "message": "selected",
            }],
            "guidance_context": "",
        }

    def execute_reference(self, **kwargs):
        return {
            "execution_reuse_verified": True,
            "run_id": "run-1",
            "result": {
                "exit_code": 0,
                "stdout": "pressure=1",
                "stderr": "",
                "timed_out": False,
                "duration_ms": 12,
            },
            "analysis": {"quality_level": "Q4", "summary": "verified"},
        }


def test_reference_path_skips_generation_and_executor(monkeypatch):
    import cores.jwave_flow.workflow.graph as graph_module

    monkeypatch.setattr(
        graph_module,
        "invoke_completion_review",
        lambda *args, **kwargs: CompletionReview(completed=True, missing_requirements=[], feedback=""),
    )
    workflow = SimulationWorkflow(
        Settings(skill_mode="reference", semantic_check_enabled=True),
        analyst_llm=LLM(),
        coder_llm=object(),
        retriever=Retriever(),
        executor=Executor(),
        skill_client=SkillPort(),
    )
    updates = list(workflow.stream("仿真 2 MHz 点源", workflow_run_id=str(uuid.uuid4())))
    nodes = [node for update in updates for node in update]
    assert "skill_resolve" in nodes
    assert "skill_execute" in nodes
    assert "generate" not in nodes
    assert "run_code" not in nodes
    final = {}
    for update in updates:
        for patch in update.values():
            if isinstance(patch, dict):
                final.update(patch)
    assert final["execution_reuse_verified"] is True
    assert final["run_id"] == "run-1"
    assert final["verified"] is True


def test_resolve_failure_falls_back_without_aborting():
    class FailingPort:
        def resolve(self, **kwargs):
            raise SkillClientError("skill_port_unavailable")

    workflow = SimulationWorkflow(
        Settings(skill_mode="reference"),
        analyst_llm=LLM(),
        coder_llm=object(),
        retriever=Retriever(),
        executor=Executor(),
        skill_client=FailingPort(),
    )
    state = {
        "query": "仿真",
        "workflow_run_id": str(uuid.uuid4()),
        "skill_query_json": json.dumps({
            "scenario_type": "point_source_2d",
            "parameters": {"frequency_hz": 2_000_000},
        }),
    }
    result = workflow.n_skill_resolve(state)
    assert result["skill_mode"] == "fallback"
    assert result["skill_error"] == "skill_port_unavailable"


def test_guidance_is_injected_into_generation_prompt(monkeypatch):
    import cores.jwave_flow.workflow.graph as graph_module

    captured = {}

    def fake_invoke(llm, prompt, schema, keys):
        captured["prompt"] = prompt
        return "print('ok')"

    monkeypatch.setattr(graph_module, "invoke_code_llm", fake_invoke)
    workflow = SimulationWorkflow(
        Settings(skill_mode="full"),
        analyst_llm=LLM(),
        coder_llm=object(),
        retriever=Retriever(),
        executor=Executor(),
        skill_client=SkillPort(),
    )
    workflow.n_generate({
        "query": "仿真",
        "params_json": json.dumps(PARAMS),
        "skill_guidance": "{\"mode\":\"metadata_only\"}",
        "param_constraints": [],
        "param_warnings": [],
    })
    rendered = "\n".join(
        getattr(item, "content", str(item)) for item in captured["prompt"]
    )
    assert "同场景技能约束" in rendered
    assert "不得声称已经复用技能代码" in rendered


def test_finalize_rejects_success_when_parameter_precheck_failed():
    workflow = SimulationWorkflow(
        Settings(skill_mode="full"),
        analyst_llm=LLM(),
        coder_llm=object(),
        retriever=Retriever(),
        executor=Executor(),
        skill_client=SkillPort(),
    )
    output = workflow.n_finalize({
        "semantic_verified": True,
        "verified": True,
        "param_check_ok": False,
        "requirement_completed": True,
    })
    assert output["verified"] is False
