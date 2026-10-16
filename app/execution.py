"""代码执行链路：代码清理（防护注入）、沙箱提交、Markdown 报告生成。"""

import json
import re
import uuid
from typing import Any

import httpx

from app import config, executor_evidence, runtime_registry_service

# 与 analysis.py 的场数据输出约定一致
_FIELD_START = "__ACOU_FIELD_START__"
_FIELD_END = "__ACOU_FIELD_END__"
_SENSOR_MARKERS = (
    ("__ACOU_SENSOR_START__", "__ACOU_SENSOR_END__"),
    ("__SENSOR_DATA_START__", "__SENSOR_DATA_END__"),
)


def _clean_code(code: str) -> str:
    """去掉 LLM 输出中可能包裹的 markdown 代码块标记，并注入图片保存."""
    code = code.strip()
    # Some reasoning models may prepend a <think>...</think> block.
    # Strip it before sandbox validation so short programs are not rejected
    # only because internal reasoning pushed them past the code-size limit.
    code = re.sub(r"<think>.*?</think>", "", code, flags=re.DOTALL).strip()
    for prefix in ("```python\n", "```python", "```\n", "```"):
        if code.startswith(prefix):
            code = code[len(prefix):]
    for suffix in ("\n```", "```"):
        if code.endswith(suffix):
            code = code[:-len(suffix)]
    code = code.strip()

    # ---- 防护 1: LLM 常用 .max() 而非 jnp.max(jnp.abs(...)) ----
    # JAX 的 .max() 在纯负值网格上返回 0。自动替换。
    code = re.sub(
        r'(?<![a-zA-Z_])'
        r'(\b[a-zA-Z_]\w*)\.max\(\s*\)',
        lambda m: (
            f'jnp.max(jnp.abs({m.group(1)}))'
            if m.group(1) not in ('jnp', 'jax', 'numpy', 'np')
            else m.group(0)
        ),
        code,
    )

    # ---- 防护 2: LLM 错误地用 .params[0] 取第一帧 ----
    # jwave simulate_wave_propagation 返回 shape=(Nt, Nx, Ny, 1)，
    # .params[0] 是 t=0 时刻（全零）。正确做法是对所有帧取 max。
    code = re.sub(
        r'\.params\s*\[\s*0\s*\]',
        '.params',
        code,
    )

    # ---- 防护 3: LLM 错误地用 .params[-1] 取最后一帧 ----
    # 最后一帧可能也不是最大值所在，改为取全部帧
    code = re.sub(
        r'\.params\s*\[\s*-\s*1\s*\]',
        '.params',
        code,
    )

    # ---- 注入图片保存代码 ----
    # 如果代码使用了 matplotlib 且没有调用 savefig，则自动注入
    has_matplotlib = bool(re.search(r'(import\s+matplotlib|from\s+matplotlib|plt\.)', code))
    has_savefig = bool(re.search(r'plt\.savefig|\.savefig\s*\(', code))
    has_figure = bool(re.search(r'plt\.figure|plt\.subplots|plt\.plot|plt\.imshow|plt\.pcolormesh|plt\.show', code))

    if has_matplotlib and has_figure and not has_savefig:
        code += (
            "\n\n# Auto-injected: save figure for gallery\n"
            "import os\n"
            "try:\n"
            "    plt.savefig('result.png', dpi=72, bbox_inches='tight')\n"
            "    print('__GALLERY_IMAGE_SAVED__')\n"
            "except Exception as __e:\n"
            "    print(f'__GALLERY_SAVE_FAILED__: {__e}', file=__import__('sys').stderr)\n"
        )

    return code


def _execute_code(code: str, timeout_seconds: int, *, request_nonce: str | None = None) -> dict[str, Any]:
    """提交代码到 jwave-executor 并返回执行结果."""
    approval = runtime_registry_service.capture_dispatch_contract()
    nonce = uuid.uuid4().hex if request_nonce is None else request_nonce
    if not isinstance(nonce, str) or re.fullmatch(r"[0-9a-f]{32}", nonce) is None:
        raise ValueError("invalid_internal_request_nonce")
    # Created before transport, outside sandbox output. No user key or root
    # enrollment is needed for this local request/response correlation.
    request = {"code": code, "timeout_seconds": timeout_seconds, "request_nonce": nonce}
    local_dispatch = {
        "version": "local-dispatch.v1",
        "source_kind": "managed_executor_transport",
        "request_sha256": executor_evidence.sha256(
            json.dumps(request, sort_keys=True, separators=(",", ":"), allow_nan=False)),
        "code_sha256": executor_evidence.sha256(code),
        "request_nonce": nonce,
        "timeout_seconds": timeout_seconds,
        "target_url": f"{config.EXECUTOR_URL}/execute",
        "trust_boundary": "gateway_and_internal_executor",
        "independent_host_authenticated": False,
    }
    response = httpx.post(
        local_dispatch["target_url"],
        headers={"X-Executor-Token": config.EXECUTOR_SHARED_TOKEN},
        json=request,
        timeout=timeout_seconds + 10,
    )
    response.raise_for_status()
    result = response.json()
    # Do not consume metadata from stdout or caller analysis arguments.
    receipt = result.pop("executor_receipt", None)
    result["execution_evidence"] = executor_evidence.verify_executor_receipt(
        receipt, result, code, timeout_seconds, nonce)
    result["execution_evidence"]["approval_usage"] = runtime_registry_service.recheck_dispatch_contract(
        approval, result["execution_evidence"])
    # Persist gateway-created context independently of executor response fields.
    result["execution_evidence"]["gateway_request_nonce"] = nonce
    result["execution_evidence"]["gateway_timeout_seconds"] = timeout_seconds
    # A correlated local transport record is not a physical verdict or a
    # cross-host trust grant. Optional accumulation checks physics separately.
    result["execution_evidence"]["local_dispatch"] = dict(
        local_dispatch, receipt_correlated=result["execution_evidence"]["matched"] is True)

    # Legacy managed executors have no executor-side nonce receipt. Preserve a
    # separate gateway transport record; never label it an executor receipt.
    if (type(result.get("stdout")) is str and type(result.get("stderr")) is str
            and type(result.get("exit_code")) is int and type(result.get("timed_out")) is bool):
        result["execution_evidence"]["local_transport_receipt"] = {
            "version":"gateway-transport-receipt.v1",
            "request_sha256":local_dispatch["request_sha256"],
            "request_nonce":nonce,
            "stdout_sha256":executor_evidence.sha256(result["stdout"]),
            "stderr_sha256":executor_evidence.sha256(result["stderr"]),
            "exit_code":result["exit_code"],"timed_out":result["timed_out"],
            "executor_receipt_absent":receipt is None,
            "authentication_configured":isinstance(config.EXECUTOR_SHARED_TOKEN,str)
                and len(config.EXECUTOR_SHARED_TOKEN)>=32,
            "independent_host_authenticated":False,
        }
    return result


def _shrink_field_in_stdout(stdout: str, max_chars: int = 350000) -> str:
    """强制压缩 stdout 中的场数据 JSON（Dify 变量上限 40 万字符）。

    无论 LLM 生成代码是否按模板降采样，网关层都保证场 JSON 大小不超限：
    解析 __ACOU_FIELD_START__/END__ 标记块，超限时对 data 做 N 维网格降采样
    （每维按比例切），并把全场 max_pressure 写入 payload（降采样不丢失峰值量级）。
    支持 2D/3D 场（data 为 2D/3D/4D list，末尾可能带 1 维通道）。
    """
    if not stdout or len(stdout) <= max_chars:
        return stdout
    m = re.search(
        re.escape(_FIELD_START) + r"\s*(\{.*?\})\s*" + re.escape(_FIELD_END),
        stdout,
        re.DOTALL,
    )
    if not m:
        return stdout
    block = m.group(0)
    raw_json = m.group(1)
    try:
        payload = json.loads(raw_json)
    except Exception:  # noqa: BLE001
        return stdout
    data = payload.get("data")
    if not isinstance(data, list) or not data:
        return stdout

    def _ndims(x):
        """返回嵌套 list 的维度数（含 1 维通道）。"""
        n = 0
        cur = x
        while isinstance(cur, list) and cur:
            n += 1
            cur = cur[0]
        return n

    def _shrink_recursive(x, step):
        """对每维按 step 步长递归降采样。"""
        if not isinstance(x, list):
            return x
        # 不降采样末尾的 1 维通道（如 [Nt][Nx][Ny][1] 的最后一维）
        if len(x) <= 1 and all(not isinstance(e, list) for e in x):
            return x
        return [_shrink_recursive(e, step) for e in x[::step]]

    def _json_len(x) -> int:
        return len(json.dumps(x, ensure_ascii=False))

    step = 1
    new_data = data
    base_ndims = _ndims(data)
    while True:
        candidate = _shrink_recursive(data, step)
        test_payload = dict(payload)
        test_payload["data"] = candidate
        test_payload["downsample"] = step
        if _json_len(test_payload) <= max_chars - 2000:
            new_data = candidate
            break
        step += 1
        if step > 64 or _ndims(candidate) != base_ndims:
            break

    new_payload = dict(payload)
    new_payload["data"] = new_data
    new_payload["downsample"] = step
    if isinstance(payload.get("max_pressure"), (int, float)):
        new_payload["max_pressure"] = payload["max_pressure"]
    new_block = _FIELD_START + "\n" + json.dumps(new_payload, ensure_ascii=False) + "\n" + _FIELD_END
    return stdout.replace(block, new_block)


def _normalize_result_artifacts(result: dict[str, Any]) -> dict[str, Any]:
    """Prefer the gateway-rendered max-abs field over an LLM-authored gallery image."""
    if result.get("exit_code") != 0 or result.get("timed_out"):
        return result
    try:
        from app import analysis

        analyzed = analysis._analyze_impl(
            result.get("stdout", "") or "",
            result.get("stderr", "") or "",
            int(result.get("exit_code") or 0),
        )
        heatmap = analyzed.get("heatmap_png_base64")
        if heatmap:
            result["image_base64"] = heatmap
            result["image_source"] = "canonical_max_abs_field"
    except Exception:  # noqa: BLE001
        pass
    return result


def _stderr_is_warning_only(stderr: str, exit_code: int | None, timed_out: bool) -> bool:
    """Recognize non-fatal Python warning streams without hiding actual failures."""
    if not stderr.strip() or timed_out or exit_code != 0:
        return False
    lowered = stderr.lower()
    if "warning:" not in lowered:
        return False
    fatal_markers = ("traceback (most recent call last)", "error:", "exception:", "fatal:")
    return not any(marker in lowered for marker in fatal_markers)


def _timing_report_note(stdout: str) -> str:
    """Static bounded copy only; never render output-provided messages or flags."""
    from app.analysis import _extract_sensor
    from app.physics_timing import assess_sensor_timing
    try:
        diagnostic = (assess_sensor_timing(_extract_sensor(stdout))
                      if isinstance(stdout, str) and len(stdout) <= 400000 else {})
    except (ValueError, TypeError, OverflowError, RecursionError):
        diagnostic = {}
    if diagnostic.get("status") == "insufficient":
        if diagnostic.get("reason") == "ends_before_geometric_arrival":
            note = "按输出中未认证的参数计算，仿真结束时间早于声波到达传感器的几何时间；本次不能作为有效接收响应或稳态结果。"
        else:
            note = "按输出中未认证的参数计算，到达传感器后的采样不足一个声源周期；本次不能作为稳态结果。"
    elif diagnostic.get("status") == "unverified":
        note = "按输出中未认证的参数计算，采样窗口未触发明显时长不足，但尚未检查收敛，不能确认稳态。"
    else:
        note = "时间轴或物理参数不足以判定传播时长与稳态；执行完成不代表物理验证通过。"
    return note


def _build_report(result: dict[str, Any], original_code: str) -> str:
    """Build a human-readable Markdown report from execution results."""
    exit_code = result.get("exit_code")
    timed_out = result.get("timed_out", False)
    duration_ms = result.get("duration_ms")
    stdout = result.get("stdout", "") or ""
    stderr = result.get("stderr", "") or ""
    total_attempts = result.get("total_attempts", 1)
    image_base64 = result.get("image_base64")

    timing_note = _timing_report_note(stdout) if exit_code == 0 and not timed_out else None

    # 剔除场数据 JSON 块（__ACOU_FIELD_START__/END__ 仅供 analyze 分析，不展示给用户）
    stdout = re.sub(
        re.escape("__ACOU_FIELD_START__") + r".*?" + re.escape("__ACOU_FIELD_END__"),
        "[场数据已用于物理分析，此处省略]",
        stdout,
        flags=re.DOTALL,
    )
    for sensor_start, sensor_end in _SENSOR_MARKERS:
        stdout = re.sub(
            re.escape(sensor_start) + r".*?" + re.escape(sensor_end),
            "[传感器时域数据已用于曲线生成，此处省略]",
            stdout,
            flags=re.DOTALL,
        )

    # Reports are display projections; never truncate the stored/analysis stdout.
    # A single JSON line can exceed Dify's result-variable size limit.
    if len(stdout.encode("utf-8")) > 12000:
        stdout = stdout.encode("utf-8")[:12000].decode("utf-8", errors="ignore") + "\n[显示内容已截断；完整原始输出保留在执行记录中，供物理分析。]"

    # Status
    if timed_out:
        status = "⏱ **超时** — 计算量超过资源限制"
    elif exit_code == 0:
        status = "✅ **成功** — 仿真正常完成"
    else:
        status = f"❌ **失败** — 退出码 {exit_code}"

    # Key metrics from stdout
    max_pressure = None
    pressure_shape = None
    m = re.search(r'(?:最大压力|max[_\s]pressure)\s*[:=]\s*([\d.eE+-]+)', stdout, re.I)
    if m:
        try:
            max_pressure = float(m.group(1))
        except ValueError:
            pass
    m = re.search(r'(?:压力场形状|pressure.*shape)\s*[:=]\s*\(?([\d,\s]+)\)?', stdout, re.I)
    if m:
        pressure_shape = m.group(1).strip().rstrip(")")

    lines = []
    lines.append("## 📊 仿真结果报告")
    lines.append("")
    lines.append("### ✅ 执行状态")
    lines.append("")
    lines.append(f"{status}")
    lines.append("")
    lines.append("| 指标 | 值 |")
    lines.append("|------|-----|")
    lines.append(f"| 执行耗时 | {duration_ms:.0f} ms |" if duration_ms else "| 执行耗时 | - |")
    lines.append(f"| 尝试次数 | {total_attempts} 次 |")
    lines.append("")

    if timing_note:
        lines.extend(["### 物理验证说明", "", timing_note, ""])

    if max_pressure is not None or pressure_shape:
        lines.append("### 📈 关键数据")
        lines.append("")
        if max_pressure is not None:
            lines.append(f"- 最大压力：**{max_pressure:.4f}**")
        if pressure_shape:
            lines.append(f"- 压力场形状：`{pressure_shape}`")
        lines.append("")

    if stdout.strip():
        lines.append("### 📝 程序输出")
        lines.append("")
        lines.append("```")
        for line in stdout.strip().splitlines()[:30]:
            lines.append(line)
        if len(stdout.strip().splitlines()) > 30:
            lines.append(f"... (共 {len(stdout.strip().splitlines())} 行，省略后续)")
        lines.append("```")
        lines.append("")

    if stderr.strip():
        warning_only = _stderr_is_warning_only(stderr, exit_code, timed_out)
        lines.append("### ⚠️ 运行警告（不影响本次执行）" if warning_only else "### ❌ 错误输出")
        lines.append("")
        lines.append("```")
        for line in stderr.strip().splitlines()[:20]:
            lines.append(line)
        lines.append("```")
        lines.append("")

    if image_base64:
        lines.append("### 🖼 仿真图像")
        lines.append("")
        lines.append(f"![仿真压力场热力图](data:image/png;base64,{image_base64})")
        lines.append("")

    return "\n".join(lines)
