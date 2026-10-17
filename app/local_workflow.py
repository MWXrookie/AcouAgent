"""Local stage-six orchestration using profiles and the existing executor.

Generated code crosses the same gateway-to-executor boundary. Deterministic
analysis decides success; model review can explain but cannot override it.
"""
from __future__ import annotations

import asyncio
import json
import re
import uuid
from functools import lru_cache
from pathlib import Path
from typing import Any, AsyncIterator

from app import analysis, cache_store, dashboard, execution, llm, model_client, model_profiles

MAX_QUERY_BYTES = 65536
MAX_CODE_BYTES = 20000
MAX_RETRIES = 2


class LocalWorkflowError(RuntimeError):
    pass


class _Registry:
    def __init__(self) -> None:
        self.functions: dict[str, Any] = {}

    def tool(self, fn):
        self.functions[fn.__name__] = fn
        return fn


@lru_cache(maxsize=1)
def _tool_functions() -> dict[str, Any]:
    from app import tools
    registry = _Registry()
    tools.register(registry)
    return registry.functions


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise LocalWorkflowError("duplicate_json_key")
        result[key] = value
    return result


def _reject_constant(value):
    raise LocalWorkflowError("nonfinite_json_value")


def _json_object(text: str) -> dict:
    cleaned = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.IGNORECASE)
    decoder = json.JSONDecoder(object_pairs_hook=_unique, parse_constant=_reject_constant)
    for index, char in enumerate(cleaned):
        if char != "{":
            continue
        try:
            value, end = decoder.raw_decode(cleaned[index:])
        except (json.JSONDecodeError, LocalWorkflowError):
            continue
        if isinstance(value, dict) and not cleaned[index + end:].strip():
            return value
    raise LocalWorkflowError("model_json_invalid")


def _knowledge() -> str:
    root = Path(__file__).resolve().parents[1] / "docs"
    parts = []
    for name in ("kb_jwave_reference.md", "kb_simulation_templates.md", "kb_physics_rules.md"):
        parts.append((root / name).read_text(encoding="utf-8")[:12000])
    return "\n\n".join(parts)


def _extraction_prompt(query: str) -> str:
    return f"""你是 jwave 0.2.1 声学仿真参数提取器。只输出一个 JSON 对象，不输出解释。
用户需求：{query}
输出结构：
{{"scenario_type":"point_source_2d|plane_wave_2d|interface_2d|initial_pressure_2d|attenuation_2d|point_source_3d|initial_pressure_3d",
"simulation_params":{{"sound_speed":1500.0,"density":1000.0,"source_frequency":1000000.0,"domain_N":[128,128],"domain_dx":[0.0002,0.0002],"t_end":0.00002,"cfl":0.3,"pml_size":12}},
"skill_query":{{"scenario_type":"同上","parameters":{{}}}},"description":"完整需求摘要"}}
simulation_params 使用 SI 单位并满足用户明确要求。skill_query.parameters 只有在能完整给出技能库规范参数时才填写，否则设为 null；不得猜测固定技能条件。"""


def _code_prompt(query: str, extracted: dict, context: str) -> str:
    return f"""你是 jwave 0.2.1 Python 代码生成器。输出只能是可执行 Python 源码；不要 Markdown、JSON 或解释。
规则：只使用已安装的 jwave/jax/numpy/matplotlib 与标准库；不得联网、读取系统文件或启动子进程；执行不超过30秒。
最后必须打印全时最大绝对压力场：
__ACOU_FIELD_START__
{{"shape":[...],"kind":"field","downsample":1,"max_pressure":0.0,"data":[[...]]}}
__ACOU_FIELD_END__
第一个字符必须是 import 或 #。
用户需求：{query}
服务端参数：{json.dumps(extracted, ensure_ascii=False, sort_keys=True)}
技能与生成上下文：{context}
已验证 API 资料：
{_knowledge()}"""


def _retry_context(query: str, extracted: dict, analyzed: dict) -> str:
    evidence = {key: analyzed.get(key) for key in (
        "verdict", "quality_level", "summary", "timing_diagnostic", "physics_checks"
    )}
    parameters = extracted.get("simulation_params", extracted)
    return (
        "不得删除压力场标记，不得联网、读取系统文件或启动子进程；保持用户参数不变。\n"
        f"参数：{llm._truncate_utf8(json.dumps(parameters, ensure_ascii=False, sort_keys=True), 8000)}\n"
        f"确定性分析：{llm._truncate_utf8(json.dumps(evidence, ensure_ascii=False, sort_keys=True), 6000)}\n"
        f"用户需求摘要：{llm._truncate_utf8(query, 1500)}"
    )


def _safe_analysis(analyzed: dict) -> dict:
    return {key: analyzed.get(key) for key in (
        "verdict", "quality_level", "max_pressure", "rms_pressure", "field_shape",
        "has_signal", "summary", "timing_diagnostic", "physics_checks"
    )}


def _skill_query(extracted: dict) -> tuple[str, str] | None:
    value = extracted.get("skill_query")
    if not isinstance(value, dict) or set(value) != {"scenario_type", "parameters"}:
        return None
    scenario, params = value.get("scenario_type"), value.get("parameters")
    if not isinstance(scenario, str) or not isinstance(params, dict) or not params:
        return None
    encoded = json.dumps(params, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"), allow_nan=False)
    return (scenario, encoded) if len(encoded.encode("utf-8")) <= 4096 else None


def _skill_notices(usage: dict) -> list[dict]:
    notices = []
    for skill in usage.get("skills", []) if isinstance(usage, dict) else []:
        if isinstance(skill, dict):
            notices.append({
                "stage": "reference_delivered", "skill_id": skill.get("skill_id"),
                "version": skill.get("version"),
                "name": skill.get("scope") or skill.get("scenario_type") or "固定技能",
                "message": "本地编排已交付经服务端重核的固定技能参考",
                "model_adoption_authenticated": False,
            })
    return notices[:3]


def _direct_reference(usage: dict, scenario: str, parameters_json: str,
                      original_context: str) -> tuple[dict | None, dict | None]:
    if not isinstance(usage, dict) or usage.get("reason") != "local_fixed_reference_delivery":
        return None, None
    decision_id, intent_id = usage.get("decision_id"), usage.get("generation_intent_id")
    if not isinstance(decision_id, str) or not isinstance(intent_id, str):
        return None, None
    from app.skill_local_execution import execute_reference
    outcome = execute_reference(decision_id=decision_id, generation_run_id=intent_id,
        scenario_type=scenario, parameters_json=parameters_json, original_context=original_context)
    if outcome.get("execution_reuse_verified") is not True:
        return None, outcome
    return dashboard.get_execution_by_run_id(outcome.get("run_id", "")), outcome


async def _model(profile_id: str | None, task: str, prompt: str, run_id: str) -> dict:
    return await model_client.complete(profile_id, task, [{"role": "user", "content": prompt}], run_id=run_id)


def _passed(result: dict | None, analyzed: dict) -> bool:
    return bool(result and result.get("exit_code") == 0 and result.get("timed_out") is False
                and analyzed.get("verdict") == "normal"
                and analyzed.get("quality_level") in {"Q1", "Q2", "Q3", "Q4"})


async def workflow_events(query: str, user: str, profile_id: str | None) -> AsyncIterator[dict]:
    del user  # session identity remains at the portal boundary; no model receives it.
    request_id, workflow_run_id = str(uuid.uuid4()), str(uuid.uuid4())
    notices: list[dict] = []
    if not isinstance(query, str) or not query.strip() or len(query.encode("utf-8")) > MAX_QUERY_BYTES:
        yield {"event": "result", "report": "请求为空或超过长度限制。", "workflow_status": "error",
               "request_id": request_id, "workflow_run_id": workflow_run_id, "skill_usage": []}
        return
    try:
        yield {"event": "progress", "stage": "本地编排：提取参数", "request_id": request_id,
               "workflow_run_id": workflow_run_id}
        extracted_response = await _model(profile_id, "parameter_extraction", _extraction_prompt(query), workflow_run_id)
        extracted = _json_object(extracted_response["content"])
        scenario, params = extracted.get("scenario_type"), extracted.get("simulation_params")
        if not isinstance(scenario, str) or not isinstance(params, dict):
            raise LocalWorkflowError("parameter_schema_invalid")
        params_json = json.dumps(params, ensure_ascii=False, sort_keys=True,
                                 separators=(",", ":"), allow_nan=False)
        validation = _tool_functions()["validate_simulation_params"](params_json)
        if validation.get("valid") is not True:
            detail = "；".join(item.get("message", "参数错误") for item in validation.get("errors", [])[:6])
            yield {"event": "result", "report": "参数物理检查未通过：" + detail,
                   "workflow_status": "error", "request_id": request_id,
                   "workflow_run_id": workflow_run_id, "skill_usage": [],
                   "model_profile": extracted_response["profile_id"], "orchestration_backend": "local"}
            return

        yield {"event": "progress", "stage": "本地编排：检索技能", "request_id": request_id,
               "workflow_run_id": workflow_run_id}
        original_context = query + "\n\n参数：" + params_json
        generation = {"context": original_context, "skill_usage": {
            "skills": [], "fallback": True, "reason": "skill_query_unavailable"}}
        query_data = _skill_query(extracted)
        if query_data is not None:
            from app.skill_retrieval import generation_context
            generation = await asyncio.to_thread(generation_context, original_context,
                scenario_type=query_data[0], parameters_json=query_data[1], _existing_store=True)
            notices = _skill_notices(generation.get("skill_usage", {}))
            for notice in notices:
                yield {"event": "skill_usage", "skill_usage": notice,
                       "request_id": request_id, "workflow_run_id": workflow_run_id}

        result, direct_outcome, code = None, None, ""
        if query_data is not None:
            result, direct_outcome = await asyncio.to_thread(_direct_reference,
                generation.get("skill_usage", {}), query_data[0], query_data[1], original_context)
        used_direct_reference = result is not None
        if result is None:
            yield {"event": "progress", "stage": "本地编排：生成代码", "request_id": request_id,
                   "workflow_run_id": workflow_run_id}
            generated = await _model(profile_id, "code_generation",
                _code_prompt(query, extracted, generation["context"]), workflow_run_id)
            code = execution._clean_code(generated["content"])
            if not code or len(code.encode("utf-8")) > MAX_CODE_BYTES:
                raise LocalWorkflowError("generated_code_invalid")

        analyzed: dict[str, Any] = {}
        pending_cache_update: tuple[str, str] | None = None
        first_code = code
        for attempt in range(MAX_RETRIES + 1):
            if result is None:
                yield {"event": "progress", "stage": f"本地编排：沙箱执行 {attempt + 1}/{MAX_RETRIES + 1}",
                       "request_id": request_id, "workflow_run_id": workflow_run_id}
                result = await asyncio.to_thread(_tool_functions()["run_jwave_code"],
                                                 code, 30, "", scenario, params_json)
            analyzed = analysis.analyze_and_record(result.get("stdout", "") or "",
                result.get("stderr", "") or "",
                int(result.get("exit_code") if result.get("exit_code") is not None else 1),
                params_json, result.get("run_id", "") or "")
            passed = _passed(result, analyzed)
            if pending_cache_update is not None:
                signature, fix_hint = pending_cache_update
                try:
                    await asyncio.to_thread(
                        cache_store._update_cache, signature, fix_hint, was_successful=passed
                    )
                except Exception:
                    # The cache is auxiliary; a locked/corrupt cache must not discard
                    # an already completed deterministic simulation result.
                    pass
                pending_cache_update = None
            if passed or used_direct_reference or attempt >= MAX_RETRIES:
                break
            yield {"event": "progress", "stage": "本地编排：修正代码", "request_id": request_id,
                   "workflow_run_id": workflow_run_id}
            repaired = await llm.fix_code(
                profile_id,
                code,
                result,
                run_id=workflow_run_id,
                requirements_context=_retry_context(query, extracted, analyzed),
            )
            code = execution._clean_code(str(repaired["code"] or ""))
            if not code or len(code.encode("utf-8")) > MAX_CODE_BYTES:
                raise LocalWorkflowError("repaired_code_invalid")
            if repaired.get("error_signature"):
                pending_cache_update = (str(repaired["error_signature"]), code[:500])
            result = None

        yield {"event": "progress", "stage": "本地编排：审查与解释", "request_id": request_id,
               "workflow_run_id": workflow_run_id}
        safe = json.dumps(_safe_analysis(analyzed), ensure_ascii=False, sort_keys=True)
        review = await _model(profile_id, "result_review",
            f"确定性物理门不能被覆盖。用户需求：{query}\n门控结果：{safe}\n简短说明通过或失败原因。", workflow_run_id)
        explanation = await _model(profile_id, "result_explanation",
            f"用3至6句中文解读，只引用给定数值；不得把normal/Q1/Q2写成完整物理正确证明。\n用户需求：{query}\n分析：{safe}", workflow_run_id)
        report = execution._build_report(result or {}, first_code or code)
        report += "\n\n## 确定性物理门\n\n" + str(analyzed.get("summary", "未获得分析摘要"))
        report += "\n\n质量等级：`" + str(analyzed.get("quality_level", "Q0")) + "`"
        report += "\n\n## 模型审查（不能覆盖物理门）\n\n" + review["content"]
        report += "\n\n## 物理解读\n\n" + explanation["content"]
        report += ("\n\n---\n\n本次本地编排模型：`" + review["profile_id"] + "` / `"
                   + review["model"] + "`。")
        yield {"event": "result", "report": report,
               "workflow_status": "succeeded" if _passed(result, analyzed) else "error",
               "request_id": request_id, "workflow_run_id": workflow_run_id,
               "skill_usage": notices, "model_profile": review["profile_id"],
               "model": review["model"], "orchestration_backend": "local",
               "quality_level": analyzed.get("quality_level", "Q0"),
               "run_id": (result or {}).get("run_id") or (direct_outcome or {}).get("run_id")}
    except model_profiles.ModelProfileError as exc:
        yield {"event": "result", "report": "模型配置不可用，请在模型设置中重新配置 BYOK，或检查环境变量模型 profile。",
               "workflow_status": "error", "request_id": request_id,
               "workflow_run_id": workflow_run_id, "skill_usage": [],
               "error_code": str(exc), "orchestration_backend": "local"}
    except (model_client.ModelCallError, LocalWorkflowError, ValueError, TypeError,
            OSError, RuntimeError) as exc:
        code = str(exc) if isinstance(exc, (model_client.ModelCallError, LocalWorkflowError)) else "local_workflow_failed"
        yield {"event": "result", "report": "本地编排未完成；本次结果不可作为成功仿真。",
               "workflow_status": "error", "request_id": request_id,
               "workflow_run_id": workflow_run_id, "skill_usage": notices,
               "error_code": code, "orchestration_backend": "local"}
