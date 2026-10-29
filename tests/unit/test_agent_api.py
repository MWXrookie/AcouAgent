import asyncio
import json

import httpx
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.routing import Route

from app import agent_api, config
from app.contracts import BackendCapabilities, BackendInfo, WorkflowEvent
from app.runtime_bus import RUNTIME_BUS


class Registry:
    def __init__(self):
        self.routes = []

    def custom_route(self, path, methods):
        def decorate(fn):
            self.routes.append(Route(path, fn, methods=methods))
            return fn
        return decorate


def _app(cors_origins=None):
    registry = Registry()
    agent_api.register(registry)
    app = Starlette(routes=registry.routes)
    if cors_origins is not None:
        app = CORSMiddleware(app, allow_origins=cors_origins, allow_methods=["GET", "POST", "DELETE", "OPTIONS"], allow_headers=["Accept", "Content-Type"])
    return app


def test_agent_info_exposes_backend_capabilities(monkeypatch):
    info = BackendInfo(
        backend_id="local",
        backend_label="本地编排",
        capabilities=BackendCapabilities(model_profiles=True, byok=True, model_preflight=True),
    )
    monkeypatch.setattr(RUNTIME_BUS, "agent_info", lambda: info)

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=_app()), base_url="http://isolated") as client:
            response = await client.get("/api/v1/agent/info")
            assert response.status_code == 200
            payload = response.json()
            assert payload["ok"] is True
            assert payload["data"]["backend_id"] == "local"
            assert payload["data"]["capabilities"]["byok"] is True

    asyncio.run(run())


def test_agent_chat_stream_is_ndjson_with_terminal_result(monkeypatch):
    async def fake_events(request):
        yield WorkflowEvent("progress", {"stage": "本地编排：生成代码", "request_id": "req-1"})
        yield WorkflowEvent("result", {"report": "ok", "workflow_status": "succeeded", "request_id": "req-1", "skill_usage": []})

    monkeypatch.setattr(RUNTIME_BUS, "agent_info", lambda: BackendInfo("local", "本地编排", BackendCapabilities()))
    monkeypatch.setattr(RUNTIME_BUS, "chat_events", fake_events)

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=_app()), base_url="http://isolated") as client:
            response = await client.post("/api/v1/agent/chat", json={"message": "test", "stream": True})
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("application/x-ndjson")
            events = [json.loads(line) for line in response.text.splitlines() if line]
            assert events[0]["event"] == "progress"
            assert events[-1]["event"] == "result"
            assert events[-1]["report"] == "ok"

    asyncio.run(run())


def test_agent_chat_business_failure_remains_http_200(monkeypatch):
    async def fake_events(request):
        yield WorkflowEvent("result", {"report": "parameter invalid", "workflow_status": "error", "request_id": "req-2", "skill_usage": []})

    monkeypatch.setattr(RUNTIME_BUS, "agent_info", lambda: BackendInfo("local", "本地编排", BackendCapabilities()))
    monkeypatch.setattr(RUNTIME_BUS, "chat_events", fake_events)

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=_app()), base_url="http://isolated") as client:
            response = await client.post("/api/v1/agent/chat", json={"message": "test"})
            assert response.status_code == 200
            assert response.json()["ok"] is True
            assert response.json()["data"]["workflow_status"] == "error"

    asyncio.run(run())


def test_agent_chat_invalid_and_oversized_requests_are_safe_envelopes(monkeypatch):
    monkeypatch.setattr(RUNTIME_BUS, "agent_info", lambda: BackendInfo("local", "本地编排", BackendCapabilities()))

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=_app()), base_url="http://isolated") as client:
            bad = await client.post("/api/v1/agent/chat", content=b"{bad", headers={"Content-Type": "application/json"})
            assert bad.status_code == 400
            assert bad.json()["ok"] is False
            assert bad.json()["error"]["code"] == "invalid_request"
            large = await client.post("/api/v1/agent/chat", content=b"{" + b"x" * (128 * 1024) + b"}")
            assert large.status_code == 413
            assert large.json()["error"]["code"] == "request_too_large"

    asyncio.run(run())


def test_dify_model_capability_is_rejected_as_501(monkeypatch):
    monkeypatch.setattr(config, "ORCHESTRATION_BACKEND", "dify")

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=_app()), base_url="http://isolated") as client:
            response = await client.get("/api/v1/agent/model-profiles")
            assert response.status_code == 501
            assert response.json()["error"]["code"] == "capability_not_supported"

    asyncio.run(run())


def test_agent_model_session_envelope_and_revoke(monkeypatch):
    monkeypatch.setattr(RUNTIME_BUS, "agent_info", lambda: BackendInfo("local", "本地编排", BackendCapabilities(model_profiles=True, byok=True)))
    monkeypatch.setattr(RUNTIME_BUS, "create_model_session", lambda body: {"profile_id": "byok-test", "model": "m", "label": "L", "expires_in_seconds": 10})
    monkeypatch.setattr(RUNTIME_BUS, "remove_model_session", lambda profile_id: profile_id == "byok-test")

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=_app()), base_url="http://isolated") as client:
            created = await client.post("/api/v1/agent/model-sessions", json={"base_url": "https://example.test/v1", "model": "m", "api_key": "secret"})
            assert created.status_code == 201
            assert created.json()["ok"] is True
            assert created.json()["data"]["profile_id"] == "byok-test"
            removed = await client.delete("/api/v1/agent/model-sessions/byok-test")
            assert removed.status_code == 200
            assert removed.json()["data"]["removed"] is True

    asyncio.run(run())


def test_openapi_documents_the_canonical_agent_paths():
    spec = json.loads((__import__("pathlib").Path(__file__).resolve().parents[2] / "docs" / "openapi.agent.v1.json").read_text(encoding="utf-8"))
    assert spec["openapi"].startswith("3.1")
    assert spec["servers"][0]["url"] == "/api/v1/agent"
    assert set(spec["paths"]) >= {
        "/info",
        "/chat",
        "/model-profiles",
        "/model-sessions",
        "/model-sessions/{profile_id}",
        "/model-profiles/{profile_id}/test",
    }


def test_cors_whitelist_allows_only_configured_origin():
    async def run():
        app = _app(["https://frontend.example"])
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://isolated") as client:
            allowed = await client.options(
                "/api/v1/agent/info",
                headers={"Origin": "https://frontend.example", "Access-Control-Request-Method": "GET"},
            )
            denied = await client.options(
                "/api/v1/agent/info",
                headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "GET"},
            )
            assert allowed.status_code == 200
            assert allowed.headers["access-control-allow-origin"] == "https://frontend.example"
            assert "access-control-allow-origin" not in denied.headers

    asyncio.run(run())
