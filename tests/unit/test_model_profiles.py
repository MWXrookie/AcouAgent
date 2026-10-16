import asyncio
import json

import pytest

from app import model_client, model_profiles


def _profiles():
    return json.dumps({
        "fast": {
            "label": "Fast",
            "provider": "openai-compatible",
            "base_url": "https://models.example.test/v1",
            "model": "acoustic-fast",
            "api_key_env": "TEST_MODEL_KEY",
            "capabilities": ["chat", "json", "code"],
            "tasks": {"code_generation": {"temperature": 0.4, "max_tokens": 4096}},
        }
    })


def test_public_profiles_never_expose_secret_or_credential_reference(monkeypatch):
    monkeypatch.setenv("MODEL_PROFILES_JSON", _profiles())
    monkeypatch.setenv("TEST_MODEL_KEY", "unit-test-secret-value")
    public = model_profiles.public_profiles()
    encoded = json.dumps(public)
    assert public["default_profile"] == "fast"
    assert public["profiles"][0]["configured"] is True
    assert "unit-test-secret-value" not in encoded
    assert "TEST_MODEL_KEY" not in encoded
    assert "base_url" not in public["profiles"][0]


def test_profile_rejects_http_without_explicit_server_flag(monkeypatch):
    raw = json.loads(_profiles())
    raw["fast"]["base_url"] = "http://127.0.0.1:11434/v1"
    monkeypatch.setenv("MODEL_PROFILES_JSON", json.dumps(raw))
    with pytest.raises(model_profiles.ModelProfileError, match="base_url"):
        model_profiles.load_profiles()
    raw["fast"]["allow_insecure_http"] = True
    monkeypatch.setenv("MODEL_PROFILES_JSON", json.dumps(raw))
    assert model_profiles.load_profiles()["fast"].endpoint().endswith("/v1/chat/completions")


def test_unknown_profile_fails_before_transport(monkeypatch):
    monkeypatch.setenv("MODEL_PROFILES_JSON", _profiles())
    monkeypatch.setenv("TEST_MODEL_KEY", "unit-test-secret-value")
    with pytest.raises(model_profiles.ModelProfileError, match="unknown"):
        model_profiles.resolve("missing", "code_generation")


def test_model_client_uses_server_profile_and_records_actual_model(monkeypatch):
    monkeypatch.setenv("MODEL_PROFILES_JSON", _profiles())
    monkeypatch.setenv("TEST_MODEL_KEY", "unit-test-secret-value")
    captured = {}

    class Response:
        content = b'{"choices":[{"message":{"content":"done"}}],"usage":{"prompt_tokens":3}}'
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return None
        def raise_for_status(self): pass
        async def aiter_bytes(self):
            yield self.content

    class Client:
        def __init__(self, **kwargs): captured["client"] = kwargs
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return None
        def stream(self, method, url, headers, json):
            captured.update(method=method, url=url, headers=headers, body=json)
            return Response()

    recorded = {}
    monkeypatch.setattr(model_client.httpx, "AsyncClient", Client)
    monkeypatch.setattr(model_client.dashboard, "record_llm_usage",
                        lambda model, usage, cost, run_id=None, metadata=None: recorded.update(
                            model=model, usage=usage, cost=cost, run_id=run_id, metadata=metadata))
    result = asyncio.run(model_client.complete(
        "fast", "code_generation", [{"role": "user", "content": "make code"}], run_id="run-1"))
    assert result["content"] == "done"
    assert result["model"] == "acoustic-fast"
    assert captured["url"] == "https://models.example.test/v1/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer unit-test-secret-value"
    assert recorded["model"] == "openai-compatible:acoustic-fast"
    assert recorded["metadata"] == {
        "profile_id": "fast", "provider": "openai-compatible", "model": "acoustic-fast",
        "task": "code_generation", "temperature": 0.4, "max_tokens": 4096,
        "thinking": False, "timeout_seconds": 120.0,
    }
    assert "base_url" not in recorded["metadata"] and "api_key" not in recorded["metadata"]


def test_model_client_stops_stream_above_response_limit(monkeypatch):
    monkeypatch.setenv("MODEL_PROFILES_JSON", _profiles())
    monkeypatch.setenv("TEST_MODEL_KEY", "unit-test-secret-value")

    class Response:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return None
        def raise_for_status(self): pass
        async def aiter_bytes(self):
            yield b"x" * model_client.MAX_RESPONSE_BYTES
            yield b"x"

    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return None
        def stream(self, *args, **kwargs): return Response()

    monkeypatch.setattr(model_client.httpx, "AsyncClient", Client)
    with pytest.raises(model_client.ModelCallError, match="too_large"):
        asyncio.run(model_client.complete(
            "fast", "code_generation", [{"role": "user", "content": "make code"}]))


def test_sync_model_client_reuses_async_transport(monkeypatch):
    monkeypatch.setenv("MODEL_PROFILES_JSON", _profiles())
    monkeypatch.setenv("TEST_MODEL_KEY", "unit-test-secret-value")
    captured = {}

    async def complete(profile, settings, task, messages, run_id=None):
        captured.update(
            profile=profile,
            settings=settings,
            task=task,
            messages=messages,
            run_id=run_id,
        )
        return {"content": "fixed"}

    monkeypatch.setattr(model_client, "_complete_resolved", complete)

    result = model_client.complete_sync(
        "fast", "retry", [{"role": "user", "content": "repair code"}], run_id="retry-1"
    )

    assert result["content"] == "fixed"
    assert captured["profile"].model == "acoustic-fast"
    assert captured["settings"].max_tokens == 8192
    assert captured["task"] == "retry"
    assert captured["messages"] == [{"role": "user", "content": "repair code"}]
    assert captured["run_id"] == "retry-1"


def test_model_probe_uses_fixed_low_cost_settings(monkeypatch):
    monkeypatch.setenv("MODEL_PROFILES_JSON", _profiles())
    monkeypatch.setenv("TEST_MODEL_KEY", "unit-test-secret-value")
    captured = {}

    async def fake_complete(profile, settings, task, messages, run_id=None):
        captured.update(
            profile=profile,
            settings=settings,
            task=task,
            messages=messages,
            run_id=run_id,
        )
        return {"content": "ok"}

    monkeypatch.setattr(model_client, "_complete_resolved", fake_complete)
    result = asyncio.run(model_client.probe(
        "fast", [{"role": "user", "content": "probe"}], run_id="probe-run"
    ))
    assert result == {"content": "ok"}
    assert captured["profile"].model == "acoustic-fast"
    assert captured["task"] == "preflight"
    assert captured["settings"].temperature == 0
    assert captured["settings"].max_tokens == 160
    assert captured["settings"].thinking is False
    assert captured["settings"].timeout_seconds == 30
    assert captured["run_id"] == "probe-run"


def test_profile_capability_mismatch_fails_before_transport(monkeypatch):
    raw = json.loads(_profiles())
    raw["fast"]["capabilities"] = ["chat", "json"]
    monkeypatch.setenv("MODEL_PROFILES_JSON", json.dumps(raw))
    monkeypatch.setenv("TEST_MODEL_KEY", "unit-test-secret-value")
    with pytest.raises(model_profiles.ModelProfileError, match="capability"):
        model_profiles.resolve("fast", "code_generation")
    assert model_profiles.resolve("fast", "parameter_extraction")[0].profile_id == "fast"


def test_portal_byok_is_ephemeral_resolvable_and_revocable(monkeypatch):
    monkeypatch.delenv("MODEL_PROFILES_JSON", raising=False)
    monkeypatch.delenv("MODEL_BASE_URL", raising=False)
    monkeypatch.delenv("MODEL_NAME", raising=False)
    monkeypatch.delenv("DEFAULT_MODEL_PROFILE", raising=False)
    monkeypatch.setattr(model_profiles, "validate_public_https_url", lambda value: value)

    created = model_profiles.register_session_profile({
        "base_url": "https://models.example.test/v1",
        "model": "user-model",
        "api_key": "user-owned-secret",
    })
    profile_id = created["profile_id"]
    profile, _ = model_profiles.resolve(profile_id, "code_generation")
    assert profile.model == "user-model"
    assert profile.api_key() == "user-owned-secret"
    assert profile_id not in profile.audit_profile_id()
    assert profile.audit_profile_id().startswith("byok-session:")
    assert "user-owned-secret" not in json.dumps(created)
    assert model_profiles.public_profiles()["profiles"] == []
    assert model_profiles.remove_session_profile(profile_id)
    with pytest.raises(model_profiles.ModelProfileError, match="missing_or_expired"):
        model_profiles.resolve(profile_id, "code_generation")


def test_portal_byok_private_endpoint_requires_operator_opt_in(monkeypatch):
    monkeypatch.delenv("BYOK_ALLOW_PRIVATE_ENDPOINTS", raising=False)
    monkeypatch.setattr(
        model_profiles, "validate_public_https_url",
        lambda _value: (_ for _ in ()).throw(model_profiles.UnsafeOutboundUrl("unsafe")),
    )
    payload = {"base_url": "https://private.example/v1", "model": "local-model",
               "api_key": "local-key"}
    with pytest.raises(model_profiles.ModelProfileError, match="endpoint_unsafe"):
        model_profiles.register_session_profile(payload)

    monkeypatch.setenv("BYOK_ALLOW_PRIVATE_ENDPOINTS", "true")
    payload["base_url"] = "http://host.docker.internal:11434/v1"
    created = model_profiles.register_session_profile(payload)
    profile, _ = model_profiles.resolve(created["profile_id"], "retry")
    assert profile.endpoint() == "http://host.docker.internal:11434/v1/chat/completions"
    model_profiles.remove_session_profile(created["profile_id"])


def test_portal_byok_expires_fail_closed(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(model_profiles.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(model_profiles, "validate_public_https_url", lambda value: value)
    created = model_profiles.register_session_profile({
        "base_url": "https://models.example.test/v1",
        "model": "user-model",
        "api_key": "ephemeral-secret",
    })
    clock[0] += model_profiles.byok_session_ttl_seconds() + 1
    with pytest.raises(model_profiles.ModelProfileError, match="missing_or_expired"):
        model_profiles.resolve(created["profile_id"], "result_review")
