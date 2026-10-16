"""Bounded OpenAI-compatible chat transport for self-hosted BYOK profiles."""
from __future__ import annotations

import asyncio
import json
import re
import uuid
from typing import Any

import httpx

from app import config, dashboard, model_profiles

MAX_RESPONSE_BYTES = 2 * 1024 * 1024


class ModelCallError(RuntimeError):
    """A model request failed without exposing endpoint or credential details."""


def _content(payload: Any) -> str:
    if not isinstance(payload, dict):
        raise ModelCallError("model_response_invalid")
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise ModelCallError("model_response_invalid")
    message = choices[0].get("message")
    if not isinstance(message, dict) or not isinstance(message.get("content"), str):
        raise ModelCallError("model_response_invalid")
    text = re.sub(r"<think>.*?</think>", "", message["content"], flags=re.DOTALL).strip()
    if not text:
        raise ModelCallError("model_response_empty")
    return text


def _validate_messages(messages: list[dict[str, str]]) -> None:
    if (not isinstance(messages, list) or not 1 <= len(messages) <= 16
            or any(not isinstance(item, dict) or set(item) != {"role", "content"}
                   or item["role"] not in {"system", "user", "assistant"}
                   or not isinstance(item["content"], str)
                   or len(item["content"].encode("utf-8")) > 100000 for item in messages)):
        raise ModelCallError("model_messages_invalid")


async def _complete_resolved(
    profile: model_profiles.ModelProfile,
    settings: model_profiles.TaskSettings,
    task: str,
    messages: list[dict[str, str]],
    *,
    run_id: str | None = None,
) -> dict[str, Any]:
    model_profiles.validate_runtime_endpoint(profile)
    _validate_messages(messages)
    body: dict[str, Any] = {
        "model": profile.model,
        "messages": messages,
        "temperature": settings.temperature,
        "max_tokens": settings.max_tokens,
    }
    if settings.thinking:
        body["thinking"] = True
    headers = {
        "Authorization": f"Bearer {profile.api_key()}",
        "Content-Type": "application/json",
    }
    try:
        async with httpx.AsyncClient(
            timeout=settings.timeout_seconds, follow_redirects=False
        ) as client:
            async with client.stream(
                "POST", profile.endpoint(), headers=headers, json=body
            ) as response:
                response.raise_for_status()
                raw = bytearray()
                async for chunk in response.aiter_bytes():
                    raw.extend(chunk)
                    if len(raw) > MAX_RESPONSE_BYTES:
                        raise ModelCallError("model_response_too_large")
        payload = json.loads(raw)
        text = _content(payload)
    except ModelCallError:
        raise
    except (httpx.HTTPError, json.JSONDecodeError, UnicodeError, TypeError, ValueError) as exc:
        raise ModelCallError("model_transport_failed") from exc
    usage = payload.get("usage") if isinstance(payload, dict) else None
    actual_run_id = run_id or str(uuid.uuid4())
    try:
        dashboard.record_llm_usage(
            f"{profile.provider}:{profile.model}",
            usage if isinstance(usage, dict) else {},
            config.calc_llm_cost(usage if isinstance(usage, dict) else {}),
            run_id=actual_run_id,
            metadata={
                "profile_id": profile.audit_profile_id(),
                "provider": profile.provider,
                "model": profile.model,
                "task": task,
                "temperature": settings.temperature,
                "max_tokens": settings.max_tokens,
                "thinking": settings.thinking,
                "timeout_seconds": settings.timeout_seconds,
            },
        )
    except Exception:
        pass
    return {
        "content": text,
        "profile_id": profile.audit_profile_id(),
        "provider": profile.provider,
        "model": profile.model,
        "task": task,
        "settings": {
            "temperature": settings.temperature,
            "max_tokens": settings.max_tokens,
            "thinking": settings.thinking,
            "timeout_seconds": settings.timeout_seconds,
        },
        "usage": usage if isinstance(usage, dict) else {},
        "run_id": actual_run_id,
    }


async def complete(profile_id: str | None, task: str, messages: list[dict[str, str]],
                   *, run_id: str | None = None) -> dict[str, Any]:
    profile, settings = model_profiles.resolve(profile_id, task)
    return await _complete_resolved(profile, settings, task, messages, run_id=run_id)


def complete_sync(profile_id: str | None, task: str, messages: list[dict[str, str]],
                  *, run_id: str | None = None) -> dict[str, Any]:
    """Run the shared async transport from a synchronous FastMCP worker."""
    return asyncio.run(complete(profile_id, task, messages, run_id=run_id))


async def probe(profile_id: str | None, messages: list[dict[str, str]],
                *, run_id: str | None = None) -> dict[str, Any]:
    """Run one low-cost capability probe against a full-workflow profile.

    Resolving both tasks verifies that the selected profile declares the JSON and
    code capabilities used by local orchestration. The transport remains the same
    bounded, redirect-free path as normal model calls.
    """
    profile, _ = model_profiles.resolve(profile_id, "parameter_extraction")
    code_profile, _ = model_profiles.resolve(profile_id, "code_generation")
    if code_profile.profile_id != profile.profile_id:
        raise model_profiles.ModelProfileError("model_profile_resolution_mismatch")
    settings = model_profiles.TaskSettings(
        temperature=0.0,
        max_tokens=160,
        thinking=False,
        timeout_seconds=30.0,
    )
    return await _complete_resolved(profile, settings, "preflight", messages, run_id=run_id)
