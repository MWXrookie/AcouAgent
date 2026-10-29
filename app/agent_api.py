"""Versioned agent HTTP API and legacy route compatibility layer.

The API layer owns browser-facing request validation and response envelopes. It
does not import local workflow, executor, database, or skill implementation
modules; those remain behind ``RuntimeBus`` and the selected backend adapter.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

from starlette.requests import Request
from starlette.responses import PlainTextResponse, StreamingResponse

from app import config, model_client, model_profiles
from app.contracts import AgentApiError, ChatRequest
from app.runtime_bus import RUNTIME_BUS

MAX_REQUEST_BYTES = 128 * 1024
MAX_MODEL_BODY_BYTES = 8192
AGENT_PREFIX = "/api/v1/agent"


def _request_id() -> str:
    return str(uuid.uuid4())


def _headers(*, no_store: bool = True, legacy: bool = False, successor: str = "") -> dict[str, str]:
    headers: dict[str, str] = {}
    if no_store:
        headers["Cache-Control"] = "no-store"
    if legacy:
        headers["Deprecation"] = "true"
        if successor:
            headers["Link"] = f'<{successor}>; rel="successor-version"'
    return headers


def _json(payload: Any, *, status_code: int = 200, headers: dict[str, str] | None = None) -> PlainTextResponse:
    try:
        body = json.dumps(payload, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError):
        body = json.dumps(
            {
                "ok": False,
                "error": {
                    "code": "serialization_error",
                    "message": "服务返回结果无法安全序列化。",
                    "request_id": _request_id(),
                },
            },
            ensure_ascii=False,
        )
        status_code = 500
    return PlainTextResponse(
        body,
        status_code=status_code,
        media_type="application/json",
        headers=headers or _headers(),
    )


def _ok(data: Any, *, headers: dict[str, str] | None = None) -> PlainTextResponse:
    return _json({"ok": True, "data": data}, headers=headers)


def _error(
    code: str,
    message: str,
    *,
    status_code: int,
    request_id: str | None = None,
    details: Any = None,
    legacy: bool = False,
    successor: str = "",
) -> PlainTextResponse:
    error: dict[str, Any] = {
        "code": code,
        "message": message,
        "request_id": request_id or _request_id(),
    }
    if details is not None:
        error["details"] = details
    return _json(
        {"ok": False, "error": error},
        status_code=status_code,
        headers=_headers(legacy=legacy, successor=successor),
    )


def _from_api_error(exc: AgentApiError, *, legacy: bool = False, successor: str = "") -> PlainTextResponse:
    if legacy:
        message = "invalid_request" if exc.code == "invalid_request" and exc.message != "message is required" else exc.message
        return _json(
            {"error": message},
            status_code=exc.status_code,
            headers=_headers(legacy=True, successor=successor),
        )
    return _error(
        exc.code,
        exc.message,
        status_code=exc.status_code,
        request_id=exc.request_id,
        details=exc.details,
        legacy=False,
        successor=successor,
    )


async def _read_json(request: Request, *, max_bytes: int = MAX_REQUEST_BYTES) -> Any:
    raw = await request.body()
    if len(raw) > max_bytes:
        raise AgentApiError(
            "request_too_large",
            "请求体超过允许大小。",
            status_code=413,
        )
    try:
        value = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError, TypeError):
        raise AgentApiError("invalid_request", "请求 JSON 无效。", status_code=400) from None
    if not isinstance(value, dict):
        raise AgentApiError("invalid_request", "请求体必须是 JSON 对象。", status_code=400)
    return value


def _chat_contract(payload: dict[str, Any]) -> ChatRequest:
    try:
        return ChatRequest.from_payload(payload)
    except ValueError as exc:
        message = str(exc)
        raise AgentApiError("invalid_request", message, status_code=400) from None


async def _chat_events(request: ChatRequest) -> AsyncIterator[dict[str, Any]]:
    """Merge the turn and yield normalized events with a terminal result."""
    if request.requirement:
        full_requirement = await RUNTIME_BUS.merge_requirement(
            request.requirement,
            request.message,
            request.model_profile,
        )
        merge_used = True
    else:
        full_requirement = request.message
        merge_used = False
    full_request = ChatRequest(
        message=request.message,
        requirement=full_requirement,
        session_id=request.session_id,
        model_profile=request.model_profile,
        stream=request.stream,
    )
    async for event in RUNTIME_BUS.chat_events(full_request):
        item = event.to_dict()
        if event.event == "result":
            item.update(
                requirement=full_requirement,
                merge_used=merge_used,
                orchestration_backend=item.get(
                    "orchestration_backend",
                    config.ORCHESTRATION_BACKEND,
                ),
            )
        yield item


async def _ndjson(events: AsyncIterator[dict[str, Any]]) -> AsyncIterator[str]:
    """Serialize events as NDJSON and keep errors safe after the response starts."""
    saw_result = False
    try:
        async for item in events:
            if item.get("event") == "result":
                saw_result = True
            yield json.dumps(item, ensure_ascii=False, allow_nan=False) + "\n"
    except (AgentApiError, ValueError, TypeError) as exc:
        request_id = getattr(exc, "request_id", None) or _request_id()
        code = getattr(exc, "code", "workflow_event_invalid")
        message = getattr(exc, "message", None) or "工作流事件无效。"
        if not saw_result:
            yield json.dumps(
                {
                    "event": "result",
                    "workflow_status": "error",
                    "report": message,
                    "error_code": code,
                    "request_id": request_id,
                    "skill_usage": [],
                },
                ensure_ascii=False,
            ) + "\n"
    if not saw_result:
        yield json.dumps(
            {
                "event": "result",
                "workflow_status": "error",
                "report": "连接提前结束，未收到完整结果。",
                "error_code": "workflow_incomplete",
                "request_id": _request_id(),
                "skill_usage": [],
            },
            ensure_ascii=False,
        ) + "\n"


def _legacy_events(events: AsyncIterator[dict[str, Any]]) -> AsyncIterator[dict[str, Any]]:
    """Preserve legacy bare event shapes while adding no v1 envelope."""
    return events


async def _first_result(events: AsyncIterator[dict[str, Any]]) -> dict[str, Any]:
    async for item in events:
        if item.get("event") == "result":
            return item
    raise AgentApiError(
        "workflow_incomplete",
        "连接提前结束，未收到完整结果。",
        status_code=502,
    )


def _legacy_deprecation(successor: str) -> dict[str, str]:
    return _headers(legacy=True, successor=successor)


async def _chat_response(
    request: Request,
    *,
    legacy: bool,
    successor: str,
) -> PlainTextResponse | StreamingResponse:
    try:
        payload = await _read_json(request)
        contract = _chat_contract(payload)
        # Resolve the selected backend before starting a streaming response so
        # a missing/unknown backend can return a real HTTP error.
        RUNTIME_BUS.agent_info()
        events = _chat_events(contract)
        if contract.stream:
            return StreamingResponse(
                _ndjson(events),
                media_type="application/x-ndjson",
                headers=_headers(
                    legacy=legacy,
                    successor=successor,
                ) | {"X-Accel-Buffering": "no"},
            )
        result = await _first_result(events)
        if legacy:
            return _json(result, headers=_headers(legacy=True, successor=successor))
        return _ok(result)
    except AgentApiError as exc:
        return _from_api_error(exc, legacy=legacy, successor=successor)
    except (model_profiles.ModelProfileError, model_client.ModelCallError) as exc:
        if legacy:
            return _error(
                "model_unavailable",
                str(exc),
                status_code=400,
                legacy=True,
                successor=successor,
            )
        return _from_api_error(
            AgentApiError("model_unavailable", str(exc), status_code=400),
            legacy=False,
        )


def _model_error(exc: Exception, *, legacy: bool, successor: str = "") -> PlainTextResponse:
    if isinstance(exc, AgentApiError):
        return _from_api_error(exc, legacy=legacy, successor=successor)
    if isinstance(exc, (model_profiles.ModelProfileError, ValueError)):
        return _error(
            str(exc) or "model_request_invalid",
            str(exc) or "模型配置请求无效。",
            status_code=400,
            legacy=legacy,
            successor=successor,
        )
    return _error(
        "model_backend_error",
        "模型服务请求失败。",
        status_code=502,
        legacy=legacy,
        successor=successor,
    )


def _legacy_model_error(exc: Exception, successor: str) -> PlainTextResponse:
    if isinstance(exc, AgentApiError):
        status_code = exc.status_code
    elif isinstance(exc, (model_profiles.ModelProfileError, ValueError)):
        status_code = 400
    elif isinstance(exc, model_client.ModelCallError):
        status_code = 502
    else:
        status_code = 502
    return _json(
        {"ok": False, "error": str(exc) or "model_backend_error"},
        status_code=status_code,
        headers=_headers(legacy=True, successor=successor),
    )


def register(mcp) -> None:
    """Register canonical v1 routes and compatibility aliases."""

    @mcp.custom_route(AGENT_PREFIX + "/info", methods=["GET"])
    async def agent_info(_: Request) -> PlainTextResponse:
        try:
            return _ok(RUNTIME_BUS.agent_info().to_dict())
        except AgentApiError as exc:
            return _from_api_error(exc)

    @mcp.custom_route(AGENT_PREFIX + "/chat", methods=["POST"])
    async def agent_chat(request: Request) -> PlainTextResponse | StreamingResponse:
        return await _chat_response(request, legacy=False, successor="")

    @mcp.custom_route(AGENT_PREFIX + "/model-profiles", methods=["GET"])
    async def agent_model_profiles(_: Request) -> PlainTextResponse:
        try:
            return _ok(RUNTIME_BUS.model_profiles())
        except Exception as exc:
            return _model_error(exc, legacy=False)

    @mcp.custom_route(AGENT_PREFIX + "/model-sessions", methods=["POST"])
    async def agent_create_model_session(request: Request) -> PlainTextResponse:
        try:
            body = await _read_json(request, max_bytes=MAX_MODEL_BODY_BYTES)
            created = RUNTIME_BUS.create_model_session(body)
            return _json({"ok": True, "data": created}, status_code=201)
        except Exception as exc:
            return _model_error(exc, legacy=False)

    @mcp.custom_route(AGENT_PREFIX + "/model-sessions/{profile_id}", methods=["DELETE"])
    async def agent_delete_model_session(request: Request) -> PlainTextResponse:
        try:
            profile_id = request.path_params["profile_id"]
            removed = RUNTIME_BUS.remove_model_session(profile_id)
            if not removed:
                return _error(
                    "model_session_not_found",
                    "模型会话不存在或已过期。",
                    status_code=404,
                )
            return _ok({"removed": True, "profile_id": profile_id})
        except Exception as exc:
            return _model_error(exc, legacy=False)

    @mcp.custom_route(AGENT_PREFIX + "/model-profiles/{profile_id}/test", methods=["POST"])
    async def agent_test_model_profile(request: Request) -> PlainTextResponse:
        try:
            profile_id = request.path_params["profile_id"]
            result = await RUNTIME_BUS.test_model_profile(profile_id)
            return _ok(result)
        except Exception as exc:
            return _model_error(exc, legacy=False)

    # Legacy aliases. They intentionally retain the pre-v1 response shapes.
    @mcp.custom_route("/chat", methods=["POST"])
    async def legacy_chat(request: Request) -> PlainTextResponse | StreamingResponse:
        return await _chat_response(request, legacy=True, successor=AGENT_PREFIX + "/chat")

    @mcp.custom_route("/api/v1/chat", methods=["POST"])
    async def legacy_v1_chat(request: Request) -> PlainTextResponse | StreamingResponse:
        return await _chat_response(request, legacy=True, successor=AGENT_PREFIX + "/chat")

    @mcp.custom_route("/api/model-profiles", methods=["GET"])
    async def legacy_model_profiles(_: Request) -> PlainTextResponse:
        try:
            payload = RUNTIME_BUS.model_profiles()
            payload["orchestration_backend"] = config.ORCHESTRATION_BACKEND
            return _json(
                payload,
                headers=_legacy_deprecation(AGENT_PREFIX + "/model-profiles"),
            )
        except Exception as exc:
            return _legacy_model_error(exc, AGENT_PREFIX + "/model-profiles")

    @mcp.custom_route("/api/byok/session", methods=["POST"])
    async def legacy_create_model_session(request: Request) -> PlainTextResponse:
        try:
            body = await _read_json(request, max_bytes=MAX_MODEL_BODY_BYTES)
            created = RUNTIME_BUS.create_model_session(body)
            return _json(
                {"ok": True, **created},
                status_code=201,
                headers=_legacy_deprecation(AGENT_PREFIX + "/model-sessions"),
            )
        except Exception as exc:
            return _legacy_model_error(exc, AGENT_PREFIX + "/model-sessions")

    @mcp.custom_route("/api/byok/session", methods=["DELETE"])
    async def legacy_delete_model_session(request: Request) -> PlainTextResponse:
        try:
            body = await _read_json(request, max_bytes=MAX_MODEL_BODY_BYTES)
            profile_id = body.get("profile_id")
            removed = RUNTIME_BUS.remove_model_session(profile_id)
            return _json(
                {"ok": removed, "error": None if removed else "byok_session_unknown"},
                status_code=200 if removed else 404,
                headers=_legacy_deprecation(AGENT_PREFIX + "/model-sessions"),
            )
        except Exception as exc:
            return _legacy_model_error(exc, AGENT_PREFIX + "/model-sessions")

    @mcp.custom_route("/api/model-profiles/test", methods=["POST"])
    async def legacy_test_model_profile(request: Request) -> PlainTextResponse:
        try:
            body = await _read_json(request, max_bytes=MAX_MODEL_BODY_BYTES)
            profile_id = body.get("profile_id")
            if not isinstance(profile_id, str) or not profile_id:
                return _error(
                    "invalid_model_profile",
                    "profile_id 必须是非空字符串。",
                    status_code=400,
                    legacy=True,
                    successor=AGENT_PREFIX + "/model-profiles",
                )
            result = await RUNTIME_BUS.test_model_profile(profile_id)
            return _json(
                result,
                headers=_legacy_deprecation(AGENT_PREFIX + "/model-profiles"),
            )
        except model_client.ModelCallError as exc:
            return _json(
                {
                    "ok": False,
                    "connected": False,
                    "error": str(exc),
                    "full_simulation_validated": False,
                },
                status_code=502,
                headers=_headers(legacy=True, successor=AGENT_PREFIX + "/model-profiles"),
            )
        except Exception as exc:
            return _legacy_model_error(exc, AGENT_PREFIX + "/model-profiles")


__all__ = ["register"]
