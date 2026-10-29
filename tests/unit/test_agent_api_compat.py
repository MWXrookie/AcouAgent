import asyncio
import json

import httpx
from starlette.applications import Starlette
from starlette.routing import Route

from app import agent_api
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


def _app():
    registry = Registry()
    agent_api.register(registry)
    return Starlette(routes=registry.routes)


def test_legacy_chat_keeps_bare_result_and_deprecation_header(monkeypatch):
    async def fake_events(request):
        yield WorkflowEvent("result", {"report": "legacy", "workflow_status": "succeeded", "skill_usage": []})

    monkeypatch.setattr(RUNTIME_BUS, "agent_info", lambda: BackendInfo("local", "本地编排", BackendCapabilities()))
    monkeypatch.setattr(RUNTIME_BUS, "chat_events", fake_events)

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=_app()), base_url="http://isolated") as client:
            for path in ("/chat", "/api/v1/chat"):
                response = await client.post(path, json={"message": "test"})
                assert response.status_code == 200
                assert response.json()["report"] == "legacy"
                assert "ok" not in response.json()
                assert response.headers["deprecation"] == "true"
                assert "/api/v1/agent/chat" in response.headers["link"]

    asyncio.run(run())


def test_legacy_malformed_chat_keeps_old_error_shape():
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=_app()), base_url="http://isolated") as client:
            response = await client.post("/chat", content=b"{bad", headers={"Content-Type": "application/json"})
            assert response.status_code == 400
            assert response.json() == {"error": "invalid_request"}

    asyncio.run(run())


def test_legacy_model_profiles_keeps_bare_shape_and_successor_link(monkeypatch):
    monkeypatch.setattr(
        RUNTIME_BUS,
        "model_profiles",
        lambda: {"profiles": [], "default_profile": None, "byok": {"enabled": True}},
    )
    monkeypatch.setattr("app.agent_api.config.ORCHESTRATION_BACKEND", "local")

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=_app()), base_url="http://isolated") as client:
            response = await client.get("/api/model-profiles")
            assert response.status_code == 200
            payload = response.json()
            assert payload["orchestration_backend"] == "local"
            assert "ok" not in payload
            assert response.headers["deprecation"] == "true"
            assert "/api/v1/agent/model-profiles" in response.headers["link"]

    asyncio.run(run())


def test_legacy_preflight_transport_error_keeps_old_sanitized_shape(monkeypatch):
    from app import model_client

    async def fail(_profile_id):
        raise model_client.ModelCallError("model_transport_failed")

    monkeypatch.setattr(RUNTIME_BUS, "test_model_profile", fail)

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=_app()), base_url="http://isolated") as client:
            response = await client.post("/api/model-profiles/test", json={"profile_id": "fast"})
            assert response.status_code == 502
            assert response.json() == {
                "ok": False,
                "connected": False,
                "error": "model_transport_failed",
                "full_simulation_validated": False,
            }

    asyncio.run(run())
