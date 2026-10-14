"""结果分析模块（T-007）· analyze_simulation_result MCP 工具。

职责
----
读取仿真 stdout 中的压力场 JSON（约定标记 `__ACOU_FIELD_START__/__ACOU_FIELD_END__`），
计算物理量摘要、生成热力图/波形图（PNG → base64），并按物理合理性给出 verdict。

verdict 判定口径（对标 docs/VALIDATION_BASELINE.md · VAL-1 验证基准集）
---------------------------------------------------------------------
- "abnormal"  : 执行失败 / 无压力场数据 / 含 NaN/Inf / 疑似数值发散（幅值超物理上限）
- "zero_field": 场全零（Sources 坐标错误、p0 误用等典型故障，见 VAL-1 与纠错速查表）
- "normal"    : 以上均未触发

依赖策略
--------
numpy/matplotlib 懒加载（网关镜像已含）；任一项不可用时自动降级：
- 无 matplotlib → ASCII 热力图（字符密度表示幅值）
- 无 numpy     → 纯 Python 计算物理量
保证工具在任何环境下不因缺依赖而崩溃。
"""

import base64
import io
import json
import math
import os
import re
from typing import Any

# matplotlib 在只读 HOME（容器）下无法建 ~/.config，强制使用可写缓存目录
os.environ.setdefault("MPLCONFIGDIR", "/tmp")

try:  # numpy 为可选（纯 Python 降级）
    import numpy as _np
except Exception:  # noqa: BLE001
    _np = None

try:  # matplotlib 为可选（ASCII 热力图降级）
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as _plt
except Exception:  # noqa: BLE001
    _plt = None

# 场数据输出约定：仿真代码在 stdout 打印以下标记包裹的 JSON
_FIELD_START = "__ACOU_FIELD_START__"
_FIELD_END = "__ACOU_FIELD_END__"
_SENSOR_MARKERS = (
    ("__ACOU_SENSOR_START__", "__ACOU_SENSOR_END__"),
    ("__SENSOR_DATA_START__", "__SENSOR_DATA_END__"),  # legacy workflow output
)

# 可注入仿真代码末尾的"场数据输出"模板（T-008 接入 Dify 工作流时复制即用；
# 也可由 LLM 依据该模板自行生成）。输出"全时最大绝对压力场"，避免 t=0 全零帧陷阱。
# 自适应降采样：网格过大时按步长抽样，保证 stdout JSON < 40万字符（Dify 变量上限），
# 同时把 max_pressure 直接放进 JSON，供 analyze 用（降采样不丢失峰值量级）。
FIELD_OUTPUT_SNIPPET = '''\
import json as __json
import jax.numpy as __jnp
__field = __jnp.max(__jnp.abs(p.params), axis=0)[..., 0]  # 全时最大 |p| 场 (Nx, Ny[, Nz])
# 3D 场取中心 z 切片 → 2D 字段（体积小、热力图/分析兼容）；2D 场原样
if __field.ndim > 2:
    __field = __field[..., __field.shape[-1] // 2]
__maxp = float(__jnp.max(__field))
__step = 1
while __field.size / (__step * __step) > 40000:  # 保底 400KB 内（Dify 变量上限）
    __step += 1
__field_out = __field[::__step, ::__step] if __step > 1 else __field
print("__ACOU_FIELD_START__")
print(__json.dumps({"shape": list(__field_out.shape), "kind": "field",
                    "downsample": __step, "max_pressure": __maxp,
                    "data": __field_out.tolist()}))
print("__ACOU_FIELD_END__")
print(f"最大压力: {__maxp:.6f}")
'''

# 物理发散阈值：声压超过该量级视为数值发散（超声非线性/空化远低于此）
_PHYSICAL_MAX_PRESSURE_PA = 1e12


def _extract_field(stdout: str) -> dict | None:
    """从 stdout 提取压力场 JSON（标记包裹）。"""
    if not stdout:
        return None
    m = re.search(
        re.escape(_FIELD_START) + r"\s*(\{.*?\})\s*" + re.escape(_FIELD_END),
        stdout,
        re.DOTALL,
    )
    if not m:
        return None
    try:
        payload = json.loads(m.group(1))
    except (json.JSONDecodeError, TypeError):
        return None
    data = payload.get("data")
    shape = payload.get("shape")
    if data is None:
        return None
    return {
        "data": data,
        "shape": shape,
        "kind": payload.get("kind", "field"),
        "max_pressure": payload.get("max_pressure"),
        "downsample": payload.get("downsample", 1),
    }


def _extract_sensor(stdout: str) -> dict | None:
    """Extract a point-sensor time series from the current or legacy marker block."""
    if not stdout:
        return None
    for start, end in _SENSOR_MARKERS:
        match = re.search(
            re.escape(start) + r"\s*(\{.*?\})\s*" + re.escape(end),
            stdout,
            re.DOTALL,
        )
        if not match:
            continue
        try:
            payload = json.loads(match.group(1))
            time_values = payload.get("time")
            pressure = payload.get("pressure")
            if not isinstance(time_values, list) or not isinstance(pressure, list):
                continue
            if not time_values or not pressure:
                continue
            if pressure and isinstance(pressure[0], list):
                # Accept (Nt, Ns) and (Ns, Nt); plot the first requested sensor.
                if len(pressure) == len(time_values):
                    pressure = [row[0] for row in pressure if row]
                elif len(pressure[0]) == len(time_values):
                    pressure = pressure[0]
            if len(time_values) != len(pressure):
                continue
            return {
                "time": time_values,
                "timing_metadata": payload.get("timing_metadata"),
                "pressure": pressure,
                "sensor_index": payload.get("sensor_index") or payload.get("sensor_indices"),
                "sensor_position": payload.get("sensor_position_m") or payload.get("sensor_position"),
            }
        except (json.JSONDecodeError, TypeError, ValueError, IndexError):
            continue
    return None


def _to_float_matrix(data: Any) -> tuple[list | None, list[int] | None]:
    """把 JSON 数据规范化为二维 float 列表（shape 优先按 data 推断）。"""
    if _np is not None:
        try:
            arr = _np.asarray(data, dtype=_np.float64)
        except Exception:  # noqa: BLE001
            return None, None
        if arr.size == 0:
            return None, None
        return arr.tolist(), list(arr.shape)
    # 纯 Python 降级
    try:
        if isinstance(data, (int, float)):
            return [[float(data)]], [1, 1]
        rows = []
        for row in data:
            if isinstance(row, (list, tuple)):
                rows.append([float(v) for v in row])
            else:
                rows.append([float(row)])
        if not rows:
            return None, None
        width = len(rows[0])
        for row in rows:
            if len(row) != width:
                return None, None
        return rows, [len(rows), width]
    except Exception:  # noqa: BLE001
        return None, None


def _compute_metrics(rows: list, shape: list[int]) -> dict:
    """计算物理量摘要（numpy 可用时用 numpy，否则纯 Python）。"""
    if _np is not None:
        arr = _np.asarray(rows, dtype=_np.float64)
        abs_arr = _np.abs(arr)
        max_p = float(abs_arr.max())
        rms_p = float(_np.sqrt(_np.mean(arr**2)))
        finite = bool(_np.all(_np.isfinite(arr)))
    else:
        vals = [v for row in rows for v in row]
        max_p = max(abs(v) for v in vals)
        rms_p = math.sqrt(sum(v * v for v in vals) / len(vals))
        finite = all(math.isfinite(v) for v in vals)
    has_signal = bool(finite and max_p > 0.0)
    return {
        "max_pressure": max_p,
        "rms_pressure": rms_p,
        "field_shape": list(shape),
        "has_signal": has_signal,
        "finite": finite,
    }


def _verdict(
    exit_code: int | None,
    timed_out: bool,
    metrics: dict,
) -> str:
    """按 VAL-1 口径判定结果状态。"""
    if exit_code not in (0, None) or timed_out:
        return "abnormal"  # 执行失败/超时
    if not metrics.get("finite", True):
        return "abnormal"  # NaN/Inf
    if metrics["max_pressure"] > _PHYSICAL_MAX_PRESSURE_PA:
        return "abnormal"  # 疑似数值发散
    if not metrics["has_signal"]:
        return "zero_field"  # 全零场（典型故障）
    return "normal"


def _heatmap_base64(
    rows: list,
    title: str = "Pressure field",
    original_shape: list[int] | None = None,
) -> str | None:
    """生成热力图 PNG → base64；无 matplotlib 时降级 ASCII 热力图。

    支持 2D/3D 场：
    - 2D 场 (Nx, Ny[, 1])：直接画
    - 3D 场 (Nt, Nx, Ny) / (Nt, Nx, Ny, Nz[, 1])：取 |p| 最大时间步的中心 2D 切片
      （z=中间平面），保证热力图始终是 2D。
    """
    if _plt is None:
        return _ascii_heatmap(rows)
    try:
        arr = _np.asarray(rows, dtype=_np.float64) if _np is not None else None  # type: ignore[assignment]
        if _np is None or arr is None:
            return _ascii_heatmap(rows)
        arr = arr.squeeze()
        # N 维 → 取中心 2D 切片：优先保留空间最大的两维
        if arr.ndim > 2:
            # 若形如 (Nt, Nx, Ny)：取 |p| 最大的时间步
            if arr.ndim == 3:
                idx = int(_np.argmax(_np.max(_np.abs(arr), axis=(1, 2))))
                arr = arr[idx, :, :]
            else:
                # 更高维：逐维取中间索引直至 2D
                while arr.ndim > 2:
                    arr = arr[arr.shape[0] // 2]
        vmax = float(_np.max(_np.abs(arr))) or 1.0
        fig, ax = _plt.subplots(figsize=(6, 5), dpi=80)
        extent = None
        if original_shape and len(original_shape) >= 2:
            extent = (0, original_shape[1], original_shape[0], 0)
        im = ax.imshow(
            arr,
            cmap="RdBu_r",
            vmin=-vmax,
            vmax=vmax,
            aspect="auto",
            extent=extent,
        )
        ax.set_title(title)
        ax.set_xlabel("x (grid)")
        ax.set_ylabel("y (grid)")
        fig.colorbar(im, ax=ax, label="Pressure (Pa)")
        fig.tight_layout()
        buf = io.BytesIO()
        fig.savefig(buf, format="png", bbox_inches="tight")
        _plt.close(fig)
        return base64.b64encode(buf.getvalue()).decode("ascii")
    except Exception:  # noqa: BLE001
        return _ascii_heatmap(rows)


def _waveform_base64(rows: list, title: str = "Pressure profile") -> str | None:
    """生成波形图 PNG → base64（2D 场取中心行剖面；1D 场直接画）。"""
    if _plt is None or _np is None:
        return None
    try:
        arr = _np.asarray(rows, dtype=_np.float64)
        if arr.ndim == 1:
            y = arr
        elif arr.ndim == 2 and arr.shape[0] > 1:
            y = arr[arr.shape[0] // 2, :]
        else:
            y = arr.ravel()
        fig, ax = _plt.subplots(figsize=(7, 3), dpi=80)
        ax.plot(y, lw=0.8)
        ax.set_title(title)
        ax.set_xlabel("index")
        ax.set_ylabel("Pressure (Pa)")
        fig.tight_layout()
        buf = io.BytesIO()
        fig.savefig(buf, format="png", bbox_inches="tight")
        _plt.close(fig)
        return base64.b64encode(buf.getvalue()).decode("ascii")
    except Exception:  # noqa: BLE001
        return None


def _sensor_waveform_base64(sensor: dict) -> str | None:
    """Render the actual point-sensor pressure against simulation time."""
    if _plt is None or _np is None:
        return None
    try:
        time_values = _np.asarray(sensor["time"], dtype=_np.float64)
        pressure = _np.asarray(sensor["pressure"], dtype=_np.float64)
        if time_values.ndim != 1 or pressure.ndim != 1 or time_values.size != pressure.size:
            return None
        fig, ax = _plt.subplots(figsize=(7, 3), dpi=80)
        ax.plot(time_values * 1e6, pressure, lw=0.9)
        ax.set_title("Point sensor pressure vs time")
        ax.set_xlabel("Time (us)")
        ax.set_ylabel("Pressure (Pa)")
        ax.grid(alpha=0.25)
        fig.tight_layout()
        buf = io.BytesIO()
        fig.savefig(buf, format="png", bbox_inches="tight")
        _plt.close(fig)
        return base64.b64encode(buf.getvalue()).decode("ascii")
    except Exception:  # noqa: BLE001
        return None


def _ascii_heatmap(rows: list, width: int = 48) -> str | None:
    """ASCII 字符热力图降级（无 matplotlib 时）。"""
    try:
        vals = [[float(v) for v in row] for row in rows]
        if not vals:
            return None
        vmax = max(abs(v) for row in vals for v in row) or 1.0
        chars = " .:-=+*#%@"
        lines = []
        h = len(vals)
        step = max(1, h // 16)
        for i in range(0, h, step):
            row = vals[i]
            n = len(row)
            s = max(1, n // width)
            line = "".join(
                chars[min(9, int(abs(row[j]) / vmax * 9))]
                for j in range(0, n, s)
            )
            lines.append(line)
        return base64.b64encode(
            ("\n".join(lines) + f"\n# ASCII heatmap, vmax={vmax:.3g} Pa").encode("utf-8")
        ).decode("ascii")
    except Exception:  # noqa: BLE001
        return None


def _summary(metrics: dict, verdict: str) -> str:
    shape = "×".join(str(s) for s in metrics["field_shape"])
    parts = [
        f"峰值压力 {metrics['max_pressure']:.4g} Pa",
        f"RMS {metrics['rms_pressure']:.4g} Pa",
        f"网格 {shape}",
        f"verdict: {verdict}",
    ]
    if verdict == "zero_field":
        parts.append("（全场为零，常见原因：Sources 坐标浮点/整型错误、p0 误用为 Sources，见纠错速查表）")
    elif verdict == "abnormal":
        parts.append("（执行失败或数值异常，建议重试/检查参数）")
    return "，".join(parts)


def _periodic_view(stdout, stderr, exit_code):
    """Display validated fixed raw evidence without changing its receipt bytes.

    The image is max-abs over three sampled snapshots, never full-time peak.
    Numeric checks alone grant neither source identity nor reusable status.
    """
    try:
        if exit_code != 0 or stderr or type(stdout) is not str or len(stdout.encode())>500000:
            return None
        from scripts.validation.host_forward_signature import unique, reject
        from scripts.validation.periodic_eigenmodes import validate
        body=json.loads(stdout,object_pairs_hook=unique,parse_constant=reject)
        if type(body) is not dict or set(body)!={"raw_evidence"}:return None
        result=validate(body)
        if result.get("numeric_pass") is not True:return None
        raw=body["raw_evidence"];frames=raw["snapshots"]
        values=[max(abs(frame[i]) for frame in frames) for i in range(4096)]
        payload={"shape":[64,64],"kind":"validated_sample_snapshots",
                 "data":[values[i:i+64] for i in range(0,4096,64)]}
        sensor={"time":raw["time"],"pressure":raw["traces"][0],
                "sensor_index":raw["configuration"]["sensor_indices"][0]}
        return payload,sensor,{"profile":raw["profile"],"numeric_pass":True,
            "checked_metrics":result.get("relative_errors"),
            "display_basis":"max_abs_of_three_snapshots",
            "trusted_run":False,"actual_use_authenticated":False}
    except (ValueError,TypeError,KeyError,IndexError,OverflowError,RecursionError,UnicodeError):
        return None

def _analyze_impl(
    stdout_text: str,
    stderr_text: str = "",
    exit_code: int = 0,
    params_json: str = "{}",
    run_id: str = "",
) -> dict[str, Any]:
    """分析一次 jwave 仿真的输出：物理量摘要 + 热力图/波形图 + verdict。

    约定：仿真代码需将压力场以 JSON 打印在 stdout 的
    ``__ACOU_FIELD_START__ {json} __ACOU_FIELD_END__`` 标记之间
    （Dify 工作流代码生成 prompt 已注入该模板）。

    注：模块级独立实现，便于脱离 MCP 直接测试；register() 内注册同名工具，
    其函数体调用本函数（避免与工具名递归遮蔽）。
    """
    from app.dashboard import resolve_execution_source
    source = resolve_execution_source(stdout_text or "", stderr_text or "", exit_code, run_id)
    # Consumption-time approval recheck: a retired or changed approval makes the
    # recorded completion unusable, even though the run itself did execute.
    try:
        from app import runtime_registry_service as _registry
        consumed = _registry.verify_completion_for_consumption(source.get("dispatch_nonce", ""))
    except Exception:  # noqa: BLE001 - never let a governance lookup break analysis
        consumed = {"consumable": False, "receipt_present": False,
                    "trusted_run": False, "runtime_approved": False,
                    "reason": "consumption_check_unavailable"}
    source["approval_consumable"] = bool(consumed.get("consumable"))
    source["approval_consumption_reason"] = consumed.get("reason")
    run_id = source.get("run_id", "")
    payload = _extract_field(stdout_text or "")
    sensor = _extract_sensor(stdout_text or "")
    fixed_view = _periodic_view(stdout_text, stderr_text, exit_code) if payload is None else None
    if fixed_view:
        payload,sensor,_ = fixed_view
    from app.physics_timing import assess_sensor_timing
    timing = assess_sensor_timing(sensor)
    rows, shape = _to_float_matrix(payload["data"]) if payload else (None, None)

    if rows is None:
        metrics = {
            "max_pressure": 0.0,
            "rms_pressure": 0.0,
            "field_shape": [],
            "has_signal": False,
            "finite": True,
        }
        verdict = _verdict(exit_code, False, metrics)
        if exit_code == 0 and verdict == "zero_field":
            verdict = "abnormal"  # 执行成功但未输出场数据 → 无法分析
        result = {
            "has_signal": False,
            "max_pressure": 0.0,
            "rms_pressure": 0.0,
            "field_shape": [],
            "heatmap_png_base64": None,
            "waveform_png_base64": _sensor_waveform_base64(sensor) if sensor else None,
            "waveform_kind": "sensor_time" if sensor else None,
            "sensor_samples": len(sensor["time"]) if sensor else 0,
            "verdict": verdict,
            "summary": f"未能从输出解析压力场（exit_code={exit_code}）。{_summary(metrics, verdict)}",
        }
        from app.physics_gate import assess_quality
        result.update(assess_quality(exit_code=exit_code, timed_out=source.get("timed_out", False), analysis=result, params_json=params_json))
        result["timing_diagnostic"] = timing
        result["run_id"] = run_id or None
        result["execution_source"] = source
        return result

    metrics = _compute_metrics(rows, shape)
    if isinstance(payload.get("shape"), list) and len(payload["shape"]) >= 2:
        metrics["field_shape"] = payload["shape"]
    # 大网格降采样输出时，用 payload 里的全场 max_pressure 覆盖（不丢失峰值量级）
    payload_max = payload.get("max_pressure")
    if isinstance(payload_max, (int, float)) and payload_max > 0:
        metrics["max_pressure"] = float(payload_max)
        metrics["has_signal"] = metrics["has_signal"] or True
    verdict = _verdict(exit_code, False, metrics)
    heatmap = _heatmap_base64(
        rows,
        title="Pressure field (max-abs over time)" if payload.get("kind") == "field" else "Pressure field",
        original_shape=payload.get("shape"),
    )
    waveform = _sensor_waveform_base64(sensor) if sensor else _waveform_base64(rows)
    sensor_pressure = sensor.get("pressure", []) if sensor else []
    sensor_peak = max((abs(float(v)) for v in sensor_pressure), default=None)

    result = {
        "has_signal": metrics["has_signal"],
        "max_pressure": metrics["max_pressure"],
        "rms_pressure": metrics["rms_pressure"],
        "field_shape": metrics["field_shape"],
        "heatmap_png_base64": heatmap,
        "waveform_png_base64": waveform,
        "waveform_kind": "sensor_time" if sensor else "spatial_profile",
        "sensor_samples": len(sensor.get("time", [])) if sensor else 0,
        "sensor_peak_pressure": sensor_peak,
        "sensor_index": sensor.get("sensor_index") if sensor else None,
        "sensor_position": sensor.get("sensor_position") if sensor else None,
        "verdict": verdict,
        "summary": _summary(metrics, verdict),
    }
    from app.physics_gate import assess_quality
    result.update(assess_quality(exit_code=exit_code, timed_out=source.get("timed_out", False), analysis=result, params_json=params_json))
    result["timing_diagnostic"] = timing
    result["run_id"] = run_id or None
    result["execution_source"] = source
    if fixed_view:
        result["fixed_numeric_diagnostic"] = fixed_view[2]
        result["summary"] += " 图像为三个采样时刻的绝对声压包络，非全时峰值或稳态证明。"
    return result


def analyze_and_record(
    stdout_text: str,
    stderr_text: str = "",
    exit_code: int = 0,
    params_json: str = "{}",
    run_id: str = "",
) -> dict[str, Any]:
    """Analyze one execution and persist its analysis event exactly once."""
    result = _analyze_impl(
        stdout_text,
        stderr_text,
        exit_code,
        params_json,
        run_id,
    )
    # Analysis history is separate from execution history. Recording failure must
    # never turn a completed deterministic analysis into a workflow failure.
    try:
        from app.dashboard import record_analysis_event

        record_analysis_event(
            exit_code=exit_code,
            verdict=result.get("verdict", "unknown"),
            max_pressure=result.get("max_pressure"),
            rms_pressure=result.get("rms_pressure"),
            field_shape=result.get("field_shape"),
            has_signal=bool(result.get("has_signal")),
            summary=result.get("summary", ""),
            heatmap_base64=result.get("heatmap_png_base64"),
            waveform_base64=result.get("waveform_png_base64"),
            stdout_excerpt=(stdout_text or "")[:500],
            stderr_excerpt=(stderr_text or "")[:200],
            run_id=result.get("run_id"),
        )
    except Exception:  # noqa: BLE001
        pass
    return result


def register(mcp) -> None:
    """向 FastMCP 实例注册本模块工具。"""

    @mcp.tool
    def analyze_simulation_result(
        stdout_text: str,
        stderr_text: str = "",
        exit_code: int = 0,
        params_json: str = "{}",
        run_id: str = "",
    ) -> dict[str, Any]:
        """Analyze a jwave simulation output: physics summary + heatmap/waveform + verdict.

        Expects the simulation code to print the pressure field as JSON between
        ``__ACOU_FIELD_START__`` and ``__ACOU_FIELD_END__`` markers on stdout.
        Returns has_signal, max/rms pressure, field shape, heatmap/waveform PNG
        (base64) and a physical verdict (normal / zero_field / abnormal).
        """
        return analyze_and_record(
            stdout_text,
            stderr_text,
            exit_code,
            params_json,
            run_id,
        )
