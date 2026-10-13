"""Web 门户路由：门户/看板/报告/缓存/Demo 页面与 API。

通过 `register(mcp)` 向网关注册 custom_route。
"""

import json
from pathlib import Path as _Path

import httpx
from starlette.requests import Request
from starlette.responses import HTMLResponse, PlainTextResponse

from app import cache_store, config, execution
from app.dashboard import (
    CHAT_HTML,
    clear_executions,
    get_execution,
    get_executions,
    get_stats,
)


_NO_CACHE = {"Cache-Control": "no-cache, no-store, must-revalidate"}


def _serve_html(filename: str) -> HTMLResponse:
    html = (_Path(__file__).resolve().parent / "web" / filename).read_text(encoding="utf-8")
    return HTMLResponse(html, headers=_NO_CACHE)


def _extract_report(output) -> str:
    """从 Dify 工作流 outputs.text 中提取 Markdown 报告字符串。"""
    if isinstance(output, str):
        return output
    if isinstance(output, list) and output:
        first = output[0]
        if isinstance(first, str):
            return first
        if isinstance(first, dict):
            return first.get("result", "") or first.get("report", "") or json.dumps(first, ensure_ascii=False)
    if isinstance(output, dict):
        return output.get("result", "") or output.get("report", "") or json.dumps(output, ensure_ascii=False)
    return ""


async def _merge_requirement(requirement: str, message: str, model_config: dict | None = None) -> str:
    """用 DeepSeek 把「历史需求 + 本轮增量修改」合并成完整需求。"""
    cfg = model_config or {}
    api_key = (cfg.get("api_key") or config.DEEPSEEK_API_KEY).strip()
    if not api_key:
        return f"{requirement}；{message}"
    base_url = (cfg.get("base_url") or "https://api.deepseek.com").rstrip("/")
    endpoint = base_url if base_url.endswith("/chat/completions") else base_url + "/chat/completions"
    model = (cfg.get("model") or config.DEEPSEEK_MODEL).strip()
    prompt = (
        "你是声学仿真需求整理助手。请根据「当前完整需求」和「用户新修改」，输出更新后的完整需求。\n"
        "要求：输出一段完整的中文需求描述，包含所有当前有效的仿真参数"
        "（如声源频率、网格大小、仿真区域、声速、介质/异质结构、传感器位置、仿真时长等）；"
        "用户的新修改要覆盖旧值；只输出需求本身，不要任何解释或前缀。\n\n"
        f"当前完整需求：\n{requirement}\n\n"
        f"用户新修改：\n{message}\n\n"
        "更新后的完整需求："
    )
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                endpoint,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.1,
                    "max_tokens": 1200,
                },
            )
        resp.raise_for_status()
        merged = resp.json()["choices"][0]["message"]["content"].strip()
        return merged or f"{requirement}；{message}"
    except Exception:
        return f"{requirement}；{message}"


def register(mcp) -> None:
    """向 FastMCP 实例注册本模块的全部 Web 路由。"""

    @mcp.custom_route("/health", methods=["GET"])
    async def health(_: Request) -> PlainTextResponse:
        return PlainTextResponse("ok")

    @mcp.custom_route("/cache", methods=["GET"])
    async def cache_page(_: Request) -> PlainTextResponse:
        return _serve_html("cache.html")

    @mcp.custom_route("/chat", methods=["POST"])
    async def chat_api(request: Request) -> PlainTextResponse:
        """多轮对话：合并历史需求 → 调用 Dify 工作流执行仿真。

        会话隔离：前端每次请求携带 session_id（浏览器 localStorage 持久化），
        Dify 侧 user 按会话区分（portal-<session>），避免多用户共享 Dify 会话上下文。
        """
        body = await request.json()
        message = (body.get("message") or "").strip()
        requirement = (body.get("requirement") or "").strip()
        session_id = (body.get("session_id") or "").strip()[:24]
        model_config = body.get("model_config") if isinstance(body.get("model_config"), dict) else None
        if not message:
            return PlainTextResponse(
                json.dumps({"error": "message is required"}, ensure_ascii=False),
                status_code=400, media_type="application/json",
            )

        if requirement:
            full_requirement = await _merge_requirement(requirement, message, model_config)
            merge_used = True
        else:
            full_requirement = message
            merge_used = False

        # 按会话隔离 Dify user（有 session 时用 portal-<session>，否则回退 portal）
        dify_user = f"portal-{session_id}" if session_id else "portal"

        dify_url = "http://nginx/v1/workflows/run"
        try:
            async with httpx.AsyncClient(timeout=180) as client:
                resp = await client.post(
                    dify_url,
                    headers={
                        "Authorization": f"Bearer {config.DIFY_API_KEY}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "inputs": {"query": full_requirement},
                        "response_mode": "blocking",
                        "user": dify_user,
                    },
                )
            data = resp.json()
        except Exception as exc:
            return PlainTextResponse(
                json.dumps({
                    "report": f"_(调用 Dify 工作流失败：{exc})_",
                    "requirement": full_requirement,
                    "merge_used": merge_used,
                    "workflow_status": "error",
                }, ensure_ascii=False),
                media_type="application/json",
            )

        wf = data.get("data", {})
        report = _extract_report(wf.get("outputs", {}).get("text"))
        if not report or not report.strip():
            report = f"_(工作流返回空结果, status={wf.get('status')}, error={wf.get('error')})_"

        return PlainTextResponse(
            json.dumps({
                "report": report,
                "requirement": full_requirement,
                "merge_used": merge_used,
                "workflow_status": wf.get("status"),
            }, ensure_ascii=False),
            media_type="application/json",
        )

    @mcp.custom_route("/", methods=["GET"])
    async def portal(_: Request) -> PlainTextResponse:
        return HTMLResponse(CHAT_HTML, headers=_NO_CACHE)

    @mcp.custom_route("/portal", methods=["GET"])
    async def portal_page(_: Request) -> PlainTextResponse:
        return HTMLResponse(CHAT_HTML, headers=_NO_CACHE)

    @mcp.custom_route("/chat", methods=["GET"])
    async def chat_page(_: Request) -> PlainTextResponse:
        return HTMLResponse(CHAT_HTML, headers=_NO_CACHE)

    @mcp.custom_route("/dashboard", methods=["GET"])
    async def dashboard(_: Request) -> PlainTextResponse:
        return _serve_html("dashboard.html")

    @mcp.custom_route("/dashboard/api/executions", methods=["GET"])
    async def dashboard_api(request: Request) -> PlainTextResponse:
        since = int(request.query_params.get("since", "0"))
        limit = min(int(request.query_params.get("limit", "100")), 500)
        return PlainTextResponse(
            json.dumps({
                "executions": get_executions(limit=limit, since_id=since),
                "stats": get_stats(),
            }, ensure_ascii=False),
            media_type="application/json",
        )

    @mcp.custom_route("/dashboard/api/executions", methods=["DELETE"])
    async def clear_executions_api(_: Request) -> PlainTextResponse:
        count = clear_executions()
        return PlainTextResponse(
            json.dumps({"deleted": count, "ok": True}, ensure_ascii=False),
            media_type="application/json",
        )

    @mcp.custom_route("/dashboard/api/rerun", methods=["POST"])
    async def rerun_execution_api(request: Request) -> PlainTextResponse:
        """Re-run an existing execution record in the same sandbox."""
        body = await request.json()
        execution_id = int(body.get("id", 0))
        row = get_execution(execution_id) if execution_id else None
        if not row or not row.get("code"):
            return PlainTextResponse(
                json.dumps({"ok": False, "error": "记录不存在或没有可执行代码"}, ensure_ascii=False),
                status_code=404,
                media_type="application/json",
            )
        try:
            code = execution._clean_code(row["code"])
            result = execution._execute_code(code, 15)
            result["stdout"] = execution._shrink_field_in_stdout(result.get("stdout", ""))
            execution._normalize_result_artifacts(result)
            return PlainTextResponse(
                json.dumps({"ok": True, "result": result}, ensure_ascii=False),
                media_type="application/json",
            )
        except Exception as exc:  # noqa: BLE001
            return PlainTextResponse(
                json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False),
                status_code=500,
                media_type="application/json",
            )

    @mcp.custom_route("/dashboard/api/cache_stats", methods=["GET"])
    async def cache_stats_api(_: Request) -> PlainTextResponse:
        return PlainTextResponse(
            json.dumps(cache_store.get_cache_stats(), ensure_ascii=False),
            media_type="application/json",
        )
