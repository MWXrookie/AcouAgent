"""Shared normalization for events emitted by selectable agent cores."""
from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

from app.contracts import WorkflowEvent

VALID_WORKFLOW_EVENTS = frozenset({"progress", "skill_usage", "result"})


def extract_report(output: Any) -> str:
    """Return a Markdown report from known workflow output shapes."""
    if isinstance(output, str):
        return output
    if isinstance(output, list) and output:
        first = output[0]
        if isinstance(first, str):
            return first
        if isinstance(first, dict):
            return first.get("result", "") or first.get("report", "") or json.dumps(
                first, ensure_ascii=False
            )
    if isinstance(output, dict):
        return (
            output.get("result", "")
            or output.get("report", "")
            or output.get("text", "")
            or json.dumps(output, ensure_ascii=False)
        )
    return ""


async def normalize_workflow_events(source) -> AsyncIterator[WorkflowEvent]:
    """Validate and normalize backend events before they cross the API edge."""
    async for item in source:
        if not isinstance(item, dict):
            raise ValueError("workflow_event_must_be_object")
        event = item.get("event")
        if event not in VALID_WORKFLOW_EVENTS:
            raise ValueError("workflow_event_invalid_name")
        data = {key: value for key, value in item.items() if key != "event"}
        if event == "result":
            if "report" not in data and "outputs" in data:
                data["report"] = extract_report(data.pop("outputs"))
            elif "report" not in data:
                data["report"] = ""
        yield WorkflowEvent(event=event, data=data)


__all__ = ["VALID_WORKFLOW_EVENTS", "extract_report", "normalize_workflow_events"]
