import asyncio
import json
from pathlib import Path

import pytest

from app import local_workflow, skill_retrieval

PARAMS = {
    "sound_speed": 1500.0,
    "density": 1000.0,
    "source_frequency": 1_000_000.0,
    "domain_N": [64, 64],
    "domain_dx": [0.0002, 0.0002],
    "t_end": 0.00001,
    "cfl": 0.3,
    "pml_size": 10,
}


def _responses():
    return {
        "parameter_extraction": json.dumps({
            "scenario_type": "point_source_2d",
            "simulation_params": PARAMS,
            "skill_query": None,
            "description": "test",
        }),
        "code_generation": "import json\nprint('ok')",
        "result_review": "确定性门通过。",
        "result_explanation": "压力场非零。该等级不代表完整物理证明。",
        "retry": "import json\nprint('retry')",
    }


def _collect(monkeypatch, analyzed):
    responses = _responses()
    async def fake_model(profile_id, task, prompt, run_id):
        return {"content": responses[task], "profile_id": profile_id or "fast",
                "model": "model-a", "task": task}
    monkeypatch.setattr(local_workflow, "_model", fake_model)
    monkeypatch.setattr(local_workflow, "_tool_functions", lambda: {
        "validate_simulation_params": lambda raw: {"valid": True, "errors": [], "warnings": []},
        "run_jwave_code": lambda *args: {
            "exit_code": 0, "timed_out": False, "duration_ms": 1,
            "stdout": "ok", "stderr": "", "run_id": "run-1",
        },
    })
    monkeypatch.setattr(local_workflow.analysis, "analyze_and_record", lambda *args: analyzed)
    monkeypatch.setattr(local_workflow.execution, "_build_report", lambda result, code: "base report")
    async def run():
        return [item async for item in local_workflow.workflow_events("simulate", "session", "fast")]
    return asyncio.run(run())


def test_local_workflow_runs_generation_executor_gate_and_profile(monkeypatch):
    analyzed = {"verdict": "normal", "quality_level": "Q2", "max_pressure": 1.0,
                "rms_pressure": 0.2, "field_shape": [64, 64], "has_signal": True,
                "summary": "finite field", "physics_checks": []}
    events = _collect(monkeypatch, analyzed)
    assert [item["stage"] for item in events if item["event"] == "progress"] == [
        "本地编排：提取参数", "本地编排：检索技能", "本地编排：生成代码",
        "本地编排：沙箱执行 1/3", "本地编排：审查与解释",
    ]
    result = events[-1]
    assert result["workflow_status"] == "succeeded"
    assert result["orchestration_backend"] == "local"
    assert result["model_profile"] == "fast" and result["quality_level"] == "Q2"
    assert "不能覆盖物理门" in result["report"]


def test_model_review_cannot_override_failed_physics_gate(monkeypatch):
    monkeypatch.setattr(local_workflow, "MAX_RETRIES", 0)
    analyzed = {"verdict": "abnormal", "quality_level": "Q0", "max_pressure": 0.0,
                "rms_pressure": 0.0, "field_shape": [], "has_signal": False,
                "summary": "no field", "physics_checks": []}
    result = _collect(monkeypatch, analyzed)[-1]
    assert result["workflow_status"] == "error"
    assert result["quality_level"] == "Q0"


def test_portal_source_sends_only_profile_id_not_browser_secret_fields():
    source = (Path(__file__).resolve().parents[2] / "app" / "dashboard.py").read_text(
        encoding="utf-8"
    )
    assert "model_profile: getModelProfile()" in source
    assert "localStorage.setItem('acouagent_byok" not in source
    for forbidden in ("model-key", "model-base", "getModelConfig", "acouagent_model_config"):
        assert forbidden not in source


def test_portal_source_offers_editable_deepseek_qwen_and_custom_byok_presets():
    source = (Path(__file__).resolve().parents[2] / "app" / "dashboard.py").read_text(
        encoding="utf-8"
    )
    for expected in (
        "DeepSeek 官方 API",
        "https://api.deepseek.com",
        "deepseek-chat",
        "deepseek-reasoner",
        "阿里云百炼 · Qwen（中国站）",
        "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "qwen-plus",
        "自定义 OpenAI 兼容端点",
        "自定义模型 ID",
    ):
        assert expected in source
    assert 'id="byok-base-url"' in source
    assert 'id="byok-api-key" type="password" autocomplete="new-password"' in source


def test_portal_model_connection_feedback_uses_compact_state_box():
    source = (Path(__file__).resolve().parents[2] / "app" / "dashboard.py").read_text(
        encoding="utf-8"
    )
    for state in ("idle", "pending", "success", "warning", "error"):
        assert f"'{state}'" in source or f'data-state="{state}"' in source
    assert 'class="model-connection-status"' in source
    assert "本次会话已安全注册" in source
    assert "发现本次浏览器会话配置" in source
    assert "请先测试连接；网关重启或配置到期后需要重新输入 API Key" in source
    assert "本次会话配置已失效，请重新输入 API Key 后测试" in source
    assert "byok_session_missing_or_expired" in source
    assert "配置已修改，请输入这组配置对应的 API Key" in source
    assert "项兼容检查通过" in source
    assert "未通过：" in source
    assert "本次会话已注册配置（详情已隐藏）" in source


def test_retry_context_keeps_parameters_and_analysis_ahead_of_long_query():
    context = local_workflow._retry_context(
        "用户需求" * 4_000,
        {"simulation_params": PARAMS, "description": "ignored" * 4_000},
        {"verdict": "abnormal", "quality_level": "Q0", "summary": "failed"},
    )

    assert context.index("参数：") < context.index("确定性分析：") < context.index("用户需求摘要：")
    assert '"source_frequency": 1000000.0' in context
    assert '"verdict": "abnormal"' in context
    assert len(context.encode("utf-8")) <= 16_000


@pytest.mark.parametrize("cache_write_fails", [False, True])
def test_failed_direct_reference_delivery_falls_back_with_full_retry_budget(
    monkeypatch, cache_write_fails
):
    responses = _responses()
    responses["parameter_extraction"] = json.dumps({
        "scenario_type": "point_source_2d",
        "simulation_params": PARAMS,
        "skill_query": {"scenario_type": "point_source_2d", "parameters": PARAMS},
        "description": "test",
    })
    model_tasks = []

    async def fake_model(profile_id, task, prompt, run_id):
        model_tasks.append(task)
        return {"content": responses[task], "profile_id": "fast", "model": "model-a", "task": task}

    execution_count = {"value": 0}

    def execute(*args):
        execution_count["value"] += 1
        passed = execution_count["value"] == 2
        return {"exit_code": 0 if passed else 1, "timed_out": False, "duration_ms": 1,
                "stdout": "ok" if passed else "failed", "stderr": "" if passed else "boom",
                "run_id": f"run-{execution_count['value']}"}

    analysis_count = {"value": 0}

    def analyze(*args):
        analysis_count["value"] += 1
        passed = analysis_count["value"] == 2
        return {"verdict": "normal" if passed else "abnormal",
                "quality_level": "Q2" if passed else "Q0", "max_pressure": 1.0 if passed else 0.0,
                "rms_pressure": 0.2 if passed else 0.0, "field_shape": [64, 64] if passed else [],
                "has_signal": passed, "summary": "pass" if passed else "fail", "physics_checks": []}

    monkeypatch.setattr(local_workflow, "_model", fake_model)
    async def fake_fix_code(profile_id, code, result, **kwargs):
        model_tasks.append("retry")
        return {"code": responses["retry"], "error_signature": "RuntimeError|general|boom"}
    cache_updates = []
    monkeypatch.setattr(local_workflow.llm, "fix_code", fake_fix_code)
    def update_cache(signature, hint, was_successful):
        if cache_write_fails:
            raise OSError("cache unavailable")
        cache_updates.append((signature, hint, was_successful))
    monkeypatch.setattr(local_workflow.cache_store, "_update_cache", update_cache)
    monkeypatch.setattr(local_workflow, "_tool_functions", lambda: {
        "validate_simulation_params": lambda raw: {"valid": True, "errors": [], "warnings": []},
        "run_jwave_code": execute,
    })
    monkeypatch.setattr(skill_retrieval, "generation_context", lambda *args, **kwargs: {
        "context": "verified lookup context",
        "skill_usage": {"reason": "local_fixed_reference_delivery", "skills": []},
    })
    monkeypatch.setattr(local_workflow, "_direct_reference",
                        lambda *args: (None, {"execution_reuse_verified": False, "reason": "stale"}))
    monkeypatch.setattr(local_workflow.analysis, "analyze_and_record", analyze)
    monkeypatch.setattr(local_workflow.execution, "_build_report", lambda result, code: "base report")

    async def run():
        return [item async for item in local_workflow.workflow_events("simulate", "session", "fast")]

    events = asyncio.run(run())
    assert execution_count["value"] == 2
    assert "retry" in model_tasks
    assert cache_updates == ([] if cache_write_fails else [
        ("RuntimeError|general|boom", responses["retry"][:500], True)
    ])
    assert events[-1]["workflow_status"] == "succeeded"


def test_verified_direct_reference_skips_generation_and_new_execution(monkeypatch):
    responses = _responses()
    responses["parameter_extraction"] = json.dumps({
        "scenario_type": "point_source_2d",
        "simulation_params": PARAMS,
        "skill_query": {"scenario_type": "point_source_2d", "parameters": PARAMS},
        "description": "test",
    })
    model_tasks = []

    async def fake_model(profile_id, task, prompt, run_id):
        model_tasks.append(task)
        return {"content": responses[task], "profile_id": "fast", "model": "model-a", "task": task}

    monkeypatch.setattr(local_workflow, "_model", fake_model)
    monkeypatch.setattr(local_workflow, "_tool_functions", lambda: {
        "validate_simulation_params": lambda raw: {"valid": True, "errors": [], "warnings": []},
        "run_jwave_code": lambda *args: (_ for _ in ()).throw(AssertionError("unexpected execution")),
    })
    monkeypatch.setattr(skill_retrieval, "generation_context", lambda *args, **kwargs: {
        "context": "verified lookup context",
        "skill_usage": {"reason": "local_fixed_reference_delivery", "skills": [{
            "skill_id": "skill-1", "version": 1, "scenario_type": "point_source_2d"}]},
    })
    direct_result = {"exit_code": 0, "timed_out": False, "duration_ms": 1,
                     "stdout": "ok", "stderr": "", "run_id": "direct-run"}
    monkeypatch.setattr(local_workflow, "_direct_reference",
                        lambda *args: (direct_result, {"execution_reuse_verified": True,
                                                       "run_id": "direct-run"}))
    monkeypatch.setattr(local_workflow.analysis, "analyze_and_record", lambda *args: {
        "verdict": "normal", "quality_level": "Q4", "max_pressure": 1.0,
        "rms_pressure": 0.2, "field_shape": [64, 64], "has_signal": True,
        "summary": "verified", "physics_checks": [],
    })
    monkeypatch.setattr(local_workflow.execution, "_build_report", lambda result, code: "base report")

    async def run():
        return [item async for item in local_workflow.workflow_events("simulate", "session", "fast")]

    events = asyncio.run(run())
    assert "code_generation" not in model_tasks and "retry" not in model_tasks
    assert events[-1]["workflow_status"] == "succeeded"
    assert events[-1]["quality_level"] == "Q4"
    assert events[-1]["run_id"] == "direct-run"
