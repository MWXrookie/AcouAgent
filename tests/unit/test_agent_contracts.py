import asyncio

import pytest

from app import config
from app.adapters import DifyCoreAdapter, LocalCoreAdapter, WorkflowAdapter, _normalize_workflow_events
from app.contracts import AgentApiError, ChatRequest, WorkflowEvent


def test_local_and_dify_capability_matrices_are_explicit():
    local = LocalCoreAdapter.info()
    dify = DifyCoreAdapter.info()
    assert local.backend_id == "local"
    assert local.capabilities.model_profiles is True
    assert local.capabilities.byok is True
    assert dify.backend_id == "dify"
    assert dify.capabilities.chat is True
    assert dify.capabilities.model_profiles is False


def test_chat_request_enforces_public_size_limits():
    with pytest.raises(ValueError, match="message_too_large"):
        ChatRequest.from_payload({"message": "x" * 65537})
    with pytest.raises(ValueError, match="requirement_too_large"):
        ChatRequest.from_payload({"message": "ok", "requirement": "x" * 65537})
    with pytest.raises(ValueError, match="invalid model_profile"):
        ChatRequest.from_payload({"message": "ok", "model_profile": "x" * 65})


def test_dify_outputs_are_normalized_to_report():
    async def source():
        yield {"event": "result", "outputs": {"text": "dify report"}, "workflow_status": "succeeded"}

    async def run():
        events = [item async for item in _normalize_workflow_events(source())]
        assert len(events) == 1
        assert events[0].event == "result"
        assert events[0].data["report"] == "dify report"
        assert "outputs" not in events[0].data

    asyncio.run(run())


def test_invalid_workflow_event_name_fails_closed():
    async def source():
        yield {"event": "done"}

    async def run():
        with pytest.raises(ValueError, match="workflow_event_invalid_name"):
            [item async for item in _normalize_workflow_events(source())]

    asyncio.run(run())


def test_unknown_backend_returns_safe_capability_error(monkeypatch):
    monkeypatch.setattr(config, "ORCHESTRATION_BACKEND", "remote-undocumented")
    with pytest.raises(AgentApiError) as exc:
        WorkflowAdapter.info()
    assert exc.value.code == "backend_unavailable"
    assert exc.value.status_code == 503
