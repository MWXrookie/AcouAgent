"""Shared state passed between LangGraph nodes."""
from __future__ import annotations

from typing import Annotated, Any, TypedDict


def _overwrite(_old, new):  # last-write-wins reducer (explicit, avoids surprises)
    return new


class WorkflowState(TypedDict, total=False):
    # --- inputs ---------------------------------------------------------
    query: str
    session_id: str
    conversation_context: str

    # --- ① 需求分析 ------------------------------------------------------
    requirement: str

    # --- ②③④ 知识检索 / 模板转换 ------------------------------------------
    kb_docs: Annotated[list[Any], _overwrite]
    kb_context: str

    # --- ④ 参数提取 ------------------------------------------------------
    params_json: str

    # --- ④b 参数物理预检（方案一，非 Dify 原生节点）---------------------
    param_check_ok: bool
    param_check_rounds: int
    param_issues: Annotated[list[str], _overwrite]
    param_warnings: Annotated[list[str], _overwrite]
    param_constraints: Annotated[list[str], _overwrite]
    param_check_report: Annotated[dict, _overwrite]

    # --- ⑤ 代码生成 ------------------------------------------------------
    code: str

    # --- ⑤b AST 静态规则检查（方案二，非 Dify 原生节点）-----------------
    static_issues: Annotated[list[str], _overwrite]
    static_rounds: int
    static_report: Annotated[dict, _overwrite]

    # --- ⑥b 语义数值门（方案三，非 Dify 原生节点）-----------------------
    semantic_issues: Annotated[list[str], _overwrite]
    selfcheck: Annotated[dict, _overwrite]
    semantic_verified: bool
    verified: bool
    requirement_completed: bool
    completion_review: Annotated[dict, _overwrite]
    completion_feedback: str
    completion_rounds: int
    answer_marked: bool

    # --- ⑥ 循环（执行 → 纠错） -------------------------------------------
    code_final: str
    stdout: str
    stderr: str
    # 最近一次真实执行失败的信息（永不覆盖，便于事后定位）
    last_error: str
    # 本次要交给「代码纠错」的原因（可能来自运行报错 / 静态检查 / 语义门）
    fix_reason: str
    results: Annotated[list[str], _overwrite]
    outputs: Annotated[list[str], _overwrite]
    new_files: Annotated[list[str], _overwrite]
    execution_time: float
    iterations: int
    success: bool

    # --- ⑧ 输出 / 观测 --------------------------------------------------
    text: str
    trace: Annotated[list[str], _overwrite]
