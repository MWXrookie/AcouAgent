import asyncio

import pytest

from app import config
from app.adapters import (
    DifyCoreAdapter,
    JwaveFlowCoreAdapter,
    LocalCoreAdapter,
    WorkflowAdapter,
    _normalize_workflow_events,
)
from app.contracts import AgentApiError, ChatRequest, WorkflowEvent


def test_local_and_dify_capability_matrices_are_explicit():
    local = LocalCoreAdapter.info()
    dify = DifyCoreAdapter.info()
    assert local.backend_id == "local"
    assert local.capabilities.model_profiles is True
    assert local.capabilities.byok is True
    assert local.capabilities.model_config_mode == "managed"
    assert dify.backend_id == "dify"
    assert dify.capabilities.chat is True
    assert dify.capabilities.model_profiles is False
    assert dify.capabilities.model_config_mode == "self"


def test_jwave_flow_uses_managed_model_config_by_default(monkeypatch):
    info = JwaveFlowCoreAdapter.info()
    assert info.backend_id == "jwave_flow"
    assert info.capabilities.chat is True
    assert info.capabilities.streaming is True
    assert info.capabilities.model_profiles is True
    assert info.capabilities.byok is True
    assert info.capabilities.model_preflight is True
    assert info.capabilities.skill_usage is True
    assert info.capabilities.model_config_mode == "managed"

    monkeypatch.setattr(config, "ORCHESTRATION_BACKEND", "jwave_flow")
    assert WorkflowAdapter.info().backend_id == "jwave_flow"


def test_jwave_flow_can_select_self_managed_model_config(monkeypatch):
    monkeypatch.setattr(config, "MODEL_CONFIG_MODE", "self")
    info = JwaveFlowCoreAdapter.info()
    assert info.capabilities.model_profiles is False
    assert info.capabilities.byok is False
    assert info.capabilities.model_config_mode == "self"


def test_jwave_flow_configuration_requires_an_exact_service_url(monkeypatch):
    monkeypatch.setattr(config, "MCP_AUTH_TOKEN", "a" * 32)
    monkeypatch.setattr(config, "EXECUTOR_SHARED_TOKEN", "b" * 32)
    monkeypatch.setattr(config, "ORCHESTRATION_BACKEND", "jwave_flow")
    monkeypatch.setattr(config, "JWAVE_FLOW_BASE_URL", "")
    with pytest.raises(RuntimeError, match="JWAVE_FLOW_BASE_URL is required"):
        config.validate()

    monkeypatch.setattr(config, "JWAVE_FLOW_BASE_URL", "http://bridge.test")
    monkeypatch.setattr(config, "JWAVE_FLOW_BRIDGE_TOKEN", "short")
    with pytest.raises(RuntimeError, match="JWAVE_FLOW_BRIDGE_TOKEN"):
        config.validate()


def test_local_core_rejects_self_managed_model_mode(monkeypatch):
    monkeypatch.setattr(config, "MCP_AUTH_TOKEN", "a" * 32)
    monkeypatch.setattr(config, "EXECUTOR_SHARED_TOKEN", "b" * 32)
    monkeypatch.setattr(config, "ORCHESTRATION_BACKEND", "local")
    monkeypatch.setattr(config, "MODEL_CONFIG_MODE", "self")
    with pytest.raises(RuntimeError, match="local orchestration requires"):
        config.validate()


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
