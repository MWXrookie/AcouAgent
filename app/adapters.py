"""Adapter layer for the versioned agent API and runtime backend contract.

Adapters isolate HTTP handlers and the runtime bus from concrete implementation
modules so local, Dify, remote, or future backends can be selected without the
web layer importing their internals.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from app import (
    analysis,
    cache_store,
    config,
    execution,
    local_workflow,
    model_client,
    model_preflight,
    model_profiles,
    skill_library_service,
)
from app.contracts import (
    AgentApiError,
    BackendInfo,
    ChatRequest,
    WorkflowEvent,
)
from app.dashboard import (
    clear_executions,
    get_analysis_updates,
    get_execution,
    get_execution_by_run_id,
    get_executions,
    get_latest_analysis_by_run_ids,
    get_stats,
    record_admin_event,
    record_execution,
)
from cores.dify.adapter import DifyCoreAdapter
from cores.events import normalize_workflow_events as _normalize_workflow_events
from cores.jwave_flow.adapter import JwaveFlowCoreAdapter
from cores.local.adapter import LocalCoreAdapter


class ModelAdapter:
    @staticmethod
    def public_profiles() -> dict[str, Any]:
        return model_profiles.public_profiles()

    @staticmethod
    def register_session_profile(body: dict[str, Any]) -> dict[str, Any]:
        return model_profiles.register_session_profile(body)

    @staticmethod
    def remove_session_profile(profile_id: str | None) -> bool:
        return model_profiles.remove_session_profile(profile_id)

    @staticmethod
    async def preflight(profile_id: str) -> dict[str, Any]:
        return await model_preflight.run(profile_id)

    @staticmethod
    async def merge_requirement(
        requirement: str,
        message: str,
        model_profile: str | None = None,
    ) -> str:
        prompt = (
            "你是声学仿真需求整理助手。根据当前完整需求和用户新修改，输出更新后的完整需求。"
            "新修改覆盖旧值；只输出需求，不要解释。\n\n"
            f"当前完整需求：\n{requirement}\n\n用户新修改：\n{message}"
        )
        try:
            result = await model_client.complete(
                model_profile,
                "requirement_merge",
                [{"role": "user", "content": prompt}],
            )
            return result["content"]
        except (model_profiles.ModelProfileError, model_client.ModelCallError):
            return f"{requirement}；{message}"


class ExecutionAdapter:
    @staticmethod
    def clean_code(code: str) -> str:
        return execution._clean_code(code)

    @staticmethod
    def execute(
        code: str,
        timeout_seconds: int,
        *,
        request_nonce: str | None = None,
    ) -> dict[str, Any]:
        return execution._execute_code(code, timeout_seconds, request_nonce=request_nonce)

    @staticmethod
    def normalize_result(result: dict[str, Any]) -> None:
        execution._normalize_result_artifacts(result)

    @staticmethod
    def shrink_stdout(stdout: str, max_chars: int = 350000) -> str:
        return execution._shrink_field_in_stdout(stdout, max_chars=max_chars)


class WorkflowAdapter:
    """Selects a core port without exposing backend details to HTTP handlers."""

    _backends = {
        "local": LocalCoreAdapter,
        "dify": DifyCoreAdapter,
        "jwave_flow": JwaveFlowCoreAdapter,
    }

    @staticmethod
    def _selected_backend():
        backend = WorkflowAdapter._backends.get(config.ORCHESTRATION_BACKEND)
        if backend is None:
            raise AgentApiError(
                "backend_unavailable",
                "当前编排后端不可用。",
                status_code=503,
            )
        return backend

    @staticmethod
    def info() -> BackendInfo:
        return WorkflowAdapter._selected_backend().info()

    @staticmethod
    def run(payload: dict[str, Any]) -> dict[str, Any]:
        return local_workflow.run(payload)

    @staticmethod
    async def events(request: ChatRequest) -> AsyncIterator[WorkflowEvent]:
        backend = WorkflowAdapter._selected_backend()
        async for event in backend.events(request):
            yield event

class AnalysisAdapter:
    @staticmethod
    def analyze(
        stdout: str,
        stderr: str,
        exit_code: int,
        params: str,
        run_id: str,
    ) -> dict[str, Any]:
        return analysis.analyze_and_record(stdout, stderr, exit_code, params, run_id)


class SkillAdapter:
    @staticmethod
    def authorize(token: str) -> None:
        skill_library_service.authorize(token)

    @staticmethod
    def read_execution_links(run_ids: list[str]) -> dict[str, Any]:
        return skill_library_service.STORE.read_execution_links(run_ids)


class DashboardAdapter:
    @staticmethod
    def chat_html() -> str:
        return (Path(__file__).resolve().parent / "web" / "chat.html").read_text(encoding="utf-8")

    @staticmethod
    def get_execution_by_run_id(run_id: str):
        return get_execution_by_run_id(run_id)

    @staticmethod
    def get_executions(limit: int = 100, since_id: int = 0):
        return get_executions(limit=limit, since_id=since_id)

    @staticmethod
    def get_analysis_updates(
        limit: int = 100,
        since_id: int = 0,
        include_images: bool = True,
    ):
        return get_analysis_updates(
            limit=limit,
            since_id=since_id,
            include_images=include_images,
        )

    @staticmethod
    def get_latest_analysis_by_run_ids(
        run_ids: list[str],
        include_images: bool = True,
    ):
        return get_latest_analysis_by_run_ids(run_ids, include_images=include_images)

    @staticmethod
    def get_stats():
        return get_stats()

    @staticmethod
    def record_admin_event(
        action: str,
        target_id: str | None,
        remote_host: str | None,
        allowed: bool,
        detail: str,
    ) -> None:
        record_admin_event(
            action,
            target_id=target_id,
            remote_host=remote_host,
            allowed=allowed,
            detail=detail,
        )

    @staticmethod
    def record_execution(**kwargs):
        return record_execution(**kwargs)

    @staticmethod
    def clear_executions() -> int:
        return clear_executions()

    @staticmethod
    def get_execution(execution_id: int):
        return get_execution(execution_id)


class CacheAdapter:
    @staticmethod
    def stats() -> dict[str, Any]:
        return cache_store.get_cache_stats()


MODEL_ADAPTER = ModelAdapter()
EXECUTION_ADAPTER = ExecutionAdapter()
WORKFLOW_ADAPTER = WorkflowAdapter()
ANALYSIS_ADAPTER = AnalysisAdapter()
SKILL_ADAPTER = SkillAdapter()
DASHBOARD_ADAPTER = DashboardAdapter()
CACHE_ADAPTER = CacheAdapter()

__all__ = [
    "ModelAdapter",
    "ExecutionAdapter",
    "WorkflowAdapter",
    "LocalCoreAdapter",
    "DifyCoreAdapter",
    "JwaveFlowCoreAdapter",
    "_normalize_workflow_events",
    "AnalysisAdapter",
    "SkillAdapter",
    "DashboardAdapter",
    "CacheAdapter",
    "MODEL_ADAPTER",
    "EXECUTION_ADAPTER",
    "WORKFLOW_ADAPTER",
    "ANALYSIS_ADAPTER",
    "SKILL_ADAPTER",
    "DASHBOARD_ADAPTER",
    "CACHE_ADAPTER",
]
