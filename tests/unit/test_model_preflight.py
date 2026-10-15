import asyncio
import json

import pytest

from app import model_preflight


def test_preflight_accepts_strict_json_and_never_returns_response_body(monkeypatch):
    async def fake_probe(profile_id, messages, run_id=None):
        assert profile_id == "profile-a"
        assert run_id
        assert len(messages) == 1
        return {
            "content": '{"status":"ok","code":"print(\'acouagent\')"}',
            "profile_id": "profile-a",
            "provider": "openai-compatible",
            "model": "model-a",
        }

    monkeypatch.setattr(model_preflight.model_client, "probe", fake_probe)
    result = asyncio.run(model_preflight.run("profile-a"))
    assert result["ok"] is True
    assert result["status"] == "basic_compatible"
    assert result["full_simulation_validated"] is False
    assert all(item["passed"] for item in result["checks"])
    encoded = json.dumps(result)
    assert "print(" not in encoded
    assert '"content"' not in encoded


@pytest.mark.parametrize(
    ("content", "json_ok", "code_ok"),
    [
        ("not-json", False, False),
        ('{"status":"ok","code":"import os"}', True, False),
        ('{"status":"wrong","code":"print(\'acouagent\')"}', False, False),
        ('{"status":"ok","code":"print(\'acouagent\')","extra":1}', False, False),
    ],
)
def test_preflight_fails_closed_for_invalid_shapes(monkeypatch, content, json_ok, code_ok):
    async def fake_probe(*_args, **_kwargs):
        return {
            "content": content,
            "profile_id": "profile-a",
            "provider": "openai-compatible",
            "model": "model-a",
        }

    monkeypatch.setattr(model_preflight.model_client, "probe", fake_probe)
    result = asyncio.run(model_preflight.run("profile-a"))
    checks = {item["id"]: item["passed"] for item in result["checks"]}
    assert result["ok"] is False
    assert result["connected"] is True
    assert checks["strict_json"] is json_ok
    assert checks["code_shape"] is code_ok


def test_preflight_rejects_parallel_paid_probe():
    assert model_preflight._LOCK.acquire(blocking=False)
    try:
        with pytest.raises(model_preflight.ModelPreflightBusy):
            asyncio.run(model_preflight.run("profile-a"))
    finally:
        model_preflight._LOCK.release()
