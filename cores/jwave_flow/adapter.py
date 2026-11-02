"""Core adapter for the migrated and restricted jwave_flow workflow."""
from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

from app import config, model_profiles
from app.contracts import BackendCapabilities, BackendInfo, ChatRequest, WorkflowEvent
from cores.events import normalize_workflow_events
from cores.jwave_flow.client import jwave_flow_events


class JwaveFlowCoreAdapter:
    """Core port backed by the migrated jwave_flow LangGraph bridge."""

    @staticmethod
    def info() -> BackendInfo:
        mode = config.effective_model_config_mode()
        managed = mode == "managed"
        return BackendInfo(
            backend_id="jwave_flow",
            backend_label="jwave_flow LangGraph 核心",
            capabilities=BackendCapabilities(
                chat=True,
                streaming=True,
                model_profiles=managed,
                byok=managed,
                model_preflight=managed,
                skill_usage=False,
                model_config_mode=mode,
            ),
        )

    @staticmethod
    async def events(request: ChatRequest) -> AsyncIterator[WorkflowEvent]:
        mode = config.effective_model_config_mode()
        model_config = None
        if mode == "managed":
            try:
                profile, task = model_profiles.resolve(
                    request.model_profile, "code_generation"
                )
                model_profiles.validate_runtime_endpoint(profile)
                model_config = {
                    "base_url": profile.base_url,
                    "model": profile.model,
                    "api_key": profile.api_key(),
                    "temperature": task.temperature,
                    "timeout_seconds": task.timeout_seconds,
                }
            except model_profiles.ModelProfileError as exc:
                yield WorkflowEvent(
                    "result",
                    {
                        "report": "统一模型配置不可用，请检查模型 profile 或重新配置凭据。",
                        "workflow_status": "error",
                        "request_id": str(uuid.uuid4()),
                        "workflow_run_id": str(uuid.uuid4()),
                        "skill_usage": [],
                        "orchestration_backend": "jwave_flow",
                        "error_code": str(exc),
                    },
                )
                return
        elif request.model_profile:
            yield WorkflowEvent(
                "result",
                {
                    "report": "当前 jwave_flow 核心配置为模型自管理，不接受网关模型 profile。",
                    "workflow_status": "error",
                    "request_id": str(uuid.uuid4()),
                    "workflow_run_id": str(uuid.uuid4()),
                    "skill_usage": [],
                    "orchestration_backend": "jwave_flow",
                    "error_code": "capability_not_supported",
                },
            )
            return
        source = jwave_flow_events(
            request.requirement or request.message,
            model_config=model_config,
        )
        async for item in normalize_workflow_events(source):
            yield item


__all__ = ["JwaveFlowCoreAdapter"]
