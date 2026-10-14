"""Server-owned OpenAI-compatible model profiles.

The browser selects only a profile id. Endpoints, models and credential environment
references are controlled by the operator and never returned with secret values.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

TASKS = (
    "requirement_merge",
    "parameter_extraction",
    "code_generation",
    "result_review",
    "result_explanation",
    "retry",
)
TASK_CAPABILITIES = {
    "requirement_merge": frozenset({"chat"}),
    "parameter_extraction": frozenset({"chat", "json"}),
    "code_generation": frozenset({"chat", "code"}),
    "result_review": frozenset({"chat"}),
    "result_explanation": frozenset({"chat"}),
    "retry": frozenset({"chat", "code"}),
}
_ID = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")
_ENV = re.compile(r"[A-Z][A-Z0-9_]{1,127}")


class ModelProfileError(ValueError):
    """A profile is absent, malformed, unsupported or not configured."""


@dataclass(frozen=True)
class TaskSettings:
    temperature: float
    max_tokens: int
    thinking: bool
    timeout_seconds: float


@dataclass(frozen=True)
class ModelProfile:
    profile_id: str
    label: str
    provider: str
    base_url: str
    model: str
    api_key_env: str
    allow_insecure_http: bool
    tasks: dict[str, TaskSettings]
    capabilities: frozenset[str]

    def endpoint(self) -> str:
        return self.base_url.rstrip("/") + "/chat/completions"

    def api_key(self) -> str:
        value = os.environ.get(self.api_key_env, "").strip()
        if not value:
            raise ModelProfileError("model_profile_credential_unavailable")
        return value


_DEFAULT_TASKS = {
    "requirement_merge": dict(temperature=0.1, max_tokens=1200, thinking=False, timeout_seconds=60),
    "parameter_extraction": dict(temperature=0.2, max_tokens=2000, thinking=False, timeout_seconds=90),
    "code_generation": dict(temperature=0.3, max_tokens=8192, thinking=False, timeout_seconds=120),
    "result_review": dict(temperature=0.1, max_tokens=800, thinking=False, timeout_seconds=60),
    "result_explanation": dict(temperature=0.3, max_tokens=1200, thinking=False, timeout_seconds=60),
    "retry": dict(temperature=0.2, max_tokens=8192, thinking=False, timeout_seconds=120),
}


def _url(value: object, allow_insecure_http: bool) -> str:
    if not isinstance(value, str) or len(value) > 2048:
        raise ModelProfileError("model_profile_base_url_invalid")
    parsed = urlsplit(value.strip())
    allowed_scheme = parsed.scheme == "https" or (allow_insecure_http and parsed.scheme == "http")
    if (not allowed_scheme or not parsed.hostname or parsed.username or parsed.password
            or parsed.query or parsed.fragment):
        raise ModelProfileError("model_profile_base_url_invalid")
    path = parsed.path.rstrip("/")
    if path.endswith("/chat/completions"):
        path = path[:-len("/chat/completions")]
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def _task_settings(name: str, raw: object) -> TaskSettings:
    defaults = _DEFAULT_TASKS[name]
    if raw is None:
        raw = {}
    if not isinstance(raw, dict) or not set(raw) <= set(defaults):
        raise ModelProfileError("model_profile_task_invalid")
    values = dict(defaults, **raw)
    temperature = values["temperature"]
    max_tokens = values["max_tokens"]
    thinking = values["thinking"]
    timeout_seconds = values["timeout_seconds"]
    if type(temperature) not in (int, float) or not 0 <= float(temperature) <= 2:
        raise ModelProfileError("model_profile_temperature_invalid")
    if type(max_tokens) is not int or not 1 <= max_tokens <= 32768:
        raise ModelProfileError("model_profile_max_tokens_invalid")
    if type(thinking) is not bool:
        raise ModelProfileError("model_profile_thinking_invalid")
    if type(timeout_seconds) not in (int, float) or not 1 <= float(timeout_seconds) <= 180:
        raise ModelProfileError("model_profile_timeout_invalid")
    return TaskSettings(float(temperature), max_tokens, thinking, float(timeout_seconds))


def _parse(profile_id: str, raw: object) -> ModelProfile:
    if not _ID.fullmatch(profile_id) or not isinstance(raw, dict):
        raise ModelProfileError("model_profile_id_invalid")
    allowed = {"label", "provider", "base_url", "model", "api_key_env",
               "allow_insecure_http", "tasks", "capabilities"}
    if not set(raw) <= allowed:
        raise ModelProfileError("model_profile_fields_invalid")
    label = raw.get("label", profile_id)
    provider = raw.get("provider", "openai-compatible")
    model = raw.get("model")
    api_key_env = raw.get("api_key_env")
    allow_http = raw.get("allow_insecure_http", False)
    tasks_raw = raw.get("tasks", {})
    capabilities = raw.get("capabilities", ["chat"])
    if not isinstance(label, str) or not 1 <= len(label) <= 80:
        raise ModelProfileError("model_profile_label_invalid")
    if provider != "openai-compatible":
        raise ModelProfileError("model_profile_provider_unsupported")
    if not isinstance(model, str) or not 1 <= len(model) <= 160:
        raise ModelProfileError("model_profile_model_invalid")
    if not isinstance(api_key_env, str) or not _ENV.fullmatch(api_key_env):
        raise ModelProfileError("model_profile_credential_ref_invalid")
    if type(allow_http) is not bool:
        raise ModelProfileError("model_profile_http_flag_invalid")
    if not isinstance(tasks_raw, dict) or not set(tasks_raw) <= set(TASKS):
        raise ModelProfileError("model_profile_tasks_invalid")
    if (not isinstance(capabilities, list) or not capabilities
            or any(not isinstance(item, str) or not _ID.fullmatch(item) for item in capabilities)):
        raise ModelProfileError("model_profile_capabilities_invalid")
    tasks = {name: _task_settings(name, tasks_raw.get(name)) for name in TASKS}
    return ModelProfile(profile_id, label, provider,
                        _url(raw.get("base_url"), allow_http), model, api_key_env,
                        allow_http, tasks, frozenset(capabilities))


def _default_document() -> dict:
    return {
        "deepseek-default": {
            "label": "DeepSeek（服务端）",
            "provider": "openai-compatible",
            "base_url": os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
            "model": os.environ.get("DEEPSEEK_MODEL", "deepseek-chat"),
            "api_key_env": "DEEPSEEK_API_KEY",
            "capabilities": ["chat", "json", "code"],
        }
    }


def load_profiles() -> dict[str, ModelProfile]:
    raw_text = os.environ.get("MODEL_PROFILES_JSON", "").strip()
    if raw_text:
        if len(raw_text.encode("utf-8")) > 65536:
            raise ModelProfileError("model_profiles_document_too_large")
        try:
            document = json.loads(raw_text)
        except json.JSONDecodeError as exc:
            raise ModelProfileError("model_profiles_json_invalid") from exc
    else:
        document = _default_document()
    if not isinstance(document, dict) or not 1 <= len(document) <= 16:
        raise ModelProfileError("model_profiles_document_invalid")
    return {profile_id: _parse(profile_id, raw) for profile_id, raw in document.items()}


def default_profile_id() -> str:
    profiles = load_profiles()
    selected = os.environ.get("DEFAULT_MODEL_PROFILE", "").strip()
    if selected:
        if selected not in profiles:
            raise ModelProfileError("default_model_profile_unknown")
        return selected
    return next(iter(profiles))


def resolve(profile_id: str | None, task: str) -> tuple[ModelProfile, TaskSettings]:
    if task not in TASKS:
        raise ModelProfileError("model_profile_task_unknown")
    profiles = load_profiles()
    selected = profile_id.strip() if isinstance(profile_id, str) and profile_id.strip() else default_profile_id()
    profile = profiles.get(selected)
    if profile is None:
        raise ModelProfileError("model_profile_unknown")
    if not TASK_CAPABILITIES[task] <= profile.capabilities:
        raise ModelProfileError("model_profile_capability_missing")
    profile.api_key()
    return profile, profile.tasks[task]


def public_profiles() -> dict:
    profiles = load_profiles()
    selected = default_profile_id()
    return {
        "default_profile": selected,
        "profiles": [
            {
                "id": profile.profile_id,
                "label": profile.label,
                "provider": profile.provider,
                "model": profile.model,
                "configured": bool(os.environ.get(profile.api_key_env, "").strip()),
                "tasks": list(TASKS),
                "capabilities": sorted(profile.capabilities),
            }
            for profile in profiles.values()
        ],
    }
