"""Private skill-port routes used by the restricted jwave_flow bridge."""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import time
import uuid
from typing import Any

from starlette.responses import JSONResponse

from app import config, dashboard
from app.async_utils import await_thread

MAX_BODY_BYTES = 131_072
MAX_CONTEXT_BYTES = 65_536
TICKET_TTL_NS = 25_000_000_000
REFERENCE_REASONS = {
    "local_fixed_reference_delivery": "local",
    "general_validated_reference_delivery": "general",
}


def _response(payload: dict[str, Any], status_code: int = 200) -> JSONResponse:
    return JSONResponse(
        payload,
        status_code=status_code,
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
    )


def _unique(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate_json_key")
        value[key] = item
    return value


def _nonfinite(value):
    raise ValueError("nonfinite_json")


async def _body(request) -> dict[str, Any]:
    data = bytearray()
    async for chunk in request.stream():
        if len(data) + len(chunk) > MAX_BODY_BYTES:
            raise OverflowError("request_too_large")
        data.extend(chunk)
    value = json.loads(
        data.decode("utf-8"),
        object_pairs_hook=_unique,
        parse_constant=_nonfinite,
    )
    if not isinstance(value, dict):
        raise ValueError("object_required")
    return value


def _authorized(request) -> bool:
    token = config.JWAVE_FLOW_SKILL_PORT_TOKEN
    supplied = request.headers.get("X-Jwave-Flow-Skill-Token", "")
    return bool(token) and hmac.compare_digest(supplied, token)


def _text(value, *, name: str, maximum: int) -> str:
    if not isinstance(value, str) or not value or len(value.encode("utf-8")) > maximum:
        raise ValueError(f"invalid_{name}")
    return value


def _identity(value, *, name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"invalid_{name}")
    try:
        parsed = str(uuid.UUID(value))
    except (ValueError, AttributeError):
        raise ValueError(f"invalid_{name}") from None
    if parsed != value:
        raise ValueError(f"invalid_{name}")
    return value


def _ticket_signature(payload: str) -> str:
    key = config.JWAVE_FLOW_SKILL_PORT_TOKEN.encode("utf-8")
    return hmac.new(key, payload.encode("ascii"), hashlib.sha256).hexdigest()


def _encode_ticket(*, kind: str, decision_id: str, generation_intent_id: str,
                   workflow_run_id: str) -> str:
    payload = json.dumps(
        {
            "kind": kind,
            "decision_id": decision_id,
            "generation_intent_id": generation_intent_id,
            "workflow_run_id": workflow_run_id,
            "expires_ns": time.time_ns() + TICKET_TTL_NS,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    encoded = base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii")
    return f"{encoded}.{_ticket_signature(encoded)}"


def _decode_ticket(value: str) -> dict[str, str]:
    if not isinstance(value, str) or value.count(".") != 1 or len(value) > 2048:
        raise ValueError("invalid_skill_ticket")
    encoded, signature = value.split(".", 1)
    if not hmac.compare_digest(signature, _ticket_signature(encoded)):
        raise ValueError("invalid_skill_ticket")
    try:
        payload = json.loads(base64.urlsafe_b64decode(encoded.encode("ascii")))
    except (ValueError, UnicodeError, binascii.Error, json.JSONDecodeError):
        raise ValueError("invalid_skill_ticket") from None
    if not isinstance(payload, dict) or set(payload) != {
        "kind", "decision_id", "generation_intent_id", "workflow_run_id", "expires_ns"
    }:
        raise ValueError("invalid_skill_ticket")
    if payload["kind"] not in {"local", "general"}:
        raise ValueError("invalid_skill_ticket")
    _identity(payload["decision_id"], name="decision_id")
    _identity(payload["generation_intent_id"], name="generation_intent_id")
    _identity(payload["workflow_run_id"], name="workflow_run_id")
    if type(payload["expires_ns"]) is not int or not time.time_ns() < payload["expires_ns"]:
        raise ValueError("expired_skill_ticket")
    return payload


def _public_skills(values) -> list[dict[str, Any]]:
    result = []
    for item in values[:3] if isinstance(values, list) else []:
        if not isinstance(item, dict):
            continue
        skill = {
            key: item.get(key)
            for key in ("skill_id", "version", "scenario_type", "quality_level", "scope")
            if item.get(key) is not None
        }
        if skill:
            result.append(skill)
    return result


def _notices(mode: str, skills: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if mode == "reference":
        return [{
            "stage": "reference_selected",
            "skill_id": item.get("skill_id"),
            "version": item.get("version"),
            "name": item.get("scope") or item.get("scenario_type") or "固定技能",
            "message": (
                "已选择经服务端重核的固定技能参考，"
                "等待重新实际执行"
            ),
            "execution_reuse_verified": False,
            "model_adoption_authenticated": False,
        } for item in skills]
    if mode == "guided":
        return [{
            "stage": "guidance_applied",
            "skill_id": item.get("skill_id"),
            "version": item.get("version"),
            "name": item.get("scenario_type") or "技能约束",
            "message": "已将同场景技能约束用于生成，未复用固定代码",
            "execution_reuse_verified": False,
            "model_adoption_authenticated": False,
        } for item in skills]
    return []


def _reference_request(body: dict[str, Any]) -> dict[str, Any]:
    fields = {
        "request_id", "workflow_run_id", "original_context",
        "scenario_type", "parameters_json",
    }
    if set(body) != fields:
        raise ValueError("invalid_request_fields")
    return {
        "request_id": _identity(body["request_id"], name="request_id"),
        "workflow_run_id": _identity(body["workflow_run_id"], name="workflow_run_id"),
        "original_context": _text(
            body["original_context"], name="original_context", maximum=MAX_CONTEXT_BYTES
        ),
        "scenario_type": _text(body["scenario_type"], name="scenario_type", maximum=80),
        "parameters_json": _text(body["parameters_json"], name="parameters_json", maximum=4096),
    }


def _execution_request(body: dict[str, Any]) -> tuple[dict[str, str], dict[str, Any]]:
    fields = {
        "skill_ticket", "workflow_run_id", "original_context",
        "scenario_type", "parameters_json",
    }
    if set(body) != fields:
        raise ValueError("invalid_request_fields")
    ticket = _decode_ticket(body["skill_ticket"])
    workflow_run_id = _identity(body["workflow_run_id"], name="workflow_run_id")
    if ticket["workflow_run_id"] != workflow_run_id:
        raise ValueError("skill_ticket_scope")
    return ticket, {
        "workflow_run_id": workflow_run_id,
        "original_context": _text(
            body["original_context"], name="original_context", maximum=MAX_CONTEXT_BYTES
        ),
        "scenario_type": _text(body["scenario_type"], name="scenario_type", maximum=80),
        "parameters_json": _text(body["parameters_json"], name="parameters_json", maximum=4096),
    }


def _bounded_execution(run_id: str | None, analysis: dict[str, Any] | None = None):
    execution = dashboard.get_execution_by_run_id(run_id) if run_id else None
    result = {}
    if execution:
        result = {
            "exit_code": execution.get("exit_code"),
            "stdout": str(execution.get("stdout") or "")[:500_000],
            "stderr": str(execution.get("stderr") or "")[:100_000],
            "timed_out": execution.get("timed_out") is True,
            "duration_ms": execution.get("duration_ms"),
        }
    safe_analysis = {}
    if isinstance(analysis, dict):
        safe_analysis = {
            key: analysis.get(key)
            for key in (
                "verdict", "quality_level", "max_pressure", "rms_pressure", "field_shape",
                "has_signal", "summary", "units", "physics_checks", "heatmap_png_base64",
                "waveform_png_base64", "result_kind", "metric_basis",
            )
            if analysis.get(key) is not None
        }
    return result, safe_analysis


def register(mcp) -> None:
    @mcp.custom_route("/internal/jwave-flow/skills/resolve", methods=["POST"])
    async def resolve_skill(request):
        if not _authorized(request):
            return _response({"error": "unauthorized"}, 401)
        try:
            values = _reference_request(await _body(request))
        except OverflowError:
            return _response({"error": "request_too_large"}, 413)
        except (ValueError, TypeError, UnicodeError, json.JSONDecodeError):
            return _response({"error": "invalid_request"}, 400)

        from app import skill_deadline, skill_retrieval
        try:
            with skill_deadline.limited(config.JWAVE_FLOW_SKILL_TIMEOUT_SECONDS):
                generation = await await_thread(
                    skill_retrieval.generation_context,
                    values["original_context"],
                    scenario_type=values["scenario_type"],
                    parameters_json=values["parameters_json"],
                    _existing_store=True,
                )
        except (TimeoutError, ValueError, TypeError, OSError):
            return _response({"mode": "fallback", "reason": "skill_port_unavailable"})

        usage = generation.get("skill_usage") if isinstance(generation, dict) else None
        reason = usage.get("reason") if isinstance(usage, dict) else "skill_port_unavailable"
        reference_kind = REFERENCE_REASONS.get(reason)
        if reference_kind is not None:
            decision_id = usage.get("decision_id")
            generation_intent_id = usage.get("generation_intent_id")
            try:
                ticket = _encode_ticket(
                    kind=reference_kind,
                    decision_id=_identity(decision_id, name="decision_id"),
                    generation_intent_id=_identity(
                        generation_intent_id, name="generation_intent_id"
                    ),
                    workflow_run_id=values["workflow_run_id"],
                )
            except ValueError:
                return _response({"mode": "fallback", "reason": "reference_ticket_unavailable"})
            skills = _public_skills(usage.get("skills"))
            return _response({
                "mode": "reference",
                "reason": reason,
                "skill_ticket": ticket,
                "skills": skills,
                "skill_usage": _notices("reference", skills),
                "guidance_context": "",
                "expires_in_ms": TICKET_TTL_NS // 1_000_000,
            })

        if config.JWAVE_FLOW_SKILL_MODE == "full":
            guidance = await await_thread(
                skill_retrieval.guidance_context,
                values["scenario_type"],
                values["parameters_json"],
                3,
            )
            if guidance.get("available") is True:
                skills = _public_skills(guidance.get("skills"))
                return _response({
                    "mode": "guided",
                    "reason": guidance.get("reason", "same_scenario_guidance"),
                    "skill_ticket": "",
                    "skills": skills,
                    "skill_usage": _notices("guided", skills),
                    "guidance_context": guidance.get("context", ""),
                    "expires_in_ms": 0,
                })
        return _response({"mode": "fallback", "reason": reason or "no_applicable_skill"})

    @mcp.custom_route("/internal/jwave-flow/skills/execute-reference", methods=["POST"])
    async def execute_reference(request):
        if not _authorized(request):
            return _response({"error": "unauthorized"}, 401)
        try:
            ticket, values = _execution_request(await _body(request))
        except OverflowError:
            return _response({"error": "request_too_large"}, 413)
        except (ValueError, TypeError, UnicodeError, json.JSONDecodeError):
            return _response({"execution_reuse_verified": False, "reason": "invalid_ticket"}, 400)

        if ticket["kind"] == "general":
            from app.skill_general_library import execute_reference as execute
        else:
            from app.skill_local_execution import execute_reference as execute
        outcome = await await_thread(
            execute,
            decision_id=ticket["decision_id"],
            generation_run_id=ticket["generation_intent_id"],
            scenario_type=values["scenario_type"],
            parameters_json=values["parameters_json"],
            original_context=values["original_context"],
        )
        verified = isinstance(outcome, dict) and outcome.get("execution_reuse_verified") is True
        result, analysis = _bounded_execution(
            outcome.get("run_id") if isinstance(outcome, dict) else None,
            outcome.get("analysis") if isinstance(outcome, dict) else None,
        )
        return _response({
            "run_id": outcome.get("run_id") if isinstance(outcome, dict) else None,
            "execution_reuse_verified": verified,
            "reason": (
                outcome.get("reason", "reference_execution_unavailable")
                if isinstance(outcome, dict)
                else "reference_execution_unavailable"
            ),
            "result": result,
            "analysis": analysis,
            "model_adoption_authenticated": False,
        })


__all__ = ["register"]
