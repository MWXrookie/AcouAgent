"""Stable contracts used by the portal and runtime bus.

These lightweight DTOs define the edges of the system so the web layer reads a
small, versioned contract instead of depending on the concrete implementation of
model, execution, or skill modules.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class PortalCommand:
    """A simple request contract passed between the UI and orchestration layer."""

    name: str
    payload: dict[str, Any] | None = None
    headers: dict[str, str] | None = None


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


__all__ = ["PortalCommand", "ExecutionArtifact", "WorkflowEvent"]
