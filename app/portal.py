"""Web 门户路由：门户/看板/报告/缓存/Demo 页面与 API。

通过 `register(mcp)` 向网关注册 custom_route。
"""

import json
import secrets
import uuid
from pathlib import Path as _Path

from starlette.requests import Request
from starlette.responses import HTMLResponse, PlainTextResponse, StreamingResponse

from app import (
    cache_store,
    config,
    execution,
    local_workflow,
    model_client,
    model_preflight,
    model_profiles,
    skill_library_service,
)
from app.dashboard import (
    CHAT_HTML,
    clear_executions,
    get_analysis_updates,
    get_execution,
    get_executions,
    get_latest_analysis_by_run_ids,
    get_stats,
    record_admin_event,
    record_execution,
)
from app.ui_chrome import render_page
from app.workflow_client import workflow_events


def _serve_html(filename: str) -> HTMLResponse:
    html = (_Path(__file__).resolve().parent / "web" / filename).read_text(encoding="utf-8")
    return HTMLResponse(render_page(html), headers={"Cache-Control": "no-store"})


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


async def _merge_requirement(requirement: str, message: str,
                             model_profile: str | None = None) -> str:
    """Merge turns through the selected BYOK profile; fail closed to concatenation."""
    prompt = (
        "你是声学仿真需求整理助手。根据当前完整需求和用户新修改，输出更新后的完整需求。"
        "新修改覆盖旧值；只输出需求，不要解释。\n\n"
        f"当前完整需求：\n{requirement}\n\n用户新修改：\n{message}"
    )
    try:
        result = await model_client.complete(
            model_profile,
            "requirement_merge",
            [{"role": "user", "content": prompt}],
        )
        return result["content"]
    except (model_profiles.ModelProfileError, model_client.ModelCallError):
        return f"{requirement}；{message}"


def register(mcp) -> None:
    """向 FastMCP 实例注册本模块的全部 Web 路由。"""
    from app.skill_api import register as register_skill_api
    register_skill_api(mcp)

    @mcp.custom_route("/health", methods=["GET"])
    async def health(_: Request) -> PlainTextResponse:
        return PlainTextResponse("ok")

    @mcp.custom_route("/api/model-profiles", methods=["GET"])
    async def model_profiles_api(_: Request) -> PlainTextResponse:
        try:
            payload = model_profiles.public_profiles()
            payload["orchestration_backend"] = config.ORCHESTRATION_BACKEND
            status = 200
        except model_profiles.ModelProfileError as exc:
            payload = {"profiles": [], "default_profile": None,
                       "orchestration_backend": config.ORCHESTRATION_BACKEND,
                       "error": str(exc)}
            status = 503
        return PlainTextResponse(json.dumps(payload, ensure_ascii=False), status_code=status,
                                 media_type="application/json",
                                 headers={"Cache-Control": "no-store"})

    @mcp.custom_route("/api/byok/session", methods=["POST"])
    async def create_byok_session(request: Request) -> PlainTextResponse:
        """Create an expiring in-memory model profile for this self-hosted portal."""
        raw = await request.body()
        if len(raw) > 8192:
            payload, status = {"ok": False, "error": "byok_payload_too_large"}, 413
        else:
            try:
                body = json.loads(raw)
                created = model_profiles.register_session_profile(body)
                payload, status = {"ok": True, **created}, 201
            except (json.JSONDecodeError, UnicodeError):
                payload, status = {"ok": False, "error": "byok_payload_invalid"}, 400
            except model_profiles.ModelProfileError as exc:
                payload, status = {"ok": False, "error": str(exc)}, 400
        return PlainTextResponse(json.dumps(payload, ensure_ascii=False), status_code=status,
                                 media_type="application/json",
                                 headers={"Cache-Control": "no-store"})

    @mcp.custom_route("/api/model-profiles/test", methods=["POST"])
    async def test_model_profile(request: Request) -> PlainTextResponse:
        """Run one bounded model call without executing returned code."""
        raw = await request.body()
        if len(raw) > 1024:
            payload, status = {"ok": False, "error": "model_preflight_payload_too_large"}, 413
        else:
            try:
                body = json.loads(raw)
                if not isinstance(body, dict) or set(body) != {"profile_id"}:
                    raise ValueError("model_preflight_payload_invalid")
                profile_id = body.get("profile_id")
                if not isinstance(profile_id, str) or not 1 <= len(profile_id) <= 64:
                    raise ValueError("model_preflight_profile_invalid")
                payload = await model_preflight.run(profile_id)
                status = 200
            except (json.JSONDecodeError, UnicodeError):
                payload, status = {"ok": False, "error": "model_preflight_payload_invalid"}, 400
            except model_preflight.ModelPreflightBusy as exc:
                payload, status = {"ok": False, "error": str(exc)}, 429
            except model_profiles.ModelProfileError as exc:
                payload, status = {"ok": False, "error": str(exc)}, 400
            except ValueError as exc:
                payload, status = {"ok": False, "error": str(exc)}, 400
            except model_client.ModelCallError as exc:
                payload = {
                    "ok": False,
                    "connected": False,
                    "error": str(exc),
                    "full_simulation_validated": False,
                }
                status = 502
        return PlainTextResponse(
            json.dumps(payload, ensure_ascii=False),
            status_code=status,
            media_type="application/json",
            headers={"Cache-Control": "no-store"},
        )

    @mcp.custom_route("/api/byok/session", methods=["DELETE"])
    async def delete_byok_session(request: Request) -> PlainTextResponse:
        raw = await request.body()
        try:
            body = json.loads(raw)
            profile_id = body.get("profile_id") if isinstance(body, dict) else None
        except (json.JSONDecodeError, UnicodeError):
            profile_id = None
        removed = model_profiles.remove_session_profile(profile_id)
        return PlainTextResponse(
            json.dumps({"ok": removed, "error": None if removed else "byok_session_unknown"},
                       ensure_ascii=False),
            status_code=200 if removed else 404,
            media_type="application/json",
            headers={"Cache-Control": "no-store"},
        )

    def require_admin(
        request: Request, action: str, target_id: str | None = None
    ) -> PlainTextResponse | None:
        """Fail closed for state-changing portal endpoints and audit every attempt."""
        supplied = request.headers.get("X-Admin-Token", "")
        expected = config.PORTAL_ADMIN_TOKEN
        remote_host = request.client.host if request.client else None
        allowed = bool(expected) and secrets.compare_digest(supplied, expected)
        record_admin_event(action, target_id=target_id, remote_host=remote_host, allowed=allowed,
                           detail="authorized" if allowed else "missing or invalid admin token")
        if not allowed:
            return PlainTextResponse(
                json.dumps({"ok": False, "error": "管理员认证失败"}, ensure_ascii=False),
                status_code=403,
                media_type="application/json",
            )
        return None

    @mcp.custom_route("/cache", methods=["GET"])
    async def cache_page(_: Request) -> PlainTextResponse:
        return _serve_html("cache.html")

    @mcp.custom_route("/chat", methods=["POST"])
    async def chat_api(request: Request) -> PlainTextResponse:
        """多轮对话：合并历史需求 → 调用本地或 Dify 兼容工作流。

        会话隔离：前端每次请求携带 session_id（浏览器 localStorage 持久化），
        Dify 侧 user 按会话区分（portal-<session>），避免多用户共享 Dify 会话上下文。
        """
        try:
            body = await request.json()
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
            return PlainTextResponse(
                json.dumps({"error": "invalid_request"}, ensure_ascii=False),
                status_code=400,
                media_type="application/json",
            )
        if not isinstance(body, dict):
            return PlainTextResponse(
                json.dumps({"error": "invalid_request"}, ensure_ascii=False),
                status_code=400,
                media_type="application/json",
            )
        message = (body.get("message") or "").strip()
        requirement = (body.get("requirement") or "").strip()
        session_id = (body.get("session_id") or "").strip()[:24]
        model_profile = body.get("model_profile")
        if model_profile is not None and (
            not isinstance(model_profile, str) or len(model_profile) > 64
        ):
            return PlainTextResponse(
                json.dumps({"error": "invalid model_profile"}, ensure_ascii=False),
                status_code=400, media_type="application/json",
            )
        if not message:
            return PlainTextResponse(
                json.dumps({"error": "message is required"}, ensure_ascii=False),
                status_code=400, media_type="application/json",
            )

        if requirement:
            full_requirement = await _merge_requirement(requirement, message, model_profile)
            merge_used = True
        else:
            full_requirement = message
            merge_used = False

        # 按会话隔离 Dify user（有 session 时用 portal-<session>，否则回退 portal）
        dify_user = f"portal-{session_id}" if session_id else "portal"

        async def events():
            source = (local_workflow.workflow_events(full_requirement, dify_user, model_profile)
                      if config.ORCHESTRATION_BACKEND == "local"
                      else workflow_events(full_requirement, dify_user, config.DIFY_API_KEY))
            async for item in source:
                if item["event"] == "result":
                    if "outputs" in item:
                        item["report"] = _extract_report(item.pop("outputs").get("text"))
                    item.update(requirement=full_requirement, merge_used=merge_used,
                                orchestration_backend=item.get("orchestration_backend",
                                                               config.ORCHESTRATION_BACKEND))
                yield item

        if body.get("stream") is True:
            async def ndjson():
                async for item in events():
                    yield json.dumps(item, ensure_ascii=False) + "\n"
            return StreamingResponse(ndjson(), media_type="application/x-ndjson",
                                     headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})
        async for item in events():
            if item["event"] == "result":
                return PlainTextResponse(json.dumps(item, ensure_ascii=False),
                                         media_type="application/json")

    @mcp.custom_route("/", methods=["GET"])
    async def portal(_: Request) -> PlainTextResponse:
        return HTMLResponse(render_page(CHAT_HTML), headers={"Cache-Control": "no-store"})

    @mcp.custom_route("/portal", methods=["GET"])
    async def portal_page(_: Request) -> PlainTextResponse:
        return HTMLResponse(render_page(CHAT_HTML), headers={"Cache-Control": "no-store"})

    @mcp.custom_route("/chat", methods=["GET"])
    async def chat_page(_: Request) -> PlainTextResponse:
        return HTMLResponse(render_page(CHAT_HTML), headers={"Cache-Control": "no-store"})

    @mcp.custom_route("/dashboard", methods=["GET"])
    async def dashboard(_: Request) -> PlainTextResponse:
        return _serve_html("dashboard.html")

    @mcp.custom_route("/dashboard/api/executions", methods=["GET"])
    async def dashboard_api(request: Request) -> PlainTextResponse:
        since = int(request.query_params.get("since", "0"))
        analysis_since = int(request.query_params.get("analysis_since", "0"))
        limit = min(int(request.query_params.get("limit", "100")), 500)
        executions = get_executions(limit=limit, since_id=since)
        analysis_updates = get_analysis_updates(limit=limit, since_id=analysis_since)
        analyses = get_latest_analysis_by_run_ids([
            row["run_id"] for row in executions if isinstance(row.get("run_id"), str)
        ])
        for row in executions:
            if row.get("run_id") in analyses:
                row["analysis"] = analyses[row["run_id"]]
        if config.SKILL_LIBRARY_ENABLED is True:
            links = skill_library_service.STORE.read_execution_links([
                row["run_id"] for row in executions if isinstance(row.get("run_id"), str)
            ])
            for row in executions:
                if row.get("run_id") in links:
                    row["skill_reference"] = links[row["run_id"]]
        return PlainTextResponse(
            json.dumps({
                "executions": executions,
                "analysis_updates": analysis_updates,
                "stats": get_stats(),
            }, ensure_ascii=False, allow_nan=False),
            media_type="application/json",
        )

    @mcp.custom_route("/dashboard/api/executions", methods=["DELETE"])
    async def clear_executions_api(request: Request) -> PlainTextResponse:
        denied = require_admin(request, "clear_executions")
        if denied:
            return denied
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
        denied = require_admin(request, "rerun_execution", str(execution_id) if execution_id else None)
        if denied:
            return denied
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
            run_id = str(uuid.uuid4())
            result["run_id"] = run_id
            record_execution(
                tool_name="dashboard_rerun",
                code=code,
                exit_code=result.get("exit_code"),
                timed_out=result.get("timed_out", False),
                duration_ms=result.get("duration_ms"),
                stdout=result.get("stdout", ""),
                stderr=result.get("stderr", ""),
                image_base64=result.get("image_base64"),
                run_id=run_id,
            )
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
