import asyncio
import json

import httpx
from starlette.applications import Starlette
from starlette.routing import Route

from app import config, local_workflow, model_profiles, portal


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
    portal.register(registry)
    return Starlette(routes=registry.routes)


def _profile_document():
    return json.dumps({
        "fast": {
            "label": "Fast",
            "provider": "openai-compatible",
            "base_url": "https://models.example.test/v1",
            "model": "acoustic-fast",
            "api_key_env": "TEST_MODEL_KEY",
            "capabilities": ["chat", "json", "code"],
        }
    })


def test_model_profiles_route_exposes_selection_without_server_secrets(monkeypatch):
    monkeypatch.setenv("MODEL_PROFILES_JSON", _profile_document())
    monkeypatch.setenv("TEST_MODEL_KEY", "server-secret")
    monkeypatch.setattr(config, "ORCHESTRATION_BACKEND", "local")

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=_app()),
                                     base_url="http://isolated") as client:
            response = await client.get("/api/model-profiles")
            assert response.status_code == 200
            payload = response.json()
            assert payload["orchestration_backend"] == "local"
            assert payload["profiles"][0]["id"] == "fast"
            encoded = response.text
            assert "server-secret" not in encoded
            assert "TEST_MODEL_KEY" not in encoded
            assert "models.example.test" not in encoded

    asyncio.run(run())


def test_chat_dispatches_local_backend_and_rejects_non_string_profile(monkeypatch):
    monkeypatch.setattr(config, "ORCHESTRATION_BACKEND", "local")
    calls = []

    async def fake_events(query, user, profile_id):
        calls.append((query, user, profile_id))
        yield {"event": "result", "report": "ok", "workflow_status": "succeeded",
               "orchestration_backend": "local"}

    monkeypatch.setattr(local_workflow, "workflow_events", fake_events)

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=_app()),
                                     base_url="http://isolated") as client:
            bad = await client.post("/chat", json={"message": "test", "model_profile": {"id": "x"}})
            assert bad.status_code == 400
            response = await client.post("/chat", json={
                "message": "simulate", "session_id": "s1", "model_profile": "fast"})
            assert response.status_code == 200
            payload = response.json()
            assert payload["workflow_status"] == "succeeded"
            assert payload["orchestration_backend"] == "local"

    asyncio.run(run())
    assert calls == [("simulate", "portal-s1", "fast")]


def test_byok_session_routes_never_return_or_persist_secret(monkeypatch):
    monkeypatch.delenv("MODEL_PROFILES_JSON", raising=False)
    monkeypatch.delenv("MODEL_BASE_URL", raising=False)
    monkeypatch.delenv("MODEL_NAME", raising=False)
    monkeypatch.delenv("DEFAULT_MODEL_PROFILE", raising=False)
    monkeypatch.setattr(model_profiles, "validate_public_https_url", lambda value: value)

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=_app()),
                                     base_url="http://isolated") as client:
            response = await client.post("/api/byok/session", json={
                "base_url": "https://models.example.test/v1",
                "model": "user-model",
                "api_key": "browser-secret",
            })
            assert response.status_code == 201
            payload = response.json()
            assert "browser-secret" not in response.text
            profile_id = payload["profile_id"]
            profile, _ = model_profiles.resolve(profile_id, "parameter_extraction")
            assert profile.api_key() == "browser-secret"
            removed = await client.request("DELETE", "/api/byok/session",
                                           json={"profile_id": profile_id})
            assert removed.status_code == 200 and removed.json()["ok"] is True

    asyncio.run(run())
