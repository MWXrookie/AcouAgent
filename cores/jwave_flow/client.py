"""Bounded NDJSON transport for the restricted jwave_flow agent bridge.

The upstream bridge exposes only the agent workflow. This module maps its
``node/complete/error`` stream into AcouAgent's stable ``progress/result``
contract and never imports or modifies jwave_codeagent.
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx

from app import config, execution

BASE_URL = config.JWAVE_FLOW_BASE_URL
BRIDGE_TOKEN = config.JWAVE_FLOW_BRIDGE_TOKEN
DEADLINE_SECONDS = config.JWAVE_FLOW_DEADLINE_SECONDS
READ_TIMEOUT_SECONDS = config.JWAVE_FLOW_READ_TIMEOUT_SECONDS
MAX_EVENT_BYTES = config.JWAVE_FLOW_MAX_EVENT_BYTES
MAX_REPORT_CHARS = 200_000

logger = logging.getLogger(__name__)

NODE_STAGES = {
    "analyze": "需求分析",
    "retrieve": "知识检索",
    "template": "模板转换",
    "extract": "参数提取",
    "param_check": "参数预检",
    "skill_resolve": "技能解析",
    "skill_execute": "固定技能执行",
    "generate": "代码生成",
    "static_check": "静态检查",
    "run_code": "执行仿真",
    "semantic_gate": "语义校验",
    "completion_review": "需求完成度评审",
    "fix_code": "代码纠错",
    "finalize": "输出",
}


def _reject_nonfinite(value: str) -> None:
    raise ValueError(f"nonfinite_json_value:{value}")


def _json_object(line: str) -> dict[str, Any]:
    value = json.loads(line, parse_constant=_reject_nonfinite)
    if not isinstance(value, dict):
        raise ValueError("jwave_flow_event_must_be_object")
    return value


async def bounded_lines(response: httpx.Response) -> AsyncIterator[str]:
    """Yield UTF-8 lines while bounding both one line and accumulated buffering."""
    pending = b""
    async for chunk in response.aiter_bytes():
        if not chunk:
            continue
        pending += chunk
        if len(pending) > MAX_EVENT_BYTES:
            raise ValueError("jwave_flow_event_size_limit")
        while b"\n" in pending:
            line, pending = pending.split(b"\n", 1)
            if len(line) > MAX_EVENT_BYTES:
                raise ValueError("jwave_flow_event_size_limit")
            yield line.rstrip(b"\r").decode("utf-8")
    if pending:
        if len(pending) > MAX_EVENT_BYTES:
            raise ValueError("jwave_flow_event_size_limit")
        yield pending.decode("utf-8")


def _text(value: Any, limit: int) -> str:
    if value is None:
        return ""
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    return text if len(text) <= limit else text[:limit] + "\n…（已截断）"


def _result(
    *,
    request_id: str,
    workflow_run_id: str,
    report: str,
    status: str,
    error_code: str | None = None,
    state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "event": "result",
        "report": report[:MAX_REPORT_CHARS],
        "workflow_status": status,
        "request_id": request_id,
        "workflow_run_id": workflow_run_id,
        "skill_usage": [],
        "orchestration_backend": "jwave_flow",
    }
    if error_code:
        payload["error_code"] = error_code
    if state:
        code = state.get("text") or state.get("code_final") or state.get("code") or ""
        usage = state.get("skill_usage")
        if not isinstance(usage, list):
            usage = []
        analysis = state.get("analysis")
        if not isinstance(analysis, dict):
            analysis = {}
        payload.update(
            {
                "code": _text(code, 40_000),
                "stdout": _text(state.get("stdout"), 24_000),
                "stderr": _text(state.get("stderr"), 8_000),
                "selfcheck": (
                    state.get("selfcheck")
                    if isinstance(state.get("selfcheck"), dict)
                    else {}
                ),
                "outputs": list(state.get("outputs") or [])[:50],
                "new_files": list(state.get("new_files") or [])[:50],
                "success": state.get("success") is True,
                "verified": state.get("verified") is True,
                "iterations": state.get("iterations"),
                "elapsed": state.get("elapsed"),
                "skill_usage": usage[:3],
                "skill_mode": str(state.get("skill_mode") or "fallback")[:32],
                "execution_reuse_verified": state.get("execution_reuse_verified") is True,
                "run_id": _text(state.get("run_id"), 128) or None,
                "analysis": analysis,
                "quality_level": analysis.get("quality_level") or state.get("skill_quality_level"),
            }
        )
        skill_query_json = state.get("skill_query_json")
        if isinstance(skill_query_json, str) and len(skill_query_json.encode("utf-8")) <= 8192:
            payload["skill_query_json"] = skill_query_json
    return payload


def _report(state: dict[str, Any]) -> tuple[str, str]:
    success = state.get("success") is True
    verified = state.get("verified") is True
    status = "succeeded" if success and verified else "error"
    analysis = state.get("analysis")
    if not isinstance(analysis, dict):
        analysis = {}
    selfcheck = state.get("selfcheck") if isinstance(state.get("selfcheck"), dict) else {}
    result = {
        "exit_code": state.get("exit_code", 0 if success else 1),
        "timed_out": state.get("timed_out") is True,
        "duration_ms": state.get("duration_ms"),
        "stdout": state.get("stdout", ""),
        "stderr": state.get("stderr", ""),
        "total_attempts": state.get("iterations") or 1,
        "image_base64": analysis.get("heatmap_png_base64") or analysis.get("waveform_png_base64"),
    }
    code = state.get("text") or state.get("code_final") or state.get("code") or ""
    report_status = None
    if success and not verified:
        report_status = "❌ **验证未通过** — 执行完成，但未通过确定性验证"
    report = execution._build_report(result, code, status_override=report_status)

    lines = [report, "", "## 确定性物理门", ""]
    quality = analysis.get("quality_level") or state.get("quality_level")
    summary = analysis.get("summary")
    if not summary and selfcheck:
        summary = (
            f"自检通过：finite={selfcheck.get('finite')}, "
            f"abs_max={selfcheck.get('abs_max')}, shape={selfcheck.get('shape')}"
        )
    lines.append(f"- 验证状态：{'通过' if verified else '未通过'}")
    lines.append(f"- 技能模式：{state.get('skill_mode', 'fallback')}")
    param_report = state.get("param_check_report")
    if isinstance(param_report, dict) and param_report.get("ok") is not None:
        lines.append(f"- 参数预检：{'通过' if param_report.get('ok') else '未通过'}")
    semantic_state = state.get("semantic_verified")
    if semantic_state is not None:
        lines.append(f"- 语义数值门：{'通过' if semantic_state else '未通过'}")
    if summary:
        lines.append(f"- 分析摘要：{summary}")
    if quality:
        lines.append(f"- 质量等级：{quality}")
    else:
        lines.append("- 质量等级：未评级（jwave_flow 语义门）")

    usage = state.get("skill_usage")
    if isinstance(usage, list) and usage:
        lines.extend(["", "### 技能库", ""])
        for item in usage[:3]:
            if isinstance(item, dict):
                skill_id = str(item.get("skill_id") or "未提供")
                version = item.get("version", "?")
                message = str(item.get("message") or "技能处理")
                lines.append(f"- {message}（{skill_id}，v{version}）")

    if state.get("execution_reuse_verified") is True:
        lines.append("- 技能复用：固定参考已重新实际执行并通过独立核验")

    diagnostics: list[str] = []
    for key in ("param_issues", "static_issues", "semantic_issues"):
        values = state.get(key)
        if isinstance(values, list) and values:
            diagnostics.extend(str(item) for item in values[:12])
    feedback = state.get("completion_feedback") or state.get("last_error")
    if feedback:
        diagnostics.append(str(feedback))
    if diagnostics:
        lines.extend(["", "### 未通过项", ""])
        lines.extend(f"- {item}" for item in diagnostics)

    review = state.get("completion_review")
    lines.extend(["", "## 模型审查（不能覆盖物理门）", ""])
    if isinstance(review, dict):
        review_status = "通过" if review.get("completed") is True else "未通过"
        lines.append(f"- 完成度评审：{review_status}")
        review_lines = []
        if review.get("missing_requirements"):
            review_lines.append("缺失项：" + "；".join(map(str, review["missing_requirements"])))
        if review.get("feedback"):
            review_lines.append(str(review["feedback"]))
        lines.append("\n".join(review_lines) if review_lines else "需求完成度评审：通过")
    else:
        lines.append("本次未获得独立的模型审查结果。")

    lines.extend(["", "## 物理解读", ""])
    if summary:
        lines.append(str(summary))
    else:
        lines.append("未获得额外的物理解读；请以执行记录和确定性物理门为准。")

    return "\n".join(lines), status


def _error_message(code: str) -> str:
    if code == "jwave_flow_unavailable":
        return (
            "无法连接 jwave_flow 核心，本次没有获得仿真结果。"
            "请检查 JWAVE_FLOW_BASE_URL 和上游服务状态。"
        )
    if code == "jwave_flow_timeout":
        return "jwave_flow 执行超时，本次没有获得完整仿真结果。"
    if code == "jwave_flow_invalid_stream":
        return "jwave_flow 返回了无效事件流，本次结果不可作为成功仿真。"
    if code == "jwave_flow_invalid_bridge":
        return (
            "目标服务不是受限的 jwave_flow Agent Bridge，"
            "本次没有执行工作流。"
        )
    if code == "jwave_flow_incomplete":
        return "jwave_flow 连接提前结束，未收到完整结果。"
    if code == "jwave_flow_model_config_missing":
        return "当前核心需要统一模型配置，但本次请求没有可用的模型 profile。"
    if code.startswith("jwave_flow_http_"):
        status = code.rsplit("_", 1)[-1]
        return f"jwave_flow 服务返回 HTTP {status}，本次结果不可作为成功仿真。"
    return "jwave_flow 执行失败，本次结果不可作为成功仿真。"


async def jwave_flow_events(
    query: str,
    *,
    model_config: dict[str, Any] | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Run one request and expose only normalized, client-safe events."""
    request_id = str(uuid.uuid4())
    workflow_run_id = str(uuid.uuid4())
    completed = False
    terminal_result: dict[str, Any] | None = None

    yield {
        "event": "progress",
        "stage": "jwave_flow：正在连接核心",
        "request_id": request_id,
        "workflow_run_id": workflow_run_id,
    }
    if not BASE_URL:
        yield _result(
            request_id=request_id,
            workflow_run_id=workflow_run_id,
            report=_error_message("jwave_flow_unavailable"),
            status="error",
            error_code="jwave_flow_unavailable",
        )
        return

    try:
        async with asyncio.timeout(DEADLINE_SECONDS):
            timeout = httpx.Timeout(READ_TIMEOUT_SECONDS, connect=10.0)
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
                health = await client.get(BASE_URL + "/health")
                if health.status_code != 200:
                    code = f"jwave_flow_http_{health.status_code}"
                    yield _result(
                        request_id=request_id,
                        workflow_run_id=workflow_run_id,
                        report=_error_message(code),
                        status="error",
                        error_code=code,
                    )
                    completed = True
                    return
                try:
                    bridge_info = health.json()
                except ValueError:
                    bridge_info = None
                capabilities = (
                    bridge_info.get("capabilities")
                    if isinstance(bridge_info, dict)
                    else None
                )
                expected_mode = config.effective_model_config_mode()
                bridge_mode = (
                    bridge_info.get("model_config_mode")
                    if isinstance(bridge_info, dict)
                    else None
                )
                bridge_managed = (
                    capabilities.get("model_configuration") is True
                    if isinstance(capabilities, dict)
                    else False
                )
                if (
                    not isinstance(bridge_info, dict)
                    or bridge_info.get("service") != "jwave-flow-agent-bridge"
                    or bridge_info.get("api_version") != "v1"
                    or not isinstance(capabilities, dict)
                    or capabilities.get("workflow") is not True
                    or bridge_mode != expected_mode
                    or (expected_mode == "managed") is not bridge_managed
                    or capabilities.get("skill_port") is not True
                    or any(
                        capabilities.get(capability) is True
                        for capability in (
                            "knowledge_base_management",
                            "history",
                            "memory",
                            "sessions",
                        )
                    )
                ):
                    yield _result(
                        request_id=request_id,
                        workflow_run_id=workflow_run_id,
                        report=_error_message("jwave_flow_invalid_bridge"),
                        status="error",
                        error_code="jwave_flow_invalid_bridge",
                    )
                    completed = True
                    return
                if expected_mode == "managed" and model_config is None:
                    yield _result(
                        request_id=request_id,
                        workflow_run_id=workflow_run_id,
                        report=_error_message("jwave_flow_model_config_missing"),
                        status="error",
                        error_code="jwave_flow_model_config_missing",
                    )
                    completed = True
                    return
                request_body: dict[str, Any] = {"query": query}
                if model_config is not None:
                    request_body["model_config"] = model_config
                async with client.stream(
                    "POST",
                    BASE_URL + "/v1/run",
                    headers={"X-Jwave-Flow-Bridge-Token": BRIDGE_TOKEN},
                    json=request_body,
                ) as response:
                    if response.status_code >= 400:
                        code = f"jwave_flow_http_{response.status_code}"
                        yield _result(
                            request_id=request_id,
                            workflow_run_id=workflow_run_id,
                            report=_error_message(code),
                            status="error",
                            error_code=code,
                        )
                        completed = True
                        return
                    content_type = response.headers.get("content-type", "").lower()
                    if "application/x-ndjson" not in content_type:
                        yield _result(
                            request_id=request_id,
                            workflow_run_id=workflow_run_id,
                            report=_error_message("jwave_flow_invalid_stream"),
                            status="error",
                            error_code="jwave_flow_invalid_stream",
                        )
                        completed = True
                        return

                    async for line in bounded_lines(response):
                        if not line.strip():
                            continue
                        event = _json_object(line)
                        event_type = event.get("type")
                        if event_type == "node":
                            node = str(event.get("node") or "node")[:80]
                            state = (
                                event.get("state")
                                if isinstance(event.get("state"), dict)
                                else {}
                            )
                            trace = (
                                state.get("trace")
                                if isinstance(state.get("trace"), list)
                                else []
                            )
                            stage = "jwave_flow：" + NODE_STAGES.get(node, node)
                            notice = {
                                "event": "progress",
                                "stage": stage,
                                "node": node,
                                "request_id": request_id,
                                "workflow_run_id": workflow_run_id,
                            }
                            if trace:
                                notice["detail"] = _text(trace[-1], 240)
                            yield notice
                        elif event_type == "complete":
                            state = event.get("state")
                            if not isinstance(state, dict):
                                terminal_result = _result(
                                    request_id=request_id,
                                    workflow_run_id=workflow_run_id,
                                    report=_error_message("jwave_flow_invalid_stream"),
                                    status="error",
                                    error_code="jwave_flow_invalid_stream",
                                )
                            else:
                                report, status = _report(state)
                                terminal_result = _result(
                                    request_id=request_id,
                                    workflow_run_id=workflow_run_id,
                                    report=report,
                                    status=status,
                                    state=state,
                                )
                            completed = True
                        elif event_type == "error":
                            if terminal_result is not None:
                                continue
                            yield _result(
                                request_id=request_id,
                                workflow_run_id=workflow_run_id,
                                report=_error_message("jwave_flow_failed"),
                                status="error",
                                error_code="jwave_flow_failed",
                            )
                            completed = True
                            return
                    if terminal_result is not None:
                        yield terminal_result
                        return
    except TimeoutError:
        if terminal_result is not None:
            yield terminal_result
            return
        logger.warning("jwave_flow request timed out")
        yield _result(
            request_id=request_id,
            workflow_run_id=workflow_run_id,
            report=_error_message("jwave_flow_timeout"),
            status="error",
            error_code="jwave_flow_timeout",
        )
        completed = True
    except (httpx.HTTPError, OSError):
        if terminal_result is not None:
            yield terminal_result
            return
        logger.warning("jwave_flow transport unavailable")
        yield _result(
            request_id=request_id,
            workflow_run_id=workflow_run_id,
            report=_error_message("jwave_flow_unavailable"),
            status="error",
            error_code="jwave_flow_unavailable",
        )
        completed = True
    except (ValueError, TypeError, UnicodeError):
        if terminal_result is not None:
            yield terminal_result
            return
        logger.warning("jwave_flow returned an invalid stream")
        yield _result(
            request_id=request_id,
            workflow_run_id=workflow_run_id,
            report=_error_message("jwave_flow_invalid_stream"),
            status="error",
            error_code="jwave_flow_invalid_stream",
        )
        completed = True
    except Exception as exc:
        if terminal_result is not None:
            yield terminal_result
            return
        logger.error("unexpected jwave_flow client failure: %s", type(exc).__name__)
        yield _result(
            request_id=request_id,
            workflow_run_id=workflow_run_id,
            report=_error_message("jwave_flow_failed"),
            status="error",
            error_code="jwave_flow_failed",
        )
        completed = True

    if not completed:
        yield _result(
            request_id=request_id,
            workflow_run_id=workflow_run_id,
            report=_error_message("jwave_flow_incomplete"),
            status="error",
            error_code="jwave_flow_incomplete",
        )


__all__ = ["bounded_lines", "jwave_flow_events"]
