"""OpenAI-compatible model profiles for self-hosted BYOK deployments.

Operators may define reusable profiles through environment variables. Portal users
may also register an in-memory, expiring profile whose secret is never persisted or
returned. Both paths resolve to the same bounded model transport.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import threading
import time
from dataclasses import dataclass, field
from urllib.parse import urlsplit, urlunsplit

from app.network_security import UnsafeOutboundUrl, validate_public_https_url

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
_MODEL = re.compile(r"[^\s\x00-\x1f]{1,160}")
_SESSION_PREFIX = "byok-"
_SESSION_LIMIT = 128


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
    api_key_env: str | None
    allow_insecure_http: bool
    tasks: dict[str, TaskSettings]
    capabilities: frozenset[str]
    api_key_value: str | None = field(default=None, repr=False, compare=False)

    def endpoint(self) -> str:
        return self.base_url.rstrip("/") + "/chat/completions"

    def api_key(self) -> str:
        value = self.api_key_value
        if value is None and self.api_key_env:
            value = os.environ.get(self.api_key_env, "").strip()
        if not value:
            raise ModelProfileError("model_profile_credential_unavailable")
        return value

    def audit_profile_id(self) -> str:
        if self.api_key_value is None:
            return self.profile_id
        digest = hashlib.sha256(self.profile_id.encode("utf-8")).hexdigest()[:16]
        return f"byok-session:{digest}"


@dataclass(frozen=True)
class _SessionProfile:
    profile: ModelProfile
    expires_at: float


_DEFAULT_TASKS = {
    "requirement_merge": dict(temperature=0.1, max_tokens=1200, thinking=False, timeout_seconds=60),
    "parameter_extraction": dict(
        temperature=0.2, max_tokens=2000, thinking=False, timeout_seconds=90
    ),
    "code_generation": dict(temperature=0.3, max_tokens=8192, thinking=False, timeout_seconds=120),
    "result_review": dict(temperature=0.1, max_tokens=800, thinking=False, timeout_seconds=60),
    "result_explanation": dict(
        temperature=0.3, max_tokens=1200, thinking=False, timeout_seconds=60
    ),
    "retry": dict(temperature=0.2, max_tokens=8192, thinking=False, timeout_seconds=120),
}
_session_profiles: dict[str, _SessionProfile] = {}
_session_lock = threading.Lock()


def byok_session_ttl_seconds() -> int:
    try:
        value = int(os.environ.get("BYOK_SESSION_TTL_SECONDS", "43200"))
    except ValueError as exc:
        raise ModelProfileError("byok_session_ttl_invalid") from exc
    if not 300 <= value <= 86400:
        raise ModelProfileError("byok_session_ttl_invalid")
    return value


def private_byok_endpoints_allowed() -> bool:
    return os.environ.get("BYOK_ALLOW_PRIVATE_ENDPOINTS", "").strip().lower() == "true"


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


def _official_deepseek(base_url: str) -> bool:
    parsed = urlsplit(base_url)
    return parsed.scheme == "https" and parsed.hostname == "api.deepseek.com"


def _task_settings(name: str, raw: object, *, reasoner: bool = False) -> TaskSettings:
    defaults = _DEFAULT_TASKS[name]
    if reasoner:
        # Thinking and final output share this budget. Explicit task limits win.
        defaults = dict(defaults, max_tokens=max(4096, defaults["max_tokens"]), thinking=True)
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
    if not isinstance(model, str) or not _MODEL.fullmatch(model):
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
    base_url = _url(raw.get("base_url"), allow_http)
    reasoner = _official_deepseek(base_url) and model == "deepseek-reasoner"
    tasks = {name: _task_settings(name, tasks_raw.get(name), reasoner=reasoner) for name in TASKS}
    return ModelProfile(profile_id, label, provider,
                        base_url, model, api_key_env,
                        allow_http, tasks, frozenset(capabilities))


def _environment_document() -> dict:
    base_url = os.environ.get("MODEL_BASE_URL", "").strip()
    model = os.environ.get("MODEL_NAME", "").strip()
    if not base_url and not model:
        return {}
    return {
        "environment-default": {
            "label": os.environ.get("MODEL_LABEL", "环境变量模型").strip() or "环境变量模型",
            "provider": "openai-compatible",
            "base_url": base_url,
            "model": model,
            "api_key_env": "MODEL_API_KEY",
            "allow_insecure_http": (
                os.environ.get("MODEL_ALLOW_INSECURE_HTTP", "").lower() == "true"
            ),
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
        document = _environment_document()
    if not isinstance(document, dict) or len(document) > 16:
        raise ModelProfileError("model_profiles_document_invalid")
    return {profile_id: _parse(profile_id, raw) for profile_id, raw in document.items()}


def default_profile_id() -> str | None:
    profiles = load_profiles()
    selected = os.environ.get("DEFAULT_MODEL_PROFILE", "").strip()
    if selected:
        if selected not in profiles:
            raise ModelProfileError("default_model_profile_unknown")
        return selected
    return next(iter(profiles), None)


def _cleanup_sessions(now: float) -> None:
    expired = [profile_id for profile_id, entry in _session_profiles.items()
               if entry.expires_at <= now]
    for profile_id in expired:
        _session_profiles.pop(profile_id, None)
    while len(_session_profiles) >= _SESSION_LIMIT:
        oldest = min(_session_profiles, key=lambda key: _session_profiles[key].expires_at)
        _session_profiles.pop(oldest, None)


def register_session_profile(payload: object) -> dict[str, object]:
    """Register one ephemeral BYOK profile and return only its opaque handle."""
    allowed = {"label", "base_url", "model", "api_key"}
    if not isinstance(payload, dict) or not set(payload) <= allowed:
        raise ModelProfileError("byok_payload_invalid")
    label = payload.get("label", "我的模型")
    base_url = payload.get("base_url")
    model = payload.get("model")
    api_key = payload.get("api_key")
    if not isinstance(label, str) or not 1 <= len(label.strip()) <= 80:
        raise ModelProfileError("model_profile_label_invalid")
    if not isinstance(model, str) or not _MODEL.fullmatch(model.strip()):
        raise ModelProfileError("model_profile_model_invalid")
    if not isinstance(api_key, str) or not 1 <= len(api_key.strip()) <= 4096:
        raise ModelProfileError("model_profile_credential_unavailable")
    allow_private = private_byok_endpoints_allowed()
    normalized_url = _url(base_url, allow_private)
    if not allow_private:
        try:
            normalized_url = validate_public_https_url(normalized_url)
        except UnsafeOutboundUrl as exc:
            raise ModelProfileError("byok_endpoint_unsafe") from exc
    profile_id = _SESSION_PREFIX + secrets.token_urlsafe(24)
    now = time.monotonic()
    ttl = byok_session_ttl_seconds()
    profile = ModelProfile(
        profile_id=profile_id,
        label=label.strip(),
        provider="openai-compatible",
        base_url=normalized_url,
        model=model.strip(),
        api_key_env=None,
        allow_insecure_http=allow_private,
        tasks={name: _task_settings(name, None, reasoner=(
            _official_deepseek(normalized_url) and model.strip() == "deepseek-reasoner")) for name in TASKS},
        capabilities=frozenset({"chat", "json", "code"}),
        api_key_value=api_key.strip(),
    )
    with _session_lock:
        _cleanup_sessions(now)
        _session_profiles[profile_id] = _SessionProfile(profile, now + ttl)
    return {
        "profile_id": profile_id,
        "label": profile.label,
        "model": profile.model,
        "expires_in_seconds": ttl,
    }


def remove_session_profile(profile_id: object) -> bool:
    if not isinstance(profile_id, str) or not profile_id.startswith(_SESSION_PREFIX):
        return False
    with _session_lock:
        return _session_profiles.pop(profile_id, None) is not None


def _resolve_session(profile_id: str) -> ModelProfile:
    now = time.monotonic()
    with _session_lock:
        _cleanup_sessions(now)
        entry = _session_profiles.get(profile_id)
    if entry is None:
        raise ModelProfileError("byok_session_missing_or_expired")
    return entry.profile


def resolve(profile_id: str | None, task: str) -> tuple[ModelProfile, TaskSettings]:
    if task not in TASKS:
        raise ModelProfileError("model_profile_task_unknown")
    selected = profile_id.strip() if isinstance(profile_id, str) and profile_id.strip() else default_profile_id()
    if selected is None:
        raise ModelProfileError("model_profile_required")
    profile = (_resolve_session(selected) if selected.startswith(_SESSION_PREFIX)
               else load_profiles().get(selected))
    if profile is None:
        raise ModelProfileError("model_profile_unknown")
    if not TASK_CAPABILITIES[task] <= profile.capabilities:
        raise ModelProfileError("model_profile_capability_missing")
    profile.api_key()
    return profile, profile.tasks[task]


def validate_runtime_endpoint(profile: ModelProfile) -> None:
    """Recheck a public session endpoint immediately before each outbound call."""
    if profile.api_key_value is not None and not profile.allow_insecure_http:
        try:
            validate_public_https_url(profile.base_url)
        except UnsafeOutboundUrl as exc:
            raise ModelProfileError("byok_endpoint_unsafe") from exc


def public_profiles() -> dict:
    profiles = load_profiles()
    selected = default_profile_id()
    return {
        "default_profile": selected,
        "byok": {
            "enabled": True,
            "session_ttl_seconds": byok_session_ttl_seconds(),
            "private_endpoints_allowed": private_byok_endpoints_allowed(),
        },
        "profiles": [
            {
                "id": profile.profile_id,
                "label": profile.label,
                "provider": profile.provider,
                "model": profile.model,
                "configured": bool(profile.api_key_env and os.environ.get(profile.api_key_env, "").strip()),
                "tasks": list(TASKS),
                "capabilities": sorted(profile.capabilities),
            }
            for profile in profiles.values()
        ],
    }
