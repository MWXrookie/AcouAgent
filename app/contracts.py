"""Stable contracts used by the portal and runtime bus.

These lightweight DTOs define the edges of the system so the web layer reads a
small, versioned contract instead of depending on the concrete implementation of
model, execution, or skill modules.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol


@dataclass(frozen=True)
class PortalCommand:
    """A simple request contract passed between the UI and orchestration layer."""

    name: str
    payload: dict[str, Any] | None = None
    headers: dict[str, str] | None = None


@dataclass(frozen=True)
class ChatRequest:
    """Frontend-independent request contract for one orchestration turn."""

    message: str
    requirement: str = ""
    session_id: str = ""
    model_profile: str | None = None
    stream: bool = False

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "ChatRequest":
        if not isinstance(payload, Mapping):
            raise ValueError("invalid_request")
        message = payload.get("message")
        if not isinstance(message, str) or not message.strip():
            raise ValueError("message is required")
        requirement = payload.get("requirement", "")
        session_id = payload.get("session_id", "")
        model_profile = payload.get("model_profile")
        if not isinstance(requirement, str) or not isinstance(session_id, str):
            raise ValueError("invalid_request")
        if model_profile is not None and (
            not isinstance(model_profile, str) or len(model_profile) > 64
        ):
            raise ValueError("invalid model_profile")
        return cls(
            message=message.strip(),
            requirement=requirement.strip(),
            session_id=session_id.strip()[:24],
            model_profile=model_profile,
            stream=payload.get("stream") is True,
        )


@dataclass
class ExecutionArtifact:
    """Stable execution payload used by the portal and dashboard."""

    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    duration_ms: int | None = None
    image_base64: str | None = None
    analysis: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        data = {
            "exit_code": self.exit_code,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "timed_out": self.timed_out,
            "duration_ms": self.duration_ms,
            "image_base64": self.image_base64,
            "analysis": self.analysis,
        }
        return {key: value for key, value in data.items() if value is not None}


@dataclass
class WorkflowEvent:
    """Stable event shape for streaming workflow output to the portal."""

    event: str
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = dict(self.data)
        payload["event"] = self.event
        return payload


class CoreBackend(Protocol):
    """Port implemented by local, Dify, LangChain, or future core adapters."""

    def events(self, request: ChatRequest) -> AsyncIterator[WorkflowEvent]:
        """Stream normalized workflow events for one frontend request."""
        ...


__all__ = [
    "PortalCommand",
    "ChatRequest",
    "ExecutionArtifact",
    "WorkflowEvent",
    "CoreBackend",
]
