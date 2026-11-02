"""Core adapter for the project-native local orchestration."""
from __future__ import annotations

from collections.abc import AsyncIterator

from app import local_workflow
from app.contracts import BackendCapabilities, BackendInfo, ChatRequest, WorkflowEvent
from cores.events import normalize_workflow_events


class LocalCoreAdapter:
    """Core port implementation backed by the native local workflow."""

    @staticmethod
    def info() -> BackendInfo:
        return BackendInfo(
            backend_id="local",
            backend_label="本地编排",
            capabilities=BackendCapabilities(
                chat=True,
                streaming=True,
                model_profiles=True,
                byok=True,
                model_preflight=True,
                skill_usage=True,
                model_config_mode="managed",
            ),
        )

    @staticmethod
    async def events(request: ChatRequest) -> AsyncIterator[WorkflowEvent]:
        source = local_workflow.workflow_events(
            request.requirement,
            f"portal-{request.session_id}" if request.session_id else "portal",
            request.model_profile,
        )
        async for item in normalize_workflow_events(source):
            yield item

__all__ = ["LocalCoreAdapter"]
