"""方案一：参数物理预检 (pre-codegen physical sanity check).

「参数提取」节点产出的 JSON 是自由格式（原始 Dify 工作流里该节点并没有配置
JSON Schema），所以这里做**宽容解析**：递归搜索常见别名，能取到多少算多少，
取不到的判据就跳过并给出说明——绝不臆造。

判据来源：jwave / k-Wave 的标准经验条件

* 每波长网格点数   ``Nppw = c_min / (f_max * dx)`` 需 ≥ ``min_points_per_wavelength``
* 采样率/Nyquist   ``dt = cfl * dx_min / c_max``（``TimeAxis.from_medium`` 的实现），
  要求 ``1/dt > 2 * f_max``
* 仿真时长         至少覆盖若干个激励周期
* PML 厚度        不能超过域的 1/2

分两级：

* ``error`` —— 物理上不可能成立（声速非正、频率缺失、PML 比域还厚…）
  → **不进入代码生成**，回退到「参数提取」重试（可配轮数）
* ``warn``  —— 能跑但结果不可信（欠采样、时长过短）
  → 不拦截，作为**约束**注入代码生成提示词，并记录到最终报告

另外，当参数里没有 ``dx`` 时（实测该节点就是不输出 dx），改为**反算合规上限**
``dx_max = c_min / (f_max * Nppw)`` 注入提示词，从上游消除欠采样——这比事后
报错更有用。
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from typing import Any

from .config import Settings

# --------------------------------------------------------------------------- #
# 别名表：键名 -> 规范名。会递归匹配嵌套结构（如 {"medium": {"sound_speed": ...}}）
# --------------------------------------------------------------------------- #
ALIASES: dict[str, tuple[str, ...]] = {
    "sound_speed": ("sound_speed", "soundspeed", "speed_of_sound", "c0", "c", "声速"),
    "density": ("density", "rho0", "rho", "密度"),
    "frequency": (
        "source_frequency", "frequency", "freq", "f0", "center_frequency",
        "centre_frequency", "信号频率", "中心频率", "频率", "声源频率",
    ),
    "t_end": (
        "t_end", "simulation_time", "total_time", "duration", "time_length",
        "仿真时长", "仿真时间", "总时长",
    ),
    "grid_size": ("grid_size", "grid_shape", "shape", "n", "domain_size", "网格尺寸", "网格大小"),
    "dx": ("dx", "grid_spacing", "spacing", "cell_size", "网格间距", "空间步长"),
    "pml_size": ("pml_size", "pml", "pml_thickness", "pml_width", "pml吸收层"),
    "positions": ("positions", "source_position", "source_positions", "location", "位置", "坐标"),
    "cfl": ("cfl", "courant", "courant_number"),
    "dimension": ("dimension", "ndim", "dim", "维度"),
}

_NUM_RE = re.compile(r"^-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?$")


def _to_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        text = value.strip()
        if _NUM_RE.match(text):
            return float(text)
    return None


def _to_size_list(value: Any) -> list[int] | None:
    if isinstance(value, (list, tuple)) and value:
        out = []
        for item in value:
            num = _to_float(item)
            if num is None:
                return None
            out.append(int(num))
        return out
    return None


def _walk(node: Any):
    """Yield (key, value) pairs recursively, innermost-friendly."""
    if isinstance(node, dict):
        for key, value in node.items():
            yield str(key), value
            yield from _walk(value)
    elif isinstance(node, list):
        for item in node:
            yield from _walk(item)


def extract_scalars(params: Any) -> dict[str, Any]:
    """Best-effort: pull the quantities we need out of a free-form params blob."""
    if isinstance(params, str):
        try:
            params = json.loads(params)
        except (json.JSONDecodeError, TypeError):
            return {}
    if not isinstance(params, dict):
        return {}

    lowered: dict[str, tuple[str, Any]] = {}
    for key, value in _walk(params):
        lowered.setdefault(key.strip().lower().replace(" ", "_"), value)

    found: dict[str, Any] = {}
    for canonical, names in ALIASES.items():
        for name in names:
            if name.lower() in lowered:
                found[canonical] = lowered[name.lower()]
                break

    out: dict[str, Any] = {}
    for key in ("sound_speed", "density", "frequency", "t_end", "dx", "pml_size", "cfl"):
        num = _to_float(found.get(key))
        if num is not None:
            out[key] = num
    for key in ("grid_size",):
        size = _to_size_list(found.get(key))
        if size:
            out[key] = size
    if "dimension" in found:
        dim = _to_float(found["dimension"])
        if dim is not None:
            out["dimension"] = int(dim)
    if "positions" in found:
        out["positions"] = found["positions"]
    return out


# --------------------------------------------------------------------------- #
# 判据
# --------------------------------------------------------------------------- #
@dataclass
class ParamIssue:
    level: str          # "error" | "warn"
    code: str
    message: str
    hint: str = ""

    def render(self) -> str:
        icon = "❌" if self.level == "error" else "⚠️"
        text = f"{icon} [{self.code}] {self.message}"
        return f"{text}（建议：{self.hint}）" if self.hint else text


@dataclass
class ParamCheckResult:
    scalars: dict[str, Any] = field(default_factory=dict)
    issues: list[ParamIssue] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)

    @property
    def errors(self) -> list[ParamIssue]:
        return [i for i in self.issues if i.level == "error"]

    @property
    def warnings(self) -> list[ParamIssue]:
        return [i for i in self.issues if i.level == "warn"]

    @property
    def ok(self) -> bool:
        return not self.errors

    def feedback(self) -> str:
        """给「参数提取」/「代码生成」的反馈文本。"""
        return "\n".join(i.render() for i in self.issues)

    def to_dict(self) -> dict[str, Any]:
        return {
            "scalars": self.scalars,
            "ok": self.ok,
            "errors": [i.code for i in self.errors],
            "warnings": [i.code for i in self.warnings],
            "issues": [i.render() for i in self.issues],
            "constraints": self.constraints,
            "skipped": self.skipped,
        }


def check_params(params: Any, settings: Settings, cfl: float | None = None) -> ParamCheckResult:
    """Run the pre-codegen physical invariants. Never raises."""
    s = extract_scalars(params)
    result = ParamCheckResult(scalars=s)
    issues = result.issues
    constraints = result.constraints
    skipped = result.skipped

    cfl_eff = cfl or s.get("cfl") or settings.default_cfl
    nppw_min = settings.min_points_per_wavelength

    # --- 1. 基本量必须存在且为正 ---------------------------------------- #
    c = s.get("sound_speed")
    if c is None:
        issues.append(ParamIssue("error", "E_NO_SOUND_SPEED", "未能从参数中解析出声速", "请明确介质声速，如水中 1500 m/s"))
    elif c <= 0:
        issues.append(ParamIssue("error", "E_BAD_SOUND_SPEED", f"声速必须为正，当前 {c}", "检查单位与数值"))

    f = s.get("frequency")
    if f is None:
        issues.append(ParamIssue("error", "E_NO_FREQUENCY", "未能从参数中解析出声源频率", "请明确中心频率，如 5 MHz = 5e6 Hz"))
    elif f <= 0:
        issues.append(ParamIssue("error", "E_BAD_FREQUENCY", f"频率必须为正，当前 {f}", "检查单位（Hz）"))

    t_end = s.get("t_end")
    if t_end is not None and t_end <= 0:
        issues.append(ParamIssue("error", "E_BAD_TEND", f"仿真时长必须为正，当前 {t_end}", "检查单位（秒）"))

    density = s.get("density")
    if density is not None and density <= 0:
        issues.append(ParamIssue("error", "E_BAD_DENSITY", f"密度必须为正，当前 {density}", "检查单位与数值"))

    grid = s.get("grid_size")
    if grid is not None and any(n <= 0 for n in grid):
        issues.append(ParamIssue("error", "E_BAD_GRID", f"网格尺寸必须为正，当前 {grid}", "检查网格定义"))

    # --- 2. 分辨率 / 采样率 --------------------------------------------- #
    dx = s.get("dx")
    if dx is None and grid and s.get("domain_size_hint"):
        dx = s["domain_size_hint"]

    if c and f and dx and dx > 0:
        nppw = c / (f * dx)
        if nppw < nppw_min:
            issues.append(
                ParamIssue(
                    "warn",
                    "W_UNDER_RESOLVED",
                    f"网格欠采样：每波长仅 {nppw:.1f} 个网格点（要求 ≥ {nppw_min:g}）",
                    f"把网格间距 dx 减小到 ≤ {c / (f * nppw_min):.3g} m，或降低频率/提高声速",
                )
            )
        sample_rate = c / (cfl_eff * dx)
        if sample_rate <= 2 * f:
            issues.append(
                ParamIssue(
                    "warn",
                    "W_NYQUIST",
                    f"采样率 {sample_rate:.3g} Hz 不满足 Nyquist（需 > {2 * f:.3g} Hz）",
                    "减小 dx 或降低 cfl",
                )
            )
    elif c and f:
        # 参数里没有 dx（实测「参数提取」就是不输出 dx）→ 反算合规上限注入提示词
        dx_max = c / (f * nppw_min)
        constraints.append(
            f"网格间距必须满足 dx ≤ {dx_max:.4g} m（即每波长至少 {nppw_min:g} 个网格点，"
            f"由 c={c:g} m/s、f={f:g} Hz 反算）"
        )
        skipped.append(
            f"参数中无 dx，跳过「已给 dx」的分辨率检查，改为给出约束：dx ≤ {dx_max:.4g} m"
        )

    # --- 3. 仿真时长应覆盖若干激励周期 ---------------------------------- #
    if f and t_end:
        periods = t_end * f
        if periods < 3:
            issues.append(
                ParamIssue(
                    "warn",
                    "W_SHORT_TEND",
                    f"仿真时长仅覆盖 {periods:.2f} 个激励周期，波场可能尚未建立",
                    f"建议 t_end ≥ {3 / f:.3g} s（约 3 个周期）",
                )
            )

    # --- 4. PML 与域的关系 ---------------------------------------------- #
    pml = s.get("pml_size")
    if pml is not None and grid:
        half = min(grid) / 2
        if pml <= 0:
            issues.append(ParamIssue("warn", "W_BAD_PML", f"pml_size 非正：{pml}", "通常取 10–20"))
        elif pml >= half:
            issues.append(
                ParamIssue(
                    "error",
                    "E_PML_TOO_THICK",
                    f"PML 厚度 {pml:g} 已达最小边长的一半（{half:g}），域内几乎没有有效区域",
                    "减小 pml_size 或增大网格",
                )
            )

    # --- 5. 源位置应在域内且避开 PML ------------------------------------ #
    positions = s.get("positions")
    if positions is not None and grid:
        coords = _flatten_positions(positions)
        if coords:
            bad_axis = [i for i, v in enumerate(coords) if not (0 <= v < min(grid))]
            if bad_axis:
                issues.append(
                    ParamIssue("warn", "W_SOURCE_OUTSIDE", f"源位置 {coords} 超出网格范围 {grid}",
                               "把源移入网格内部")
                )
            elif pml is not None and any(v < pml or v > min(grid) - pml for v in coords):
                issues.append(
                    ParamIssue("warn", "W_SOURCE_IN_PML", f"源位置 {coords} 落在 PML 层内（pml_size={pml:g}）",
                               "把源移到 PML 之外，否则激励会被吸收")
                )

    # 数值健康度：顺带挡住 NaN / inf
    for key, value in s.items():
        if isinstance(value, float) and not math.isfinite(value):
            issues.append(ParamIssue("error", f"E_NOT_FINITE_{key.upper()}", f"{key} 不是有限数值：{value}", "检查参数"))

    return result


def _flatten_positions(positions: Any) -> list[int] | None:
    """把 [[32,32]] / [[32],[32]] / [32,32] / (np.array([32]), np.array([32])) 都尽量读成坐标。"""
    if isinstance(positions, dict):
        vals = [positions.get(k) for k in ("x", "y", "z") if positions.get(k) is not None]
        positions = vals if vals else None
    if isinstance(positions, (list, tuple)):
        flat: list[int] = []
        for item in positions:
            if isinstance(item, (list, tuple)):
                flat.extend(int(v) for v in item if _to_float(v) is not None)
            else:
                num = _to_float(item)
                if num is None:
                    return None
                flat.append(int(num))
        return flat or None
    return None
