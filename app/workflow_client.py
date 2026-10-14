"""Bounded Dify streaming transport; no blind replay of paid workflow requests."""
import asyncio
import json
import logging
import re
import uuid
import httpx
from app import config

BASE_URL = config.DIFY_BASE_URL
DEADLINE_SECONDS = 180
MAX_EVENT_BYTES = 8 * 1024 * 1024
logger = logging.getLogger(__name__)


async def bounded_lines(response):
    pending = b""
    async for chunk in response.aiter_bytes():
        pending += chunk
        if len(pending) > MAX_EVENT_BYTES:
            raise ValueError("line_size_limit")
        while b"\n" in pending:
            line, pending = pending.split(b"\n", 1)
            yield line.rstrip(b"\r").decode("utf-8")
    if pending:
        yield pending.decode("utf-8")


def failure_message(error):
    text = str(error).lower()
    if "timeout" in text or "timed out" in text:
        return "模型服务响应超时，本次未获得完整仿真结果。请稍后重试。"
    if "quota" in text or "balance" in text or "rate limit" in text or "429" in text:
        return "模型服务额度不足或繁忙，本次未完成，请稍后重试或检查服务配置。"
    return "工作流执行失败，本次结果不可作为成功仿真。请用请求编号排查服务记录。"


async def workflow_events(query, user, api_key):
    request_id = str(uuid.uuid4())
    task_id = None
    run_id = None
    completed = False
    skill_notices = []
    stop_status = "not_needed"
    error = None
    yield {"event": "progress", "stage": "正在连接工作流", "request_id": request_id}
    headers = {"Authorization": f"Bearer {api_key}"}
    try:
        async with asyncio.timeout(DEADLINE_SECONDS):
            async with httpx.AsyncClient(timeout=httpx.Timeout(45, connect=10), follow_redirects=False) as client:
                async with client.stream("POST", BASE_URL + "/workflows/run", headers=headers,
                                         json={"inputs": {"query": query}, "user": user,
                                               "response_mode": "streaming"}) as response:
                    response.raise_for_status()
                    if "text/event-stream" not in response.headers.get("content-type", ""):
                        raise ValueError("invalid_stream_content_type")
                    pieces = []
                    size = 0
                    async for line in bounded_lines(response):
                        size += len(line.encode("utf-8"))
                        if size > MAX_EVENT_BYTES:
                            raise ValueError("event_size_limit")
                        if line:
                            if line.startswith("data:"):
                                pieces.append(line[5:].lstrip())
                            continue
                        if not pieces:
                            size = 0
                            continue
                        item = json.loads("\n".join(pieces))
                        pieces, size = [], 0
                        if not isinstance(item, dict):
                            raise ValueError("invalid_event")
                        candidate = item.get("task_id")
                        if isinstance(candidate, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,128}", candidate):
                            task_id = candidate
                        run_id = item.get("workflow_run_id") or run_id
                        data = item.get("data") or {}
                        if not isinstance(data, dict):
                            raise ValueError("invalid_event_data")
                        event = item.get("event")
                        if event == "node_started":
                            # Never forward inputs, outputs, tracebacks or provider credentials.
                            stage = str(data.get("title") or "处理中")[:80]
                            yield {"event": "progress", "stage": stage, "request_id": request_id,
                                   "workflow_run_id": run_id}
                        elif event == "node_finished":
                            from app.skill_usage import reference_notice, execution_node_notice
                            notice=reference_notice(data) or execution_node_notice(data)
                            if notice and notice not in skill_notices and len(skill_notices)<6:
                                skill_notices.append(notice)
                                yield {"event":"skill_usage","skill_usage":notice,"request_id":request_id}
                        elif event == "workflow_finished":
                            completed = True
                            if data.get("status") != "succeeded":
                                error = failure_message(data.get("error", ""))
                                break
                            outputs = data.get("outputs")
                            if not isinstance(outputs, dict) or not outputs.get("text"):
                                error = "工作流结束但未返回报告，本次未获得完整结果。"
                                break
                            logger.info("workflow request_id=%s workflow_run_id=%s status=succeeded",
                                        request_id, run_id)
                            yield {"event": "result", "outputs": outputs, "workflow_status": "succeeded",
                                   "elapsed_time": data.get("elapsed_time"), "request_id": request_id,
                                   "workflow_run_id": run_id,"skill_usage":skill_notices}
                            return
                        elif event == "error":
                            error = failure_message(item.get("message", ""))
                            break
                    if error is None:
                        error = "工作流连接提前中断，未收到完成回执；请勿把部分输出视为成功。"
    except (TimeoutError, httpx.TimeoutException):
        error = "等待工作流超时，本次未获得完整结果。请稍后重试。"
    except httpx.HTTPStatusError as exc:
        code = exc.response.status_code
        error = ("工作流认证失败，请检查服务器配置。" if code in (401, 403)
                 else f"工作流服务暂不可用（HTTP {code}），请稍后重试。")
    except (httpx.HTTPError, ValueError, TypeError, UnicodeError):
        error = "工作流连接或响应异常，本次未获得完整结果。"
    finally:
        if task_id and not completed:
            stop_status = "unconfirmed"
            try:
                async with httpx.AsyncClient(timeout=5, follow_redirects=False) as client:
                    response = await client.post(BASE_URL + f"/workflows/tasks/{task_id}/stop",
                                                 headers=headers, json={"user": user})
                    response.raise_for_status()
                    stop_status = "requested"
            except (httpx.HTTPError, asyncio.CancelledError):
                pass
    logger.info("workflow request_id=%s workflow_run_id=%s status=error stop_status=%s",
                request_id, run_id, stop_status)
    yield {"event": "result", "report": error, "workflow_status": "error",
           "request_id": request_id, "workflow_run_id": run_id, "stop_status": stop_status,"skill_usage":skill_notices}
