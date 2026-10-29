"""Stable runtime bus for the portal layer.

This module intentionally sits between the web-facing portal and the concrete
runtime modules (model profiles, execution, skill retrieval, dashboard audits).
The goal is to keep the portal dependent on a thin, stable contract instead of a
large set of direct imports and implementation details.
"""

from __future__ import annotations

import json
from typing import Any

from app import config
from app.adapters import (
    ANALYSIS_ADAPTER,
    CACHE_ADAPTER,
    DASHBOARD_ADAPTER,
    EXECUTION_ADAPTER,
    MODEL_ADAPTER,
    SKILL_ADAPTER,
    WORKFLOW_ADAPTER,
)
from app.contracts import ChatRequest, CoreBackend, PortalCommand


class RuntimeBus:
    """Thin façade over the concrete runtime modules.

    The portal must not import the implementation modules directly. Instead, it
    talks to this bus, which preserves a stable contract while the underlying
    runtime can evolve independently.
    """

    @staticmethod
    def public_profiles() -> dict[str, Any]:
        return MODEL_ADAPTER.public_profiles()

    @staticmethod
    def register_session_profile(body: dict[str, Any]) -> dict[str, Any]:
        return MODEL_ADAPTER.register_session_profile(body)

    @staticmethod
    def remove_session_profile(profile_id: str | None) -> bool:
        return MODEL_ADAPTER.remove_session_profile(profile_id)

    @staticmethod
    async def model_preflight(profile_id: str) -> dict[str, Any]:
        return await MODEL_ADAPTER.preflight(profile_id)

    @staticmethod
    async def merge_requirement(requirement: str, message: str, model_profile: str | None = None) -> str:
        """Merge turns through the selected BYOK profile; fail closed to concatenation."""
        return await MODEL_ADAPTER.merge_requirement(requirement, message, model_profile)

    @staticmethod
    def parse_report(output: Any) -> str:
        """Return a Markdown report string from workflow outputs."""
        if isinstance(output, str):
            return output
        if isinstance(output, list) and output:
            first = output[0]
            if isinstance(first, str):
                return first
            if isinstance(first, dict):
                return first.get("result", "") or first.get("report", "") or json.dumps(first, ensure_ascii=False)
        if isinstance(output, dict):
            return output.get("result", "") or output.get("report", "") or json.dumps(output, ensure_ascii=False)
        return ""

    @staticmethod
    def require_admin_record(
        action: str,
        target_id: str | None,
        request: Any,
        *,
        allowed: bool,
        detail: str,
    ) -> None:
        remote_host = request.client.host if request.client else None
        DASHBOARD_ADAPTER.record_admin_event(
            action,
            target_id=target_id,
            remote_host=remote_host,
            allowed=allowed,
            detail=detail,
        )

    @staticmethod
    def execution_cleaner(code: str) -> str:
        return EXECUTION_ADAPTER.clean_code(code)

    @staticmethod
    def execute_remote(code: str, timeout_seconds: int, *, request_nonce: str | None = None) -> dict[str, Any]:
        return EXECUTION_ADAPTER.execute(code, timeout_seconds, request_nonce=request_nonce)

    @staticmethod
    def normalize_result_artifacts(result: dict[str, Any]) -> None:
        EXECUTION_ADAPTER.normalize_result(result)

    @staticmethod
    def shrink_field_in_stdout(stdout: str, max_chars: int = 350000) -> str:
        return EXECUTION_ADAPTER.shrink_stdout(stdout, max_chars=max_chars)

    @staticmethod
    def local_workflow_run(payload: dict[str, Any]) -> dict[str, Any]:
        return WORKFLOW_ADAPTER.run(payload)

    @staticmethod
    def workflow_events(requirement: str, user: str, model_profile: str | None = None) -> Any:
        request = ChatRequest(
            message=requirement,
            requirement=requirement,
            session_id=user.removeprefix("portal-") if user.startswith("portal-") else "",
            model_profile=model_profile,
        )
        return WORKFLOW_ADAPTER.events(request)

    @staticmethod
    async def chat_events(request: ChatRequest) -> Any:
        """Return the stable event stream exposed to every frontend."""
        backend: CoreBackend = WORKFLOW_ADAPTER
        async for event in backend.events(request):
            yield event

    @staticmethod
    def analyze_and_record(stdout: str, stderr: str, exit_code: int, params: str, run_id: str) -> dict[str, Any]:
        return ANALYSIS_ADAPTER.analyze(stdout, stderr, exit_code, params, run_id)

    @staticmethod
    def get_cache_stats() -> dict[str, Any]:
        return CACHE_ADAPTER.stats()

    @staticmethod
    def skill_authorization(token: str) -> None:
        SKILL_ADAPTER.authorize(token)

    @staticmethod
    def read_execution_links(run_ids: list[str]) -> dict[str, Any]:
        return SKILL_ADAPTER.read_execution_links(run_ids)

    @staticmethod
    def chat_html() -> str:
        return DASHBOARD_ADAPTER.chat_html()

    @staticmethod
    def dashboard_executions(limit: int = 100, since_id: int = 0):
        return DASHBOARD_ADAPTER.get_executions(limit=limit, since_id=since_id)

    @staticmethod
    def dashboard_analysis_updates(limit: int = 100, since_id: int = 0, include_images: bool = True):
        return DASHBOARD_ADAPTER.get_analysis_updates(limit=limit, since_id=since_id, include_images=include_images)

    @staticmethod
    def get_latest_analysis_by_run_ids(run_ids: list[str], include_images: bool = True):
        return DASHBOARD_ADAPTER.get_latest_analysis_by_run_ids(run_ids, include_images=include_images)

    @staticmethod
    def dashboard_stats():
        return DASHBOARD_ADAPTER.get_stats()

    @staticmethod
    def execution_record(**kwargs):
        return DASHBOARD_ADAPTER.record_execution(**kwargs)

    @staticmethod
    def clear_execution_log() -> int:
        return DASHBOARD_ADAPTER.clear_executions()

    @staticmethod
    def get_execution_row(execution_id: int):
        return DASHBOARD_ADAPTER.get_execution(execution_id)

    @staticmethod
    def get_execution_row_by_run_id(run_id: str):
        return DASHBOARD_ADAPTER.get_execution_by_run_id(run_id)


RUNTIME_BUS = RuntimeBus()

__all__ = ["PortalCommand", "RuntimeBus", "RUNTIME_BUS"]
