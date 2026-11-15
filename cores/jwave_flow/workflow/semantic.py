"""方案三：输出契约 + 数值门（运行之后）。

循环原本的退出条件是「stderr 为空」——只证明代码能跑。这里再加一道
**确定性的数值判据**：要求生成代码在末尾打印一行机器可读的自检结果

    __JWAVE_SELFCHECK__{"finite": true, "abs_max": 5016.6, "shape": [1001,64,64,1], "dt": 1.1e-08, "f0": 5000000.0}

执行器抓到这行后，本模块据此判断结果是否「物理上讲得通」：

  S1 自检行是否存在        （契约是否被遵守）
  S2 场是否有限           （无 nan / inf）
  S3 场是否非全零         （激励真的起作用了）
  S4 量级是否在合理范围   （没炸/没退化成噪声）
  S5 形状是否存在
  S6 dt / f0 与需求是否一致（防串参）

判据全部确定性、可解释，失败原因会原样回灌给「代码纠错」节点。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from .config import Settings

MARKER = "__JWAVE_SELFCHECK__"

RESULT_OUTPUT_CONTRACT = '''
【用户结果输出契约 · 必须遵守】
自检行不是最终结果，不能作为唯一输出。程序必须在自检行之外输出用户可读的仿真结果：

1. 用 print 输出与用户需求直接对应的数值结果（例如模态阶数与频率表、
   压力场最大值、时间步数、接收点波形摘要等）；不要只输出调试信息。
2. 用户要求二维场、快照或可视化时，使用 matplotlib 的非交互后端生成图片，
   保存到当前工作目录的 outputs/ 目录（例如 outputs/pressure_field.png），
   必须调用 savefig，不能只调用 show()。保存后用 print 明确报告文件路径。
3. 解析模态分析必须打印排序后的模态列表及 (nx, ny) 和频率；
   不要为了满足时域自检而伪造 time_axis、source_frequency 或传播场。
4. 结果输出可以出现在自检行之前或之后，但必须保留下面的机器可读自检行。
'''

# 追加到「代码生成 / 代码纠错」提示词里的输出契约
SELFCHECK_CONTRACT = f'''
【输出契约 · 必须遵守】
在代码中额外打印一行机器可读的自检结果（**单行**、以 {MARKER} 开头、紧随合法 JSON）。
这一行是给工作流解析的附加信息，不是用户结果，也不是唯一允许的输出：

_check_field = _field.params if hasattr(_field, "params") else _field
_check_time_axis = globals().get("time_axis")
_check_frequency = globals().get("source_frequency", 0.0)
print("{MARKER}" + json.dumps({{
    "finite": bool(jnp.all(jnp.isfinite(_check_field))),
    "abs_max": float(jnp.max(jnp.abs(_check_field))),
    "shape": list(_check_field.shape),
    "dt": float(_check_time_axis.dt) if _check_time_axis is not None else 0.0,
    "f0": float(_check_frequency),
}}))

其中 _field 是你最终得到的压力场；若它是 Field/OnGrid 对象，请用 _field.params。
如果任务是解析模态分析而没有时域压力场，请用最终模态场作为 _field，
并将 dt/f0 填为任务中实际存在的值；不要凭空构造时域传播结果。
'''


@dataclass
class SemanticReport:
    present: bool = False
    data: dict[str, Any] = field(default_factory=dict)
    issues: list[str] = field(default_factory=list)
    checked: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.issues

    def feedback(self) -> str:
        return "自检未通过：\n" + "\n".join(f"- {i}" for i in self.issues)

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "present": self.present,
            "data": self.data,
            "issues": self.issues,
            "checked": self.checked,
        }


def parse_selfcheck(stdout: str) -> dict[str, Any] | None:
    """从 stdout 中提取自检 JSON（宽容：允许前面有别的内容）。"""
    if not stdout:
        return None
    for line in stdout.splitlines():
        idx = line.find(MARKER)
        if idx < 0:
            continue
        payload = line[idx + len(MARKER):].strip().strip("`")
        if not payload:
            continue
        try:
            data = json.loads(payload)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(data, dict):
            return data
    return None


def has_user_output(stdout: str) -> bool:
    """Return whether stdout contains output other than the machine marker."""
    if not stdout:
        return False
    return any(line.strip() and MARKER not in line for line in stdout.splitlines())


def _num(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def check_semantics(
    stdout: str,
    settings: Settings,
    expected: dict[str, Any] | None = None,
) -> SemanticReport:
    """对一次执行结果做数值判据。Never raises."""
    report = SemanticReport()
    expected = expected or {}

    if not settings.semantic_check_enabled:
        report.data = {"skipped": True}
        return report

    data = parse_selfcheck(stdout)
    if data is None:
        report.present = False
        if settings.require_selfcheck:
            report.issues.append(
                f"输出中找不到自检行 {MARKER}（未遵守输出契约）"
                "，无法判断结果是否有效"
            )
        return report

    if not has_user_output(stdout):
        report.issues.append("程序只输出了机器自检行，缺少用户可读的仿真结果")
    report.checked.append("S0_user_output")

    report.present = True
    report.data = data

    # S2 有限性
    finite = data.get("finite")
    if finite is None:
        report.issues.append("自检结果缺少 finite 字段")
    elif finite is not True:
        report.issues.append("场中出现 nan/inf（finite=false），数值发散")
    report.checked.append("S2_finite")

    # S3/S4 量级
    abs_max = _num(data.get("abs_max"))
    if abs_max is None:
        report.issues.append("自检结果缺少可解析的 abs_max 字段")
    else:
        if abs_max <= settings.min_abs_max:
            report.issues.append(
                f"场幅值 |p|max = {abs_max:g}，几乎全零——激励没有真正起作用"
            )
        if abs_max > settings.max_abs_max:
            report.issues.append(
                f"场幅值 |p|max = {abs_max:g} 超出合理上限 {settings.max_abs_max:g}，疑似数值发散"
            )
    report.checked.append("S3_nonzero")
    report.checked.append("S4_magnitude")

    # S5 形状
    shape = data.get("shape")
    if not shape:
        report.issues.append("自检结果缺少 shape 字段")
    report.checked.append("S5_shape")

    # S6 dt / f0 与预期一致（防串参）
    exp_f0 = _num(expected.get("frequency"))
    got_f0 = _num(data.get("f0"))
    if exp_f0 and got_f0 and abs(got_f0 - exp_f0) > 0.01 * exp_f0:
        report.issues.append(
            f"代码实际使用的频率 f0={got_f0:g} 与需求 {exp_f0:g} 不一致（偏差 >1%）"
        )
    report.checked.append("S6_params_match")

    return report
