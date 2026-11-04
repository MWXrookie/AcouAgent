"""Bounded client for the gateway-owned jwave_flow skill port."""
from __future__ import annotations

import json
import urllib.error
import urllib.request
import uuid
from typing import Any
from urllib.parse import urlparse

MAX_RESPONSE_BYTES = 2 * 1024 * 1024


class SkillClientError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class SkillClient:
    def __init__(self, base_url: str, token: str, timeout: float = 2.0):
        parsed = urlparse(base_url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("invalid_skill_port_url")
        if len(token) < 32:
            raise ValueError("skill_port_token_too_short")
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout
        self._opener = urllib.request.build_opener(_NoRedirect)

    def _post(self, path: str, payload: dict[str, Any], *, timeout: float) -> dict[str, Any]:
        body = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        request = urllib.request.Request(
            self.base_url + path,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "X-Jwave-Flow-Skill-Token": self.token,
            },
        )
        try:
            with self._opener.open(request, timeout=timeout) as response:
                if response.geturl() != request.full_url:
                    raise SkillClientError("skill_port_redirect")
                raw = response.read(MAX_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as exc:
            raise SkillClientError(f"skill_port_http_{exc.code}") from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise SkillClientError("skill_port_unavailable") from None
        if len(raw) > MAX_RESPONSE_BYTES:
            raise SkillClientError("skill_port_response_too_large")
        try:
            value = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeError):
            raise SkillClientError("skill_port_invalid_response") from None
        if not isinstance(value, dict):
            raise SkillClientError("skill_port_invalid_response")
        return value

    def resolve(
        self,
        *,
        workflow_run_id: str,
        original_context: str,
        scenario_type: str,
        parameters_json: str,
    ) -> dict[str, Any]:
        result = self._post(
            "/resolve",
            {
                "request_id": str(uuid.uuid4()),
                "workflow_run_id": workflow_run_id,
                "original_context": original_context,
                "scenario_type": scenario_type,
                "parameters_json": parameters_json,
            },
            timeout=self.timeout,
        )
        mode = result.get("mode")
        if mode not in {"reference", "guided", "fallback"}:
            raise SkillClientError("skill_port_invalid_mode")
        if mode == "reference" and not isinstance(result.get("skill_ticket"), str):
            raise SkillClientError("skill_port_invalid_ticket")
        if mode == "guided" and not isinstance(result.get("guidance_context"), str):
            raise SkillClientError("skill_port_invalid_guidance")
        return result

    def execute_reference(
        self,
        *,
        workflow_run_id: str,
        skill_ticket: str,
        original_context: str,
        scenario_type: str,
        parameters_json: str,
    ) -> dict[str, Any]:
        result = self._post(
            "/execute-reference",
            {
                "skill_ticket": skill_ticket,
                "workflow_run_id": workflow_run_id,
                "original_context": original_context,
                "scenario_type": scenario_type,
                "parameters_json": parameters_json,
            },
            timeout=max(40.0, self.timeout),
        )
        if not isinstance(result.get("execution_reuse_verified"), bool):
            raise SkillClientError("skill_port_invalid_execution")
        return result


__all__ = ["SkillClient", "SkillClientError"]
