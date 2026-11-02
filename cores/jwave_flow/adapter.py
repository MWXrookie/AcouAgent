"""Core adapter for the migrated and restricted jwave_flow workflow."""
from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator

from app import config, model_profiles
from app.async_utils import await_thread
from app.contracts import BackendCapabilities, BackendInfo, ChatRequest, WorkflowEvent
from cores.events import normalize_workflow_events
from cores.jwave_flow.client import jwave_flow_events


def _learning_query(value: str) -> tuple[str, str] | None:
    try:
        query = json.loads(value)
    except (ValueError, TypeError):
        return None
    if not isinstance(query, dict):
        return None
    scenario_type = query.get("scenario_type")
    parameters = query.get("parameters")
    result_spec = query.get("result_spec")
    if (
        not isinstance(scenario_type, str)
        or not isinstance(parameters, dict)
        or not isinstance(result_spec, dict)
    ):
        return None
    envelope = {
        "normalizer_version": "normalizer_v2",
        "parameters": parameters,
        "result_spec": result_spec,
    }
    try:
        parameters_json = json.dumps(
            envelope,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError):
        return None
    if len(parameters_json.encode("utf-8")) > 4096:
        return None
    return scenario_type, parameters_json


def _queue_learning(value: str | None, workflow_run_id: str) -> dict:
    if not value:
        return {"queued": False, "reason": "not_applicable"}
    query = _learning_query(value)
    if query is None:
        return {"queued": False, "reason": "not_applicable"}
    try:
        from app.skill_auto_source import queue_source

        return queue_source(
            scenario_type=query[0],
            parameters_json=query[1],
            request_key="jwave_flow:" + workflow_run_id,
        )
    except Exception:
        return {"queued": False, "reason": "learning_queue_unavailable"}


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
                skill_usage=True,
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
            if item.event != "result":
                yield item
                continue
            data = dict(item.data)
            skill_query = data.pop("skill_query_json", None)
            if data.get("execution_reuse_verified") is True:
                data["learning"] = {"queued": False, "reason": "reference_already_reused"}
            elif data.get("success") is True and data.get("verified") is True:
                data["learning"] = await await_thread(
                    _queue_learning,
                    skill_query,
                    str(data.get("workflow_run_id") or ""),
                )
            yield WorkflowEvent(item.event, data)


__all__ = ["JwaveFlowCoreAdapter"]
