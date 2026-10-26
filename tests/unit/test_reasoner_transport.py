"""Keep reasoning separate, bounded and audited when no final answer arrives."""
import asyncio
import json

import pytest

from app import model_client, model_profiles


def configure(monkeypatch, base="https://api.deepseek.com", model="deepseek-reasoner", tasks=None):
    document = {"test": dict(base_url=base, model=model, api_key_env="UNIT_MODEL_KEY",
        tasks=tasks or {}, capabilities=["chat", "json", "code"])}
    monkeypatch.setenv("MODEL_PROFILES_JSON", json.dumps(document))
    monkeypatch.setenv("UNIT_MODEL_KEY", "unit-test-secret")


def transport(monkeypatch, payload):
    calls, records = [], []
    class Response:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def raise_for_status(self): pass
        async def aiter_bytes(self): yield json.dumps(payload).encode()
    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def stream(self, *args, **kwargs): calls.append(kwargs["json"]); return Response()
    monkeypatch.setattr(model_client.httpx, "AsyncClient", Client)
    monkeypatch.setattr(model_client.dashboard, "record_llm_usage", lambda *a, **k: records.append((a, k)))
    return calls, records


@pytest.mark.parametrize("base,model,override,thinking,tokens", [
    ("https://api.deepseek.com", "deepseek-reasoner", {}, {"type": "enabled"}, 4096),
    ("https://api.deepseek.com", "deepseek-chat", {}, {"type": "disabled"}, 800),
    ("https://api.deepseek.com.evil.test", "deepseek-reasoner", {}, None, 800),
    ("https://models.example.test", "deepseek-reasoner", {"result_review": {"thinking": True}}, True, 800),
])
def test_official_thinking_protocol_does_not_change_other_providers(monkeypatch, base, model, override, thinking, tokens):
    configure(monkeypatch, base, model, override)
    calls, records = transport(monkeypatch, {"choices": [{"finish_reason": "stop",
        "message": {"content": "safe answer", "reasoning_content": "private reasoning"}}],
        "usage": {"completion_tokens": 12}})
    result = asyncio.run(model_client.complete("test", "result_review", [{"role": "user", "content": "request"}]))
    assert calls[0].get("thinking") == thinking and calls[0]["max_tokens"] == tokens
    assert result["content"] == "safe answer" and len(records) == 1
    assert "private reasoning" not in json.dumps(records)


def test_explicit_reasoner_limits_remain_authoritative(monkeypatch):
    configure(monkeypatch, tasks={"result_review": {"max_tokens": 96, "thinking": False}})
    _, settings = model_profiles.resolve("test", "result_review")
    assert settings.max_tokens == 96 and settings.thinking is False


def test_session_reasoner_uses_same_bounded_defaults(monkeypatch):
    monkeypatch.setattr(model_profiles, "validate_public_https_url", lambda url: url)
    v = model_profiles.register_session_profile(dict(base_url="https://api.deepseek.com",
        model="deepseek-reasoner", api_key="unit-test-secret"))
    try:
        _, settings = model_profiles.resolve(v["profile_id"], "result_review")
        assert settings.max_tokens == 4096 and settings.thinking is True
    finally:
        model_profiles.remove_session_profile(v["profile_id"])


@pytest.mark.parametrize("finish,content,error", [
    ("length", "print('truncated but syntactically valid')", "model_output_truncated"),
    ("stop", "", "model_response_empty"),
])
def test_paid_empty_or_truncated_response_is_recorded_without_reasoning(monkeypatch, finish, content, error):
    configure(monkeypatch)
    calls, records = transport(monkeypatch, {"choices": [{"finish_reason": finish,
        "message": {"content": content, "reasoning_content": "private reasoning"}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 800}})
    with pytest.raises(model_client.ModelCallError, match=error):
        asyncio.run(model_client.complete("test", "result_review", [{"role": "user", "content": "private request"}]))
    assert len(calls) == len(records) == 1
    assert records[0][0][1]["completion_tokens"] == 800
    assert records[0][1]["metadata"]["response_error"] == error
    assert all(x not in json.dumps(records) for x in ("private reasoning", "private request", "unit-test-secret"))
