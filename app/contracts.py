"""Stable contracts used by the portal and agent runtime.

These lightweight DTOs define the edges of the system so web and native clients
read a small, versioned contract instead of depending on the concrete
implementation of model, execution, or skill modules.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol

MAX_CHAT_FIELD_BYTES = 65536
MAX_MODEL_PROFILE_CHARS = 64


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
        if len(message.encode("utf-8")) > MAX_CHAT_FIELD_BYTES:
            raise ValueError("message_too_large")
        if len(requirement.encode("utf-8")) > MAX_CHAT_FIELD_BYTES:
            raise ValueError("requirement_too_large")
        if model_profile is not None and (
            not isinstance(model_profile, str)
            or len(model_profile) > MAX_MODEL_PROFILE_CHARS
        ):
            raise ValueError("invalid model_profile")
        return cls(
            message=message.strip(),
            requirement=requirement.strip(),
            # Keep the established legacy behavior for browser session keys.
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
    """Stable event shape for streaming workflow output to every client."""

    event: str
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = dict(self.data)
        payload["event"] = self.event
        return payload


@dataclass(frozen=True)
class BackendCapabilities:
    """Capabilities a backend may expose without forcing feature parity."""

    chat: bool = True
    streaming: bool = True
    model_profiles: bool = False
    byok: bool = False
    model_preflight: bool = False
    skill_usage: bool = True

    def to_dict(self) -> dict[str, bool]:
        return {
            "chat": self.chat,
            "streaming": self.streaming,
            "model_profiles": self.model_profiles,
            "byok": self.byok,
            "model_preflight": self.model_preflight,
            "skill_usage": self.skill_usage,
        }


@dataclass(frozen=True)
class BackendInfo:
    """Public identity and capability declaration for one core backend."""

    backend_id: str
    backend_label: str
    capabilities: BackendCapabilities
    api_version: str = "v1"

    def to_dict(self) -> dict[str, Any]:
        return {
            "api_version": self.api_version,
            "backend_id": self.backend_id,
            "backend_label": self.backend_label,
            "capabilities": self.capabilities.to_dict(),
        }


class AgentApiError(RuntimeError):
    """A safe, client-visible error from the agent API boundary."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int = 400,
        request_id: str | None = None,
        details: Any = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.request_id = request_id
        self.details = details


class CoreBackend(Protocol):
    """Port implemented by local, Dify, remote, or future core adapters."""

    def info(self) -> BackendInfo:
        """Describe this backend and its capabilities."""
        ...

    def events(self, request: ChatRequest) -> AsyncIterator[WorkflowEvent]:
        """Stream normalized workflow events for one frontend request."""
        ...


class ModelManagementBackend(Protocol):
    """Optional port for backends that own model profiles and BYOK sessions."""

    def model_profiles(self) -> dict[str, Any]:
        """Return public model profile metadata without credentials."""
        ...

    def create_model_session(self, payload: object) -> dict[str, Any]:
        """Create one expiring opaque model session."""
        ...

    async def test_model_profile(self, profile_id: str) -> dict[str, Any]:
        """Run one bounded model compatibility preflight."""
        ...

    def remove_model_session(self, profile_id: object) -> bool:
        """Revoke one session-owned model profile."""
        ...


__all__ = [
    "PortalCommand",
    "ChatRequest",
    "ExecutionArtifact",
    "WorkflowEvent",
    "BackendCapabilities",
    "BackendInfo",
    "AgentApiError",
    "CoreBackend",
    "ModelManagementBackend",
]
