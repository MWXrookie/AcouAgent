"""方案二：AST 静态规则检查（生成之后、运行之前）。

把知识库末尾「LLM 常编造的禁止 API」那张黑名单，变成**可执行**的规则。
纯标准库 ``ast``，零 LLM 成本、确定性、可解释，并直接给出修法。

规则集（每条都对应一次真实踩坑）：
  R1  tone_burst(dt, ...)                 第一参数是采样频率 1/dt，不是 dt
  R2  pressure_from_density(simulate_...) 默认返回的已是压力场，二次转换
  R3  positions 用浮点坐标               JAX .at[] 静默失效 -> 压力场全零
  R4  Sources(...) 用关键字参数          位置参数才行，否则 TypeError
  R5  jw.np.*                            jwave 顶层没有 np
  R6  time_axis.t / time_axis.time       应为 time_axis.to_array()
  R7  FourierSeries.from_array(...)      直接 FourierSeries(data, domain)
  R8  jw.show_field / jw.display_complex_field 这两个在 jwave.utils 下
  R9  Sources 的 signals 传成 list           必须是 2D jnp 数组（list -> TypeError）
  R10 Sources 的 positions 传成 list        必须是 (int 数组, int 数组) 元组
"""
from __future__ import annotations

import ast
from dataclasses import dataclass, field


@dataclass
class StaticIssue:
    rule: str
    line: int
    message: str
    hint: str = ""

    def render(self) -> str:
        text = f"[{self.rule}] 第 {self.line} 行: {self.message}"
        return f"{text}（修法：{self.hint}）" if self.hint else text


@dataclass
class StaticReport:
    issues: list[StaticIssue] = field(default_factory=list)
    rules_run: int = 0
    parse_error: str | None = None

    @property
    def ok(self) -> bool:
        return not self.issues and not self.parse_error

    def feedback(self) -> str:
        if self.parse_error:
            return f"[SYNTAX] 代码无法解析：{self.parse_error}"
        return "\n".join(i.render() for i in self.issues)

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "parse_error": self.parse_error,
            "issues": [i.render() for i in self.issues],
            "codes": [i.rule for i in self.issues],
        }


def _dotted(node: ast.AST) -> str | None:
    """把 a.b.c 这类属性链读成字符串。"""
    parts: list[str] = []
    cur = node
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if isinstance(cur, ast.Name):
        parts.append(cur.id)
        return ".".join(reversed(parts))
    return None


def _callee_name(node: ast.Call) -> str | None:
    return _dotted(node.func) or (node.func.id if isinstance(node.func, ast.Name) else None)


def _looks_like_dt(node: ast.AST) -> bool:
    """1/dt 视为正确；dt / xxx.dt 视为可疑。"""
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        return False
    name = _dotted(node)
    if name is None:
        return False
    return name == "dt" or name.endswith(".dt")


def check_code(code: str) -> StaticReport:
    """对生成的代码跑一遍静态规则。Never raises."""
    report = StaticReport()
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        report.parse_error = f"{exc.msg} (line {exc.lineno})"
        return report

    # 预扫：记录哪些变量是 simulate_wave_propagation 的结果（供 R2 用）
    sim_result_vars: set[str] = set()
    positions_assignments: list[tuple[int, ast.AST]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            callee = _callee_name(node.value)
            if callee and callee.endswith("simulate_wave_propagation"):
                for tgt in node.targets:
                    if isinstance(tgt, ast.Name):
                        sim_result_vars.add(tgt.id)
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and tgt.id == "positions":
                    positions_assignments.append((node.lineno, node.value))

    issues = report.issues

    for node in ast.walk(tree):
        # ---- R1 tone_burst 第一参数 ----
        if isinstance(node, ast.Call):
            callee = _callee_name(node)
            short = (callee or "").split(".")[-1]
            if short == "tone_burst" and node.args and _looks_like_dt(node.args[0]):
                issues.append(
                    StaticIssue(
                        "R1_TONE_BURST_DT", node.lineno,
                        f"tone_burst 的第一个参数是采样频率，传入了 {_dotted(node.args[0])}（疑似把 dt 当采样率）",
                        "改成 1/dt，例如 tone_burst(1/time_axis.dt, 5e6, 3)",
                    )
                )

            # ---- R2 pressure_from_density 二次转换 ----
            if short == "pressure_from_density" and node.args:
                arg = node.args[0]
                name = _dotted(arg)
                direct = isinstance(arg, ast.Call) and (
                    (_callee_name(arg) or "").endswith("simulate_wave_propagation")
                )
                if direct or (name in sim_result_vars):
                    issues.append(
                        StaticIssue(
                            "R2_DOUBLE_PRESSURE", node.lineno,
                            "simulate_wave_propagation 默认（未传 sensors）返回的已经是压力场，再调用 pressure_from_density 是二次转换",
                            "直接使用返回值即可；只有拿到 PSTD 内部的 rho 时才需要转换",
                        )
                    )

            if short == "Sources":
                # ---- R4 关键字参数 ----
                if node.keywords:
                    bad = ", ".join(k.arg or "**" for k in node.keywords)
                    issues.append(
                        StaticIssue(
                            "R4_SOURCES_KEYWORDS", node.lineno,
                            f"Sources 需要 4 个位置参数，不能用关键字：{bad}",
                            "Sources(positions, signals, dt, domain)",
                        )
                    )

                def _positional(index, keyword):
                    if len(node.args) > index:
                        return node.args[index]
                    for kw in node.keywords:
                        if kw.arg == keyword:
                            return kw.value
                    return None

                # ---- R9 signals 不能是 list 字面量 ----
                sig = _positional(1, "signals")
                if isinstance(sig, ast.List):
                    issues.append(
                        StaticIssue(
                            "R9_SIGNALS_LIST", node.lineno,
                            "Sources 的 signals 传成了 list，jwave 要求的是 2D jnp 数组"
                            "（list 会在 sources.on_grid() 里抛 TypeError）",
                            "signals = jnp.expand_dims(signal_1d, 0)",
                        )
                    )

                # ---- R10 positions 不能是 list（应为元组）----
                pos = _positional(0, "positions")
                if isinstance(pos, ast.List):
                    issues.append(
                        StaticIssue(
                            "R10_POSITIONS_LIST", node.lineno,
                            "Sources 的 positions 传成了 list，jwave 要求的是"
                            "『1D 整数数组』的元组（如 (jnp.array([64]), jnp.array([64]))）",
                            "positions = (jnp.array([x]), jnp.array([y]))",
                        )
                    )

            # ---- R7 FourierSeries.from_array ----
            dotted = _dotted(node.func) or ""
            if dotted.endswith("FourierSeries.from_array"):
                issues.append(
                    StaticIssue(
                        "R7_FOURIER_FROM_ARRAY", node.lineno,
                        "FourierSeries 没有 from_array，直接构造即可",
                        "FourierSeries(data, domain)",
                    )
                )

        # ---- R5 jw.np.* ----
        if isinstance(node, ast.Attribute) and node.attr == "np":
            dotted = _dotted(node)
            if dotted and dotted.startswith("jw.np"):
                issues.append(
                    StaticIssue("R5_JW_NP", node.lineno, f"{dotted} 不存在（jwave 顶层没有 np）",
                                "改用 import jax.numpy as jnp 后写 jnp.*")
                )

        # ---- R6 time_axis.t / .time ----
        if isinstance(node, ast.Attribute) and node.attr in ("t", "time"):
            if isinstance(node.value, ast.Name) and node.value.id == "time_axis":
                issues.append(
                    StaticIssue("R6_TIME_AXIS_T", node.lineno,
                                f"time_axis.{node.attr} 不存在", "用 time_axis.to_array()")
                )

        # ---- R8 jw.show_field / jw.display_complex_field ----
        if isinstance(node, ast.Attribute) and node.attr in ("show_field", "display_complex_field"):
            if isinstance(node.value, ast.Name) and node.value.id == "jw":
                issues.append(
                    StaticIssue("R8_TOPLEVEL_UTILS", node.lineno,
                                f"jwave 顶层没有 {node.attr}", f"改用 jw.utils.{node.attr}(...)")
                )

    # ---- R3 positions 浮点坐标 ----
    for lineno, value in positions_assignments:
        floats = [n for n in ast.walk(value) if isinstance(n, ast.Constant) and isinstance(n.value, float)]
        if floats:
            issues.append(
                StaticIssue(
                    "R3_FLOAT_POSITIONS", lineno,
                    f"positions 里出现浮点字面量（{len(floats)} 个），JAX 整数索引会被静默吞掉导致压力场全零",
                    "坐标用整数，例如 positions = (jnp.array([64]), jnp.array([64]))",
                )
            )

    report.rules_run = 10
    # 去重并按行排序，输出更可读
    seen, unique = set(), []
    for issue in sorted(issues, key=lambda i: (i.line, i.rule)):
        key = (issue.rule, issue.line)
        if key not in seen:
            seen.add(key)
            unique.append(issue)
    report.issues = unique
    return report
