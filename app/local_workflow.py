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
{{"scenario_type":"point_source_2d|plane_wave_2d|interface_2d|heterogeneous_2d|initial_pressure_2d|attenuation_2d|point_source_3d|initial_pressure_3d",
"simulation_params":{{"完整的物理参数键":"对应值"}},
"skill_query":{{"scenario_type":"同上","parameters":{{}}}},"description":"完整需求摘要"}}
simulation_params 必须包含完整物理合同，不能只把激励信息放入 skill_query。
驱动源需要 sound_speed、density、source_frequency、domain_N、domain_dx、t_end、cfl、pml_size。
初压激励没有驱动源频率，不得添加 source_frequency（含 null）。
周期初压必须在 simulation_params 保留 source_type=modal_initial_pressure、modal_amplitudes_pa、
modal_wavenumber_rad_per_m、boundary_condition、initial_velocity 及用户给出的全部 SI 参数。
高斯初压保留 source_type=gaussian_initial_pressure、initial_pressure_pa、initial_sigma_m 和零初速度。
用户提供完整 SI 字段时保留字段和值；缺项不得从技能名或技能库补齐。
二维频域稳态点源可选在顶层给出 result_spec；只提取用户明确声明的物理输入和结果量。
频域 simulation_params 使用完整 SI v2 字段：dimensions、solver_mode、medium_type、sound_speed_m_s、
density_kg_m3、grid_shape、dx_m、frequency_hz、pml_size、source_index、source_type、source_geometry、
helmholtz_source_coefficient、background_attenuation_db_mhz2_cm、material_regions、boundary_condition。
材料区域保留 start/stop 半开索引及其声速/密度；用户未明确声明均匀介质、无损或源系数时不能填默认值。
对于用户已明确的二维频域连续正弦点源，规范值是 dimensions=2、solver_mode=frequency_domain、
source_type=continuous_sinusoid、source_geometry=point、boundary_condition=absorbing_pml。
source_type描述时间激励，point属于source_geometry；不能把point_source写入source_type。
medium_type仅可使用water、homogeneous_water、homogeneous、heterogeneous、tissue或air。
用户明确说均匀水介质时使用homogeneous_water；不得另造uniform_water等枚举。
只在用户明确要求完整二维单频稳态复压力、轴序和数值源归一化约定时给出 result_spec：
{{"kind":"steady_complex_pressure","units":"numerical_source_normalization","axis_order":["x","y"],
"grid_shape":["用户网格x","用户网格y"],"full_complex":true,"downsample":1}}。
已有完整 result_spec 则原样保留；缺项不得从技能名、参考答案或已验证技能补齐。
当用户已明确要求二维频域、完整稳态复压力、x/y轴序、原网格不降采样和数值源归一化时，
必须在顶层输出 result_spec，不能因通用输出结构未列它而遗漏。
该频域分支输出完整 v2 simulation_params 和 result_spec，并将 skill_query 设为 null；
不要混入旧时域字段或另造一份不完整的 skill_query.parameters。
不得猜测材料区域、源强、吸收、单位或结果合同，也不得提供参考数组、答案图或用户自授质量等级。
skill_query.parameters 只有在能完整给出技能库规范参数时才填写，否则设为 null；不得猜测固定技能条件。"""


def _explicit_frequency_contract(query: str) -> dict | None:
    """Preserve a complete user declaration when the extraction model omits it.

    Like the modal declaration below, this only accepts explicit user bytes. No
    result fields, units, material or reference values are filled from a skill.
    """
    start = query.find("{")
    if start < 0:
        return None
    text = query[start:]
    if len(text.encode("utf-8")) > 4096:
        return None
    from app.skill_spec import _decode
    try:
        value = _decode(text)
    except ValueError:
        # An explicit v2-shaped document must not fall through to weaker legacy
        # parsing when malformed, duplicated, or nonfinite.
        if '"result_spec"' in text or '"normalizer_version"' in text:
            raise LocalWorkflowError("explicit_frequency_contract_invalid") from None
        return None
    if not {"simulation_params", "result_spec"} <= value.keys():
        return None
    if set(value) != {"scenario_type", "simulation_params", "result_spec"}:
        raise LocalWorkflowError("explicit_frequency_contract_invalid")
    from app.skill_general_library import normalize_query
    try:
        normalized = normalize_query(value["scenario_type"], json.dumps(dict(normalizer_version="normalizer_v2",
            parameters=value["simulation_params"], result_spec=value["result_spec"]), allow_nan=False))
    except (ValueError, TypeError, KeyError, OverflowError):
        raise LocalWorkflowError("explicit_frequency_contract_invalid") from None
    return dict(scenario_type=value["scenario_type"], simulation_params=normalized["parameters"],
                result_spec=normalized["result_spec"], skill_query=None)


def _explicit_initial_contract(query: str) -> dict | None:
    """Preserve copied Gaussian or modal initial conditions over model defaults."""
    start = query.find("{")
    if start < 0:
        return None
    text = query[start:]
    if len(text.encode("utf-8")) > 4096:
        return None
    try:
        value = json.loads(text, object_pairs_hook=_unique, parse_constant=_reject_constant)
    except (ValueError, LocalWorkflowError):
        if any(source in text for source in ('"modal_initial_pressure"', '"gaussian_initial_pressure"')):
            raise LocalWorkflowError("explicit_initial_contract_invalid") from None
        return None
    if isinstance(value, dict) and value.get("source_type") in ("modal_initial_pressure", "gaussian_initial_pressure"):
        # The skill UI includes scenario metadata beside the physical fields.
        value = dict(value)
        declared_scenario = value.pop("scenario_type", "initial_pressure_2d")
        allowed = {"initial_pressure_2d"}
        if value["source_type"] == "gaussian_initial_pressure":
            allowed.add("interface_2d")
        if declared_scenario not in allowed:
            raise LocalWorkflowError("declared_scenario_mismatch")
        return dict(scenario_type=declared_scenario, simulation_params=value)
    return None


def _explicit_modal_parameters(query: str) -> dict | None:
    """Parameter-only compatibility view of a checked initial declaration."""
    declaration = _explicit_initial_contract(query)
    return declaration["simulation_params"] if declaration is not None else None


def _code_prompt(query: str, extracted: dict, context: str) -> str:
    return f"""你是 jwave 0.2.1 Python 代码生成器。输出只能是可执行 Python 源码；不要 Markdown、JSON 或解释。
规则：只使用已安装的 jwave/jax/numpy/matplotlib 与标准库；不得联网、读取系统文件或启动子进程；执行不超过30秒。
网格坐标必须为 jnp.arange(N)*dx，或 jnp.linspace(0,N*dx,N,endpoint=False)，不得包含周期域右端点。
输出全时最大绝对压力场应对时间轴取 max(abs(p.params),axis=0)，不得用某个时间快照代替。
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
    query_summary = ("已提供结构化初压参数；纠错仅按上述物理参数执行。"
        if isinstance(parameters, dict) and parameters.get("source_type") == "modal_initial_pressure"
        else llm._truncate_utf8(query, 1500))
    return (
        "不得删除压力场标记，不得联网、读取系统文件或启动子进程；保持用户物理参数不变。\n"
        "纠错阶段必须修复失败的 API 调用；用户复现故障或仅首次运行的指令不适用于修复阶段。\n"
        f"参数：{llm._truncate_utf8(json.dumps(parameters, ensure_ascii=False, sort_keys=True), 8000)}\n"
        f"确定性分析：{llm._truncate_utf8(json.dumps(evidence, ensure_ascii=False, sort_keys=True), 6000)}\n"
        f"用户需求摘要：{query_summary}"
    )


def _safe_analysis(analyzed: dict) -> dict:
    return {key: analyzed.get(key) for key in (
        "verdict", "quality_level", "max_pressure", "rms_pressure", "field_shape",
        "has_signal", "summary", "timing_diagnostic", "physics_checks", "units", "result_kind", "metric_basis"
    )}


def _skill_query(extracted: dict) -> tuple[str, str] | None:
    from app import skill_general_library as general
    value = extracted.get("skill_query")
    if "result_spec" in extracted:
        params = extracted.get("simulation_params")
        envelope = dict(normalizer_version="normalizer_v2",parameters=params,
                        result_spec=extracted["result_spec"])
        try:
            scenario = extracted["scenario_type"]
            encoded = json.dumps(envelope,ensure_ascii=False,sort_keys=True,separators=(",",":"),allow_nan=False)
            normalized = general.normalize_query(scenario,encoded)
            if value is not None:
                if type(value) is not dict or set(value)!={"scenario_type","parameters"} or value["scenario_type"]!=scenario:
                    return None
                claimed=value["parameters"]
                if general.is_general_query(claimed):
                    claimed=general.normalize_query(scenario,json.dumps(claimed,allow_nan=False))
                    if claimed!=normalized:return None
                elif claimed!=params:return None
            return scenario,json.dumps(normalized,ensure_ascii=False,sort_keys=True,separators=(",",":"),allow_nan=False)
        except (ValueError,TypeError,KeyError,OverflowError,RecursionError):
            return None
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
                  "message": "本地编排已交付经服务端重核的技能参考",
                "model_adoption_authenticated": False,
            })
    return notices[:3]


def _direct_reference(usage: dict, scenario: str, parameters_json: str,
                      original_context: str) -> tuple[dict | None, dict | None]:
    if not isinstance(usage, dict) or usage.get("reason") not in {"local_fixed_reference_delivery","general_validated_reference_delivery"}:
        return None, None
    decision_id, intent_id = usage.get("decision_id"), usage.get("generation_intent_id")
    if not isinstance(decision_id, str) or not isinstance(intent_id, str):
        return None, None
    if usage["reason"] == "general_validated_reference_delivery":
        from app.skill_general_library import execute_reference
    else:
        from app.skill_local_execution import execute_reference
    outcome = execute_reference(decision_id=decision_id, generation_run_id=intent_id,
        scenario_type=scenario, parameters_json=parameters_json, original_context=original_context)
    if outcome.get("execution_reuse_verified") is not True:
        return None, outcome
    return dashboard.get_execution_by_run_id(outcome.get("run_id", "")), outcome


async def _model(profile_id: str | None, task: str, prompt: str, run_id: str) -> dict:
    return await model_client.complete(profile_id, task, [{"role": "user", "content": prompt}], run_id=run_id)


async def _commentary_model(profile_id, task, prompt, run_id, extracted_response):
    """Optional model prose cannot discard a completed deterministic execution."""
    try:
        return dict(await _model(profile_id, task, prompt, run_id), available=True)
    except (model_client.ModelCallError, model_profiles.ModelProfileError) as exc:
        allowed = {"model_transport_failed", "model_response_empty", "model_response_invalid",
                   "model_response_too_large", "model_output_truncated"}
        code = str(exc) if str(exc) in allowed else "model_commentary_unavailable"
        return dict(content="模型说明暂不可用（" + code + "）。请以实际执行记录和确定性物理门为准。",
            profile_id=extracted_response["profile_id"], model=extracted_response.get("model", "configured-model"),
            available=False)


def _passed(result: dict | None, analyzed: dict) -> bool:
    return bool(result and result.get("exit_code") == 0 and result.get("timed_out") is False
                and analyzed.get("verdict") == "normal"
                and analyzed.get("quality_level") in {"Q1", "Q2", "Q3", "Q4"})


def _auto_source_enabled() -> bool:
    # Avoid loading the new lifecycle when automatic learning is disabled.
    from app import config
    if config.SKILL_LEARNING_ENABLED is not True:
        return False
    from app.skill_auto_source import enabled
    return enabled() is True


def _learning_status(value: Any) -> dict:
    """Only bounded status facts may enter a public report or usage event."""
    source = value if isinstance(value, dict) else {}
    progress = source.get("progress")
    # A bounded scheduler tick can service an older source. Its success must
    # never be relabeled as this request's admission, including under races
    # with the background pump.
    same_job = (isinstance(progress, dict) and isinstance(source.get("job_id"), str)
                and progress.get("job_id") == source["job_id"])
    same_source = (isinstance(progress, dict) and isinstance(source.get("source_run_id"), str)
                   and progress.get("source_run_id") == source["source_run_id"])
    current = progress if same_job or same_source else source
    state = current.get("state")
    known_state = state in {"queued", "running", "needs_reconciliation", "cancel_requested",
                            "validated", "admitted", "cancelled", "failed", "rejected"}
    result = {
        "recorded": source.get("recorded") is True or source.get("observed") is True or current.get("observed") is True,
        "queued": state == "queued" if known_state else current.get("queued") is True,
        "validated": state in {"validated", "admitted"} if known_state else current.get("validated") is True,
        "admitted": state == "admitted" if known_state else current.get("admitted") is True,
    }
    reason = current.get("reason")
    result["reason"] = reason if isinstance(reason, str) and re.fullmatch(r"[a-z0-9_-]{1,80}", reason) else "learning_status_unavailable"
    counts = source.get("counts", current.get("counters", source.get("counters")))
    if isinstance(counts, dict):
        result["counts"] = {key: counts[key] for key in ("candidates", "validated", "admitted", "reused")
                            if type(counts.get(key)) is int and 0 <= counts[key] <= 2**53-1}
    return result


def _learning_message(status: dict) -> str:
    if status.get("admitted"):
        return "本次执行已记录，独立验证通过并接纳；后续精确请求可复用。首次执行结果仍为 Q2。"
    if status.get("validated"):
        return "独立验证已通过，技能尚未接纳；首次执行结果仍为 Q2。"
    if status.get("recorded") or status.get("queued"):
        return "本次执行已记录为学习候选，待独立验证与接纳；首次执行结果仍为 Q2。"
    return "首次仿真已完成，本次自动学习未接纳技能；首次执行结果仍为 Q2。"


async def workflow_events(query: str, user: str, profile_id: str | None) -> AsyncIterator[dict]:
    del user  # session identity remains at the portal boundary; no model receives it.
    request_id, workflow_run_id = str(uuid.uuid4()), str(uuid.uuid4())
    notices: list[dict] = []
    cache_queries = cache_hits = 0
    observed_errors: list[str] = []
    if not isinstance(query, str) or not query.strip() or len(query.encode("utf-8")) > MAX_QUERY_BYTES:
        yield {"event": "result", "report": "请求为空或超过长度限制。", "workflow_status": "error",
               "request_id": request_id, "workflow_run_id": workflow_run_id, "skill_usage": []}
        return
    try:
        yield {"event": "progress", "stage": "本地编排：提取参数", "request_id": request_id,
               "workflow_run_id": workflow_run_id}
        frequency = _explicit_frequency_contract(query)
        declared = _explicit_initial_contract(query)
        parameter_extraction_available = True
        try:
            extracted_response = await _model(profile_id, "parameter_extraction", _extraction_prompt(query), workflow_run_id)
        except model_client.ModelCallError:
            if frequency is None and declared is None:
                raise
            # No inference or catalog defaults: the original user declaration
            # still goes through the same validation, retrieval and execution.
            profile, _ = model_profiles.resolve(profile_id, "parameter_extraction")
            extracted_response = dict(content=json.dumps(frequency or declared, allow_nan=False),
                profile_id=profile.audit_profile_id(), model=profile.model)
            parameter_extraction_available = False
        extracted = _json_object(extracted_response["content"])
        scenario, params = extracted.get("scenario_type"), extracted.get("simulation_params")
        if not isinstance(scenario, str) or not isinstance(params, dict):
            raise LocalWorkflowError("parameter_schema_invalid")
        if frequency is not None:
            if scenario != frequency["scenario_type"]:
                raise LocalWorkflowError("declared_scenario_mismatch")
            extracted.update(frequency)
            params = frequency["simulation_params"]
        if declared is not None:
            # The declaration itself checks its scenario metadata. The model's
            # reclassification cannot override the user's explicit initial state.
            scenario = declared["scenario_type"]
            extracted["scenario_type"] = scenario
            from app.skill_store import normalize_parameters
            # Preserve omissions and changed boundaries; validation must reject them.
            params = declared["simulation_params"]
            extracted["simulation_params"] = params
            extracted["skill_query"] = {"scenario_type": scenario,
                                       "parameters": normalize_parameters(params)}
        params_json = json.dumps(params, ensure_ascii=False, sort_keys=True,
                                 separators=(",", ":"), allow_nan=False)
        if "result_spec" in extracted:
            query_contract = _skill_query(extracted)
            validation = {"valid": query_contract is not None, "errors": [{"message":"完整频域参数或检索合同不一致"}]}
        else:
            validation = _tool_functions()["validate_simulation_params"](params_json)
        if validation.get("valid") is not True:
            detail = "；".join(item.get("message", "参数错误") for item in validation.get("errors", [])[:6])
            yield {"event": "result", "report": "参数物理检查未通过：" + detail,
                   "workflow_status": "error", "request_id": request_id,
                   "workflow_run_id": workflow_run_id, "skill_usage": [],
                   "model_parameter_extraction_available": parameter_extraction_available,
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
        used_auto_source, learning_status = False, None
        if query_data is not None:
            result, direct_outcome = await asyncio.to_thread(_direct_reference,
                generation.get("skill_usage", {}), query_data[0], query_data[1], original_context)
        if ("result_spec" in extracted and query_data is not None and result is None
                and not (direct_outcome or {}).get("run_id") and _auto_source_enabled()):
            yield {"event": "progress", "stage": "本地编排：受控首次求解与自动学习", "request_id": request_id,
                   "workflow_run_id": workflow_run_id}
            from app.skill_auto_source import execute_source
            direct_outcome = await asyncio.to_thread(execute_source, scenario_type=query_data[0],
                parameters_json=query_data[1], request_key=request_id)
            if (isinstance(direct_outcome, dict) and direct_outcome.get("source_execution_verified") is True
                    and isinstance(direct_outcome.get("analysis"), dict)):
                result = dashboard.get_execution_by_run_id(direct_outcome.get("run_id", ""))
                used_auto_source = result is not None
                if used_auto_source:
                    learning_status = _learning_status(direct_outcome.get("learning"))
                    notice = {"stage": "automatic_learning", "message": _learning_message(learning_status),
                              "run_id": direct_outcome["run_id"], "learning": learning_status,
                              "model_adoption_authenticated": False}
                    notices.append(notice)
                    yield {"event": "skill_usage", "skill_usage": notice,
                           "request_id": request_id, "workflow_run_id": workflow_run_id}
        used_direct_reference = result is not None
        if "result_spec" in extracted and result is None:
            yield {"event": "result", "report": "未找到可用的已验证技能，需要先完成独立验证并接纳；如已派发执行，请检查本次执行记录。",
                   "workflow_status": "error", "request_id": request_id,
                   "workflow_run_id": workflow_run_id, "skill_usage": notices,
                   "quality_level": "Q0", "error_code": "validated_general_reference_required",
                   "run_id": (direct_outcome or {}).get("run_id"),
                   "orchestration_backend": "local"}
            return
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
            if used_auto_source:
                # First execution and later validation are distinct runs. A newly
                # admitted skill cannot retroactively promote this source run.
                analyzed = dict(direct_outcome["analysis"])
                analyzed["quality_level"] = "Q2"
            elif (used_direct_reference and generation.get("skill_usage", {}).get("reason") == "general_validated_reference_delivery"
                    and direct_outcome.get("execution_reuse_verified") is True
                    and isinstance(direct_outcome.get("analysis"), dict)):
                # This value comes from the internal reexecution verifier, never model output.
                analyzed = dict(direct_outcome["analysis"])
            else:
                analyzed = analysis.analyze_and_record(result.get("stdout", "") or "",
                    result.get("stderr", "") or "",
                    int(result.get("exit_code") if result.get("exit_code") is not None else 1),
                    params_json, result.get("run_id", "") or "")
            signature = cache_store._extract_error_signature(result.get("stderr", "") or "",
                result.get("exit_code"), result.get("stdout", "") or "")
            if signature:
                observed_errors.append(signature)
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
            cache_queries += 1
            if repaired.get("cache_hit") is True:
                cache_hits += 1
                yield {"event": "progress", "stage": "本地编排：纠错缓存命中，重新执行验证",
                       "request_id": request_id, "workflow_run_id": workflow_run_id}
            code = execution._clean_code(str(repaired["code"] or ""))
            if not code or len(code.encode("utf-8")) > MAX_CODE_BYTES:
                raise LocalWorkflowError("repaired_code_invalid")
            if repaired.get("error_signature"):
                pending_cache_update = (str(repaired["error_signature"]), code[:500])
            result = None

        yield {"event": "progress", "stage": "本地编排：审查与解释", "request_id": request_id,
               "workflow_run_id": workflow_run_id}
        safe = json.dumps(_safe_analysis(analyzed), ensure_ascii=False, sort_keys=True)
        facts = json.dumps({"attempts": attempt + 1, "observed_errors": observed_errors,
                            "cache_queries": cache_queries, "cache_hits": cache_hits}, ensure_ascii=False)
        review = await _commentary_model(profile_id, "result_review",
            f"确定性物理门不能被覆盖。用户需求：{query}\n门控结果：{safe}\n"
            f"服务端执行事实：{facts}。不得将需求中的计划、故障注入或重试描述为已经发生；只按执行事实陈述。\n简短说明通过或失败原因。", workflow_run_id, extracted_response)
        explanation = await _commentary_model(profile_id, "result_explanation",
            f"用3至6句中文解读，只引用给定数值；不得把normal/Q1/Q2写成完整物理正确证明。\n用户需求：{query}\n分析：{safe}\n"
            f"服务端执行事实：{facts}。不得将需求中的计划当作已发生的故障、修复或缓存命中。", workflow_run_id, extracted_response)
        report_result = dict(result or {})
        report_result["total_attempts"] = attempt + 1
        if used_auto_source or (used_direct_reference and generation.get("skill_usage", {}).get("reason") == "general_validated_reference_delivery"):
            # Preserve receipt-bound history; hide the full binary export only
            # in this presentation copy instead of dumping base64 into chat.
            report_result["stdout"] = re.sub(r"__SKILL_PROBE_START__.*?__SKILL_PROBE_END__",
                "[完整复数结果已保存]" if used_auto_source else "[完整复数结果已保存并独立验证]",
                report_result.get("stdout", "") or "", flags=re.DOTALL)
        if analyzed.get("result_kind") == "sensor_time":
            report_result["stdout"] = "[完整原始传感器波形保留在本次执行记录；图中展示两个传感器，非二维场]"
            report_result["display_kind"] = "sensor_time"
        display_image = analyzed.get("heatmap_png_base64") or analyzed.get("waveform_png_base64")
        if not report_result.get("image_base64") and display_image:
            report_result["image_base64"] = display_image
        report = execution._build_report(report_result, first_code or code)
        if not parameter_extraction_available:
            report += "\n\n参数提取模型暂不可用；本次仅按用户原始JSON进行参数校验、技能检索和实际执行，未补猜参数。"
        if cache_queries:
            report += (f"\n\n纠错缓存：查询尝试 {cache_queries} 次，命中 {cache_hits} 次。"
                       "提示仅辅助模型修复，每次修复仍重新执行和分析。")
        report += "\n\n## 确定性物理门\n\n" + str(analyzed.get("summary", "未获得分析摘要"))
        if analyzed.get("units") == "numerical_source_normalization":
            report += "\n\n场量单位：数值源归一化（numerical_source_normalization）；未标定为 Pa。"
        report += "\n\n质量等级：`" + str(analyzed.get("quality_level", "Q0")) + "`"
        if learning_status is not None:
            report += "\n\n自动学习：" + _learning_message(learning_status)
        if used_direct_reference and (direct_outcome or {}).get("execution_reuse_verified") is True:
            report += "\n\n技能复用：已重新实际执行，并通过本次独立核验。"
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
               "execution_reuse_verified": used_direct_reference and (direct_outcome or {}).get("execution_reuse_verified") is True,
               "model_review_available": review["available"],
               "model_parameter_extraction_available": parameter_extraction_available,
               "model_explanation_available": explanation["available"],
               **({"learning": learning_status} if learning_status is not None else {}),
               "cache_usage": {"queries": cache_queries, "hits": cache_hits},
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
