"""Web 门户路由：门户/看板/报告/缓存/Demo 页面与 API。

通过 `register(mcp)` 向网关注册 custom_route。
"""

import json
import secrets
import uuid
from pathlib import Path as _Path

from starlette.requests import Request
from starlette.responses import HTMLResponse, PlainTextResponse

from app import config
from app.runtime_bus import RUNTIME_BUS
from app.ui_chrome import render_page


def get_execution_by_run_id(run_id: str):
    """Compatibility seam for the dashboard route and its focused tests."""
    return RUNTIME_BUS.get_execution_row_by_run_id(run_id)


get_executions = RUNTIME_BUS.dashboard_executions
get_analysis_updates = RUNTIME_BUS.dashboard_analysis_updates
get_latest_analysis_by_run_ids = RUNTIME_BUS.get_latest_analysis_by_run_ids


def _serve_html(filename: str) -> HTMLResponse:
    html = (_Path(__file__).resolve().parent / "web" / filename).read_text(encoding="utf-8")
    return HTMLResponse(render_page(html), headers={"Cache-Control": "no-store"})


def register(mcp) -> None:
    """向 FastMCP 实例注册本模块的全部 Web 路由。"""
    from app.skill_authoring_api import register as register_authoring_api
    register_authoring_api(mcp)
    from app.skill_api import register as register_skill_api
    register_skill_api(mcp)

    @mcp.custom_route("/health", methods=["GET"])
    async def health(_: Request) -> PlainTextResponse:
        return PlainTextResponse("ok")

    @mcp.custom_route("/assets/{filename}", methods=["GET"])
    async def asset(request: Request) -> PlainTextResponse:
        """Serve only the fixed static assets used by the replaceable chat UI."""
        filename = request.path_params["filename"]
        media_types = {
            "chat.css": "text/css; charset=utf-8",
            "chat.js": "text/javascript; charset=utf-8",
            "agent-client.js": "text/javascript; charset=utf-8",
        }
        media_type = media_types.get(filename)
        if media_type is None:
            return PlainTextResponse("not found", status_code=404)
        path = _Path(__file__).resolve().parent / "web" / filename
        return PlainTextResponse(path.read_text(encoding="utf-8"), media_type=media_type,
                                 headers={"Cache-Control": "no-store"})

    def require_admin(
        request: Request, action: str, target_id: str | None = None
    ) -> PlainTextResponse | None:
        """Fail closed for state-changing portal endpoints and audit every attempt."""
        supplied = request.headers.get("X-Admin-Token", "")
        expected = config.PORTAL_ADMIN_TOKEN
        remote_host = request.client.host if request.client else None
        allowed = bool(expected) and secrets.compare_digest(supplied, expected)
        RUNTIME_BUS.require_admin_record(
            action,
            target_id,
            request,
            allowed=allowed,
            detail="authorized" if allowed else "missing or invalid admin token",
        )
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

    @mcp.custom_route("/", methods=["GET"])
    async def portal(_: Request) -> PlainTextResponse:
        return _serve_html("chat.html")

    @mcp.custom_route("/portal", methods=["GET"])
    async def portal_page(_: Request) -> PlainTextResponse:
        return _serve_html("chat.html")

    @mcp.custom_route("/chat", methods=["GET"])
    async def chat_page(_: Request) -> PlainTextResponse:
        return _serve_html("chat.html")

    @mcp.custom_route("/dashboard", methods=["GET"])
    async def dashboard(_: Request) -> PlainTextResponse:
        return _serve_html("dashboard.html")

    @mcp.custom_route("/dashboard/api/executions/by-run/{run_id}", methods=["GET"])
    async def execution_status_api(request: Request) -> PlainTextResponse:
        """Read bounded execution facts without loading unrelated report payloads."""
        run_id = request.path_params["run_id"]
        try:
            if str(uuid.UUID(run_id)) != run_id:
                raise ValueError("noncanonical_uuid")
        except (ValueError, TypeError, AttributeError):
            return PlainTextResponse('{"error":"invalid_run_id"}', status_code=400,
                media_type="application/json", headers={"Cache-Control": "no-store"})
        row = get_execution_by_run_id(run_id)
        if row is None or row.get("run_id") != run_id:
            return PlainTextResponse('{"error":"execution_not_found"}', status_code=404,
                media_type="application/json", headers={"Cache-Control": "no-store"})
        return PlainTextResponse(json.dumps({"execution": {key: row.get(key) for key in
            ("id", "run_id", "tool_name", "exit_code", "timed_out")}}, allow_nan=False),
            media_type="application/json", headers={"Cache-Control": "no-store"})

    @mcp.custom_route("/dashboard/api/executions", methods=["GET"])
    async def dashboard_api(request: Request) -> PlainTextResponse:
        since = int(request.query_params.get("since", "0"))
        analysis_since = int(request.query_params.get("analysis_since", "0"))
        limit = min(int(request.query_params.get("limit", "100")), 500)
        executions = RUNTIME_BUS.dashboard_executions(limit=limit, since_id=since)
        analysis_updates = RUNTIME_BUS.dashboard_analysis_updates(
            limit=limit, since_id=analysis_since, include_images=True
        )
        analyses = RUNTIME_BUS.get_latest_analysis_by_run_ids([
            row["run_id"] for row in executions if isinstance(row.get("run_id"), str)
        ], include_images=True)
        for row in executions:
            if row.get("run_id") in analyses:
                row["analysis"] = {key: value for key, value in analyses[row["run_id"]].items()
                                   if key != "heatmap_base64"}
                if not row.get("image_base64"):
                    row["image_base64"] = analyses[row["run_id"]].get("heatmap_base64")
        if config.SKILL_LIBRARY_ENABLED is True:
            links = RUNTIME_BUS.read_execution_links([
                row["run_id"] for row in executions if isinstance(row.get("run_id"), str)
            ])
            for row in executions:
                if row.get("run_id") in links:
                    row["skill_reference"] = links[row["run_id"]]
        return PlainTextResponse(
            json.dumps({
                "executions": executions,
                "analysis_updates": analysis_updates,
                "stats": RUNTIME_BUS.dashboard_stats(),
            }, ensure_ascii=False, allow_nan=False),
            media_type="application/json",
        )

    @mcp.custom_route("/dashboard/api/executions", methods=["DELETE"])
    async def clear_executions_api(request: Request) -> PlainTextResponse:
        denied = require_admin(request, "clear_executions")
        if denied:
            return denied
        count = RUNTIME_BUS.clear_execution_log()
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
        row = RUNTIME_BUS.get_execution_row(execution_id) if execution_id else None
        if not row or not row.get("code"):
            return PlainTextResponse(
                json.dumps({"ok": False, "error": "记录不存在或没有可执行代码"}, ensure_ascii=False),
                status_code=404,
                media_type="application/json",
            )
        try:
            code = RUNTIME_BUS.execution_cleaner(row["code"])
            result = RUNTIME_BUS.execute_remote(code, 15)
            raw_stdout = result.get("stdout", "") or ""
            result["stdout"] = RUNTIME_BUS.shrink_field_in_stdout(result.get("stdout", ""))
            RUNTIME_BUS.normalize_result_artifacts(result)
            run_id = str(uuid.uuid4())
            result["run_id"] = run_id
            RUNTIME_BUS.execution_record(
                tool_name="dashboard_rerun",
                code=code,
                exit_code=result.get("exit_code"),
                timed_out=result.get("timed_out", False),
                duration_ms=result.get("duration_ms"),
                stdout=result.get("stdout", ""),
                stderr=result.get("stderr", ""),
                image_base64=result.get("image_base64"),
                run_id=run_id,
                execution_evidence=result.get("execution_evidence"),
            )
            # A dashboard rerun is a new execution, without inherited skill authority
            # or parameters. Analyze its own raw output and retain its own receipt.
            analyzed = RUNTIME_BUS.analyze_and_record(raw_stdout, result.get("stderr", "") or "",
                int(result["exit_code"] if result.get("exit_code") is not None else 1),
                "{}", run_id)
            result["analysis"] = {key: analyzed.get(key) for key in
                ("verdict", "quality_level", "max_pressure", "rms_pressure", "field_shape")}
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
            json.dumps(RUNTIME_BUS.get_cache_stats(), ensure_ascii=False),
            media_type="application/json",
        )
