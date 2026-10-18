"""MCP 执行、只读检索与默认关闭的服务端技能生成上下文工具。

通过 `register(mcp)` 向网关注册；T-007 analyze_simulation_result 将落在
`analysis.py`（本模块保持聚焦仿真执行类工具）。
"""

import json
import uuid
from typing import Any

import httpx

from app import (
    cache_store,
    candidate_intake,
    config,
    execution,
    llm,
    model_profiles,
    runtime_registry_service,
)
from app.dashboard import record_execution


def _record_dispatch_completion(result: dict[str, Any], run_id: str) -> None:
    """Bind a matched approval window to an immutable per-dispatch receipt.

    Runs after the dispatch, when the run id is known. Absent or unmatched
    approval wiring is a no-op: nothing is written and no database is created.
    """
    evidence = result.get("execution_evidence")
    usage = evidence.get("approval_usage") if isinstance(evidence, dict) else None
    if not isinstance(usage, dict):
        return
    nonce = evidence.get("gateway_request_nonce", "")
    usage["completion"] = runtime_registry_service.record_dispatch_completion(usage, nonce, run_id)


def _finalize_skill_learning(result: dict[str, Any], run_id: str,
                             candidate_context: dict[str, Any] | None) -> None:
    """Finalize one recorded execution through the internal learning gates.

    Exact server-owned profiles get the deterministic Q4 path first. Generic
    generated code can only become a Q2 candidate. The two paths are ordered so
    a successful Q4 admission is never followed by a lower-grade write for the
    same run. Optional storage failures never change the execution result.
    """
    learning_enabled = (config.SKILL_LIBRARY_ENABLED is True
                        and config.SKILL_LEARNING_ENABLED is True)
    fixed = None
    if config.fixed_intake_enabled():
        from app import skill_local_profile
        fixed = skill_local_profile.record(run_id)
        result["local_skill_intake"] = fixed
        if fixed.get("recorded") and (fixed.get("admitted") or fixed.get("duplicate")):
            if learning_enabled:
                result["skill_learning"] = {
                    "recorded": True,
                    "skill_id": fixed.get("skill_id"),
                    "status": "active",
                    "quality_level": fixed.get("quality_level", "Q4"),
                    "reason": fixed.get("reason"),
                }
            return
    if candidate_context is not None:
        candidate = candidate_intake.complete(run_id)
        result["candidate_accumulation"] = candidate
        result["skill_learning"] = {
            "recorded": candidate.get("recorded") is True,
            "skill_id": candidate.get("skill_id"),
            "status": "candidate" if candidate.get("recorded") is True else None,
            "quality_level": "Q2" if candidate.get("recorded") is True else None,
            "reason": candidate.get("reason"),
        }
    elif fixed is not None and learning_enabled:
        result["skill_learning"] = {
            "recorded": False,
            "skill_id": None,
            "status": None,
            "quality_level": None,
            "reason": fixed.get("reason", "learning_not_applicable"),
        }


def register(mcp) -> None:
    """向 FastMCP 实例注册本模块的全部工具。"""

    @mcp.tool
    def search_simulation_skill(scenario_type: str, parameters_json: str, limit: int = 3) -> dict[str, Any]:
        """Read-only skill lookup after parameter extraction, before generation.

        Default disabled; returns no reusable code until provenance is authenticated.
        Limit is 1–3. No learning, activation, database path or trust flag inputs.
        """
        from app.skill_retrieval import search
        return search(scenario_type, parameters_json, limit)

    @mcp.tool
    def build_simulation_generation_context(original_context: str, scenario_type: str,
                                            parameters_json: str) -> dict[str, Any]:
        """Workflow-only generation context, default off with unchanged input.

        Uses only revalidated server-held local profiles or signed fixed-profile evidence. No caller code,
        grade, proof, trust boolean, database path or execution inputs. Search
        remains a separate read-only tool. A handoff audits reference delivery,
        not actual model/executor/user use; it never runs simulation code.
        """
        if type(original_context) is not str or len(original_context.encode("utf-8"))>65536:
            raise ValueError("generation_context_budget")
        from app.skill_deadline import limited
        from app.skill_retrieval import generation_context
        try:
            with limited(2):
                return generation_context(original_context,scenario_type=scenario_type,
                                          parameters_json=parameters_json,_existing_store=True)
        except TimeoutError:
            return {"context":original_context,"skill_usage":{"skills":[],"fallback":True,
                "reason":"generation_context_deadline","actual_use_authenticated":False}}

    @mcp.tool
    def read_simulation_generation_context(original_context: str, scenario_type: str,
                                           parameters_json: str, decision_id: str,
                                           generation_run_id: str) -> str:
        """Read-only committed handoff pointer, freshly reverified; default off.

        No caller code, proof or trust flags. Invalid/expired/withdrawn pointers
        return the original context. This never executes simulation code.
        """
        if type(original_context) is not str or len(original_context.encode("utf-8"))>65536:
            raise ValueError("generation_context_budget")
        from app.skill_deadline import limited
        from app.skill_reference_consumer import read_committed_context
        try:
            with limited(2):
                return read_committed_context(decision_id=decision_id,generation_run_id=generation_run_id,
                    scenario_type=scenario_type,parameters_json=parameters_json,original_context=original_context)
        except TimeoutError:
            return original_context

    @mcp.tool
    def list_installed_libraries() -> dict[str, Any]:
        """Report the packaged environment available in this gateway image."""
        return {
            "fastmcp": "3.4.6",
            "code_tool": "run_jwave_code",
            "auto_fix_tool": "run_jwave_code_with_retry",
            "analysis_tool": "analyze_simulation_result",
            "validator_tool": "validate_simulation_params",
            "model_runtime": "openai-compatible-byok",
            "configured_environment_profiles": len(model_profiles.load_profiles()),
        }

    @mcp.tool
    def jwave_environment() -> dict[str, Any]:
        """Report the packaged jwave environment and JAX device status."""
        response = httpx.get(
            f"{config.EXECUTOR_URL}/health",
            headers={"X-Executor-Token": config.EXECUTOR_SHARED_TOKEN},
            timeout=10,
        )
        response.raise_for_status()
        return response.json()

    @mcp.tool
    def run_jwave_code(code: str, timeout_seconds: int = 15, run_id: str = "",
                       scenario_type: str = "", params_json: str = "{}") -> dict[str, Any]:
        """Run Python; optional scenario/parameters record an unverified Q2 candidate.

        Learning requires both server switches. Declared parameters never confer trust.
        """
        if not isinstance(code, str) or not code.strip():
            raise ValueError("code must be a non-empty Python string")
        code = execution._clean_code(code)
        if len(code.encode("utf-8")) > 20000:
            raise ValueError("code is limited to 20000 UTF-8 bytes")
        if not 1 <= timeout_seconds <= 30:
            raise ValueError("timeout_seconds must be between 1 and 30")
        run_id = run_id or str(uuid.uuid4())
        candidate_context = candidate_intake.prepare(code, scenario_type, params_json)
        result = execution._execute_code(code, timeout_seconds)
        _record_dispatch_completion(result, run_id)
        result["stdout"] = execution._shrink_field_in_stdout(result.get("stdout", ""))
        execution._normalize_result_artifacts(result)
        record_execution(
            tool_name="run_jwave_code",
            code=code,
            exit_code=result.get("exit_code"),
            timed_out=result.get("timed_out", False),
            duration_ms=result.get("duration_ms"),
            stdout=result.get("stdout", ""),
            stderr=result.get("stderr", ""),
            attempt_count=1,
            image_base64=result.get("image_base64"),
            run_id=run_id,
            execution_evidence=result.get("execution_evidence"),
            candidate_context=candidate_context,
        )
        result["run_id"] = run_id
        _finalize_skill_learning(result, run_id, candidate_context)
        return result

    @mcp.tool
    def run_jwave_code_with_retry(
        code: str,
        timeout_seconds: int = 15,
        max_retries: int | None = None,
        run_id: str = "",
        scenario_type: str = "",
        params_json: str = "{}",
        model_profile: str = "",
    ) -> dict[str, Any]:
        """Run Python code and auto-fix errors using the selected BYOK profile.

        Executes the code in the sandboxed jwave environment.  If the code fails
        (non-zero exit code or timeout), the tool sends the error output to the model
        and asks it to produce a corrected version, then re-executes.  This loop
        continues until the code succeeds or *max_retries* is exhausted.

        Returns a dict with ``final_code`` (the last version tried), ``history``
        (one entry per attempt), and the fields from the final execution.
        """
        if max_retries is None:
            max_retries = config.CODE_RETRY_MAX
        if not isinstance(code, str) or not code.strip():
            raise ValueError("code must be a non-empty Python string")
        code = execution._clean_code(code)
        if len(code.encode("utf-8")) > 20000:
            raise ValueError("code is limited to 20000 UTF-8 bytes")
        if not 1 <= timeout_seconds <= 30:
            raise ValueError("timeout_seconds must be between 1 and 30")
        if not 0 <= max_retries <= 7:
            raise ValueError("max_retries must be between 0 and 7")

        resolved_profile, _ = model_profiles.resolve(model_profile or None, "retry")

        run_id = run_id or str(uuid.uuid4())
        current_code = code
        history: list[dict[str, Any]] = []
        last_error_sig: str | None = None

        for attempt in range(max_retries + 1):  # 首次 + N 次重试
            candidate_context = candidate_intake.prepare(current_code, scenario_type, params_json)
            result = execution._execute_code(current_code, timeout_seconds)
            _record_dispatch_completion(result, run_id)
            result["stdout"] = execution._shrink_field_in_stdout(result.get("stdout", ""))
            execution._normalize_result_artifacts(result)
            step = {
                "attempt": attempt + 1,
                "code": current_code,
                "exit_code": result["exit_code"],
                "timed_out": result["timed_out"],
                "duration_ms": result.get("duration_ms"),
                "stdout_tail": result.get("stdout", "")[-2000:],
                "stderr_tail": result.get("stderr", "")[-2000:],
            }
            history.append(step)

            # 成功 —— 直接返回
            if result["exit_code"] == 0 and not result["timed_out"]:
                result["history"] = history
                result["final_code"] = current_code
                result["total_attempts"] = attempt + 1
                result["run_id"] = run_id
                result["report"] = execution._build_report(result, code)
                if last_error_sig:
                    try:
                        cache_store._update_cache(last_error_sig, current_code[:500], was_successful=True)
                    except Exception:
                        pass
                record_execution(
                    tool_name="run_jwave_code_with_retry",
                    code=current_code,
                    exit_code=result["exit_code"],
                    timed_out=False,
                    duration_ms=result.get("duration_ms"),
                    stdout=result.get("stdout", ""),
                    stderr=result.get("stderr", ""),
                    attempt_count=attempt + 1,
                    image_base64=result.get("image_base64"),
                    run_id=run_id,
                    execution_evidence=result.get("execution_evidence"),
                    candidate_context=candidate_context,
                )
                _finalize_skill_learning(result, run_id, candidate_context)
                return result

            # 已达最大重试次数
            if attempt >= max_retries:
                result["history"] = history
                result["final_code"] = current_code
                result["total_attempts"] = attempt + 1
                result["error"] = "max_retries exhausted"
                result["run_id"] = run_id
                result["report"] = execution._build_report(result, code)
                if last_error_sig:
                    try:
                        cache_store._update_cache(last_error_sig, current_code[:500], was_successful=False)
                    except Exception:
                        pass
                record_execution(
                    tool_name="run_jwave_code_with_retry",
                    code=current_code,
                    exit_code=result["exit_code"],
                    timed_out=result.get("timed_out", False),
                    duration_ms=result.get("duration_ms"),
                    stdout=result.get("stdout", ""),
                    stderr=result.get("stderr", ""),
                    attempt_count=attempt + 1,
                    image_base64=result.get("image_base64"),
                    run_id=run_id,
                    execution_evidence=result.get("execution_evidence"),
                    candidate_context=candidate_context,
                )
                _finalize_skill_learning(result, run_id, candidate_context)
                return result

            # 调用 LLM 修正
            error_sig = cache_store._extract_error_signature(
                result.get("stderr", ""), result.get("exit_code"), result.get("stdout", "")
            )
            if error_sig:
                last_error_sig = error_sig
            current_code = llm._llm_fix_code(
                resolved_profile.profile_id,
                current_code,
                result,
                run_id=run_id,
            )

        # 不应该走到这里，但保底
        return {"error": "unreachable", "history": history}

    @mcp.tool
    def validate_simulation_params(params_json: str) -> dict[str, Any]:
        """Validate simulation parameters against physical rules before code generation.

        Checks Nyquist condition, CFL stability, grid size, PML layers,
        frequency-resolution matching, and time-propagation distance matching.
        All validation rules are hardcoded -- no LLM is called.
        """
        # Parse JSON input
        try:
            params = json.loads(params_json)
        except (json.JSONDecodeError, TypeError) as exc:
            return {
                "valid": False,
                "errors": [{"field": "_json", "message": f"JSON 解析失败: {exc}"}],
                "warnings": [],
            }

        if not isinstance(params, dict):
            return {
                "valid": False,
                "errors": [{"field": "_json", "message": "params_json 必须编码为一个 JSON 对象"}],
                "warnings": [],
            }

        errors: list[dict[str, str]] = []
        warnings: list[dict[str, str]] = []

        # Extract fields
        sound_speed = params.get("sound_speed")
        density = params.get("density")
        source_frequency = params.get("source_frequency")
        domain_N = params.get("domain_N")
        domain_dx = params.get("domain_dx")
        t_end = params.get("t_end")
        cfl = params.get("cfl")
        pml_size = params.get("pml_size")

        # -------------------------------------------------------------------
        # 1. Required field check
        # -------------------------------------------------------------------
        required_fields = {
            "sound_speed": sound_speed,
            "density": density,
            "source_frequency": source_frequency,
            "domain_N": domain_N,
            "domain_dx": domain_dx,
        }
        required_field_names = set(required_fields.keys())

        for field_name, value in required_fields.items():
            if value is None:
                errors.append({"field": field_name, "message": f"缺少必填字段 {field_name}"})
            elif isinstance(value, list):
                if len(value) == 0:
                    errors.append({"field": field_name, "message": f"{field_name} 为空列表"})
                else:
                    for idx, elem in enumerate(value):
                        if not isinstance(elem, (int, float)) or elem <= 0:
                            errors.append({
                                "field": field_name,
                                "message": f"{field_name}[{idx}] = {elem} 必须 > 0",
                            })
            elif not isinstance(value, (int, float)) or value <= 0:
                errors.append({
                    "field": field_name,
                    "message": f"{field_name} 必须 > 0，当前值: {value}",
                })

        # If any required field is broken, stop -- downstream checks need them
        if any(e["field"] in required_field_names for e in errors):
            return {"valid": False, "errors": errors, "warnings": warnings}

        # Typecast for clarity -- at this point they are validated
        sound_speed = float(sound_speed)  # type: ignore[arg-type]
        source_frequency = float(source_frequency)  # type: ignore[arg-type]
        domain_N_list = domain_N if isinstance(domain_N, list) else [domain_N]  # type: ignore[union-attr]
        domain_dx_list = domain_dx if isinstance(domain_dx, list) else [domain_dx]  # type: ignore[union-attr]

        freq_mhz = source_frequency / 1e6

        # -------------------------------------------------------------------
        # 2. Nyquist condition
        # -------------------------------------------------------------------
        wavelength_min = sound_speed / source_frequency
        dx_max_allowed = wavelength_min / 4.0

        for i, dx in enumerate(domain_dx_list):
            dx = float(dx)
            if dx > dx_max_allowed * 1.001:  # floating-point tolerance
                errors.append({
                    "field": "domain_dx",
                    "message": (
                        f"dx({dx}m) 不满足 Nyquist 条件，"
                        f"{freq_mhz}MHz 对应的最小波长为 {wavelength_min:.6f}m，"
                        f"建议 dx ≤ {dx_max_allowed:.6f}m"
                    ),
                })

        # -------------------------------------------------------------------
        # 3. CFL condition
        # -------------------------------------------------------------------
        if cfl is not None and isinstance(cfl, (int, float)):
            cfl = float(cfl)
            if cfl > 0.3 * 1.001:
                errors.append({
                    "field": "cfl",
                    "message": f"CFL({cfl}) 超过安全值 0.3，可能导致数值不稳定",
                })

        # -------------------------------------------------------------------
        # 4. Grid size
        # -------------------------------------------------------------------
        for i, n in enumerate(domain_N_list):
            n = int(n)
            if n < 32:
                errors.append({
                    "field": "domain_N",
                    "message": f"网格点数 {n} 过小（<32），jwave 0.2.1 可能存在 broadcasting 问题",
                })
            elif n > 1024:
                warnings.append({
                    "field": "domain_N",
                    "message": f"网格点数较大({n})，仿真可能耗时较长",
                })

        # -------------------------------------------------------------------
        # 5. PML check
        # -------------------------------------------------------------------
        if pml_size is not None and isinstance(pml_size, (int, float)):
            pml_size = int(pml_size)
            if pml_size < 10:
                warnings.append({
                    "field": "pml_size",
                    "message": f"PML 层数({pml_size})偏少，建议 ≥ 10 以保证吸收效果",
                })

        # -------------------------------------------------------------------
        # 6. Frequency-resolution matching (MHz ultrasound)
        # -------------------------------------------------------------------
        if source_frequency > 1e6:
            for i, dx in enumerate(domain_dx_list):
                dx = float(dx)
                if dx > 0.0005 * 1.001:
                    errors.append({
                        "field": "domain_dx",
                        "message": (
                            f"对于 MHz 级超声({freq_mhz}MHz)，"
                            f"分辨率(dx={dx}m)过粗，建议 dx < 0.5mm"
                        ),
                    })

        # -------------------------------------------------------------------
        # 7. Time-propagation distance matching
        # -------------------------------------------------------------------
        if t_end is not None and isinstance(t_end, (int, float)) and float(t_end) > 0:
            t_end_val = float(t_end)
            estimated_distance = sound_speed * t_end_val
            domain_length = max(
                float(domain_N_list[i]) * float(domain_dx_list[i])
                for i in range(min(len(domain_N_list), len(domain_dx_list)))
            )
            denom = min(estimated_distance, domain_length)
            if denom > 1e-20:
                ratio = max(estimated_distance, domain_length) / denom
                if ratio > 10:
                    warnings.append({
                        "field": "t_end",
                        "message": (
                            f"仿真时间({t_end_val}s)对应传播距离约{estimated_distance:.4f}m，"
                            f"与区域大小({domain_length:.4f}m)偏差较大"
                        ),
                    })

        # -------------------------------------------------------------------
        # 8. Initial pressure (p0) check —— P2: 环形/椭圆初始压力曾全零
        # -------------------------------------------------------------------
        initial_pressure = params.get("initial_pressure")
        if initial_pressure is not None:
            if not isinstance(initial_pressure, dict):
                errors.append({
                    "field": "initial_pressure",
                    "message": "initial_pressure 必须是一个 JSON 对象（如 {type: gaussian|ring|ellipse, peak, radius}）",
                })
            else:
                ip_type = initial_pressure.get("type")
                peak = initial_pressure.get("peak")
                if ip_type not in (None, "gaussian", "ring", "circle", "ellipse", "plane"):
                    warnings.append({
                        "field": "initial_pressure.type",
                        "message": f"未知初始压力类型 '{ip_type}'，可用: gaussian / ring / circle / ellipse / plane",
                    })
                if peak is not None and (not isinstance(peak, (int, float)) or peak <= 0):
                    errors.append({
                        "field": "initial_pressure.peak",
                        "message": f"peak 必须 > 0，当前值: {peak}",
                    })
                # 几何超域检查：环形半径/椭圆半轴 >= 域半宽 → 场被 PML 吃掉 → 全零
                half_w = None
                if isinstance(domain_dx_list, list) and isinstance(domain_N_list, list):
                    try:
                        half_w = min(
                            float(n) * float(dx)
                            for n, dx in zip(domain_N_list, domain_dx_list)
                        ) / 2.0
                    except Exception:  # noqa: BLE001
                        half_w = None
                if half_w is not None:
                    geom_vals = []
                    if ip_type in ("ring", "circle"):
                        geom_vals.append(("radius", initial_pressure.get("radius")))
                    if ip_type == "ellipse":
                        geom_vals.append(("radius_x", initial_pressure.get("radius_x")
                                          or initial_pressure.get("semi_major")))
                        geom_vals.append(("radius_y", initial_pressure.get("radius_y")
                                          or initial_pressure.get("semi_minor")))
                    for fname, val in geom_vals:
                        if val is not None and isinstance(val, (int, float)) and float(val) >= half_w:
                            errors.append({
                                "field": f"initial_pressure.{fname}",
                                "message": (
                                    f"{fname}({val}m) 超出计算域半宽 {half_w:.5f}m，"
                                    f"环形/椭圆压力场可能被 PML 吸收导致全零，请减小或增大网格/区域"
                                ),
                            })

        # -------------------------------------------------------------------
        # 9. 3D 内存预算（P1 3D 扩展：全场 (Nt,Nx,Ny,Nz,1) float32）
        # -------------------------------------------------------------------
        if len(domain_N_list) == 3:
            cells = 1
            for n in domain_N_list:
                cells *= int(n)
            est_nt = None
            if t_end is not None and isinstance(t_end, (int, float)) and float(t_end) > 0 \
                    and cfl is not None and isinstance(cfl, (int, float)) and float(cfl) > 0:
                dt = float(cfl) * min(float(d) for d in domain_dx_list) / sound_speed
                est_nt = float(t_end) / dt
            field_bytes = cells * (est_nt or 800) * 4  # float32
            if field_bytes > 3.0e9:
                errors.append({
                    "field": "domain_N",
                    "message": (
                        f"3D 网格 {domain_N_list} 内存预算约 {field_bytes/1e9:.1f}GB"
                        f"（全压力场 {cells} 单元 × {int(est_nt or 800)} 时间步 × 4B），"
                        f"超过 executor 4GB 限制，请减小 N 或 t_end（建议 N ≤ 72，"
                        f"72³ ≈ 1.1GB）"
                    ),
                })
            elif field_bytes > 1.5e9:
                warnings.append({
                    "field": "domain_N",
                    "message": (
                        f"3D 网格 {domain_N_list} 内存预算约 {field_bytes/1e9:.1f}GB，"
                        f"接近 4GB 上限，建议 N ≤ 72（72³ ≈ 1.1GB）或缩短 t_end"
                    ),
                })

        # -------------------------------------------------------------------
        # 10. 介质衰减（P1 衰减扩展：Medium.attenuation，频域 Helmholtz 专用）
        # -------------------------------------------------------------------
        attenuation = params.get("attenuation")
        if attenuation is not None:
            if isinstance(attenuation, (int, float)):
                if attenuation < 0:
                    errors.append({
                        "field": "attenuation",
                        "message": f"attenuation({attenuation}) 不能为负（衰减系数 ≥ 0，单位 dB，幂律 y=2）",
                    })
                elif attenuation > 100:
                    warnings.append({
                        "field": "attenuation",
                        "message": (
                            f"attenuation({attenuation}) 很大（dB 单位、幂律 y=2，k-Wave 约定），"
                            f"注意 db2neper(α,2) 换算后指数吸收 exp(-ω²α·r) 可能使远场信号过弱"
                        ),
                    })
                warnings.append({
                    "field": "attenuation",
                    "message": (
                        "jwave 0.2.1 时域 simulate_wave_propagation 忽略 attenuation，"
                        "衰减仅在频域 helmholtz_solver（wavevector 算子）生效；"
                        "需求含'衰减/吸收/attenuation'时必须用 helmholtz_solver 频域求解"
                    ),
                })
            elif not isinstance(attenuation, (list, dict)):
                errors.append({
                    "field": "attenuation",
                    "message": "attenuation 必须是数值或与介质空间分布对应的 list/dict",
                })

        return {
            "valid": len(errors) == 0,
            "errors": errors,
            "warnings": warnings,
        }
