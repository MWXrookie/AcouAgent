"""Core adapter for the optional Dify workflow API."""
from __future__ import annotations

from collections.abc import AsyncIterator

from app import config
from app.contracts import BackendCapabilities, BackendInfo, ChatRequest, WorkflowEvent
from app.workflow_client import workflow_events as dify_workflow_events
from cores.events import normalize_workflow_events


class DifyCoreAdapter:
    """Core port implementation backed by the optional Dify workflow API."""

    @staticmethod
    def info() -> BackendInfo:
        return BackendInfo(
            backend_id="dify",
            backend_label="Dify 兼容工作流",
            capabilities=BackendCapabilities(
                chat=True,
                streaming=True,
                model_profiles=False,
                byok=False,
                model_preflight=False,
                skill_usage=True,
                model_config_mode="self",
            ),
        )

    @staticmethod
    async def events(request: ChatRequest) -> AsyncIterator[WorkflowEvent]:
        source = dify_workflow_events(
            request.requirement,
            f"portal-{request.session_id}" if request.session_id else "portal",
            config.DIFY_API_KEY,
        )
        async for item in normalize_workflow_events(source):
            yield item


__all__ = ["DifyCoreAdapter"]
