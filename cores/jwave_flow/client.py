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

from app import config

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
            }
        )
    return payload


def _report(state: dict[str, Any]) -> tuple[str, str]:
    success = state.get("success") is True
    verified = state.get("verified") is True
    status = "succeeded" if success and verified else "error"
    lines = [
        "## jwave_flow 仿真结果",
        "",
        f"- 执行状态：{'成功' if success else '失败或未完成'}",
        f"- 确定性验证：{'通过' if verified else '未通过'}",
        f"- 迭代次数：{state.get('iterations', 0)}",
        f"- 用时：{state.get('elapsed', 0)} s",
    ]

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

    stdout = _text(state.get("stdout"), 24_000)
    if stdout:
        lines.extend(["", "### 程序输出", "", "```text", stdout, "```"])

    stderr = _text(state.get("stderr"), 8_000)
    if stderr:
        lines.extend(["", "### 标准错误", "", "```text", stderr, "```"])

    selfcheck = state.get("selfcheck")
    if isinstance(selfcheck, dict) and selfcheck:
        lines.extend(
            [
                "",
                "### 自检数据",
                "",
                "```json",
                json.dumps(selfcheck, ensure_ascii=False, indent=2, default=str),
                "```",
            ]
        )

    code = state.get("text") or state.get("code_final") or state.get("code") or ""
    if code:
        lines.extend(
            [
                "",
                "### 最终代码",
                "",
                "```python",
                _text(code, 40_000),
                "```",
            ]
        )
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
