#!/usr/bin/env python3
"""Run and record the first local natural-language simulation acceptance."""

from __future__ import annotations

import argparse
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

MAX_RESPONSE_BYTES = 2 * 1024 * 1024
DEFAULT_PROMPT = (
    "仿真二维水中点源：64×64 网格，dx=0.5 mm，频率100 kHz，"
    "时长20 us，CFL=0.3，PML=10。"
)


class AcceptanceError(RuntimeError):
    pass


def _url(base_url: str, path: str) -> str:
    base_url = base_url.rstrip("/") + "/"
    parsed = urllib.parse.urlparse(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise AcceptanceError("invalid_base_url")
    return urllib.parse.urljoin(base_url, path.lstrip("/"))


def _request_json(
    method: str,
    url: str,
    payload: dict[str, Any] | None,
    timeout: float,
) -> dict[str, Any]:
    body = None
    headers = {"Accept": "application/json", "User-Agent": "AcouAgent-UAT/1"}
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            if response.status != 200:
                raise AcceptanceError(f"http_status_{response.status}")
            data = response.read(MAX_RESPONSE_BYTES + 1)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise AcceptanceError("request_failed") from exc
    if len(data) > MAX_RESPONSE_BYTES:
        raise AcceptanceError("response_too_large")
    try:
        decoded = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AcceptanceError("invalid_json_response") from exc
    if not isinstance(decoded, dict):
        raise AcceptanceError("invalid_response_shape")
    return decoded


def evaluate(
    base_url: str,
    requested_profile: str | None,
    prompt: str,
    timeout: float,
) -> dict[str, Any]:
    profiles = _request_json("GET", _url(base_url, "/api/model-profiles"), None, 15)
    if profiles.get("orchestration_backend") != "local":
        raise AcceptanceError("local_backend_not_active")
    available = {
        item.get("id") for item in profiles.get("profiles", []) if isinstance(item, dict)
    }
    profile = requested_profile or profiles.get("default_profile")
    if not isinstance(profile, str) or not profile or profile not in available:
        raise AcceptanceError("model_profile_unavailable")

    started = time.monotonic()
    result = _request_json(
        "POST",
        _url(base_url, "/chat"),
        {
            "message": prompt,
            "session_id": "first-uat-" + uuid.uuid4().hex[:10],
            "model_profile": profile,
        },
        timeout,
    )
    duration = time.monotonic() - started
    if result.get("workflow_status") != "succeeded":
        error_code = result.get("error_code")
        suffix = f":{error_code}" if isinstance(error_code, str) else ""
        raise AcceptanceError("workflow_not_succeeded" + suffix)
    if result.get("orchestration_backend") != "local":
        raise AcceptanceError("unexpected_orchestration_backend")
    run_id = result.get("run_id")
    try:
        uuid.UUID(str(run_id))
    except (ValueError, TypeError, AttributeError) as exc:
        raise AcceptanceError("invalid_run_id") from exc
    quality = result.get("quality_level")
    if quality not in {"Q1", "Q2", "Q3", "Q4"}:
        raise AcceptanceError("quality_gate_not_passed")

    execution = None
    for _ in range(5):
        dashboard = _request_json(
            "GET", _url(base_url, "/dashboard/api/executions?limit=100"), None, 15
        )
        execution = next(
            (
                item
                for item in dashboard.get("executions", [])
                if isinstance(item, dict) and item.get("run_id") == run_id
            ),
            None,
        )
        if execution is not None:
            break
        time.sleep(1)
    if execution is None:
        raise AcceptanceError("execution_record_missing")
    if execution.get("exit_code") != 0 or bool(execution.get("timed_out")):
        raise AcceptanceError("execution_not_successful")

    return {
        "schema_version": 1,
        "accepted": True,
        "checked_at_utc": datetime.now(timezone.utc).isoformat(),
        "duration_seconds": round(duration, 3),
        "workflow_status": "succeeded",
        "orchestration_backend": "local",
        "model_profile": profile,
        "model": result.get("model"),
        "quality_level": quality,
        "run_id": run_id,
        "request_id": result.get("request_id"),
        "workflow_run_id": result.get("workflow_run_id"),
        "execution": {
            "id": execution.get("id"),
            "exit_code": execution.get("exit_code"),
            "timed_out": bool(execution.get("timed_out")),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-url", default=os.environ.get("GW_BASE_URL", "http://127.0.0.1:8001")
    )
    parser.add_argument("--profile", default=os.environ.get("ACOUAGENT_MODEL_PROFILE") or None)
    parser.add_argument("--prompt", default=DEFAULT_PROMPT)
    parser.add_argument("--timeout", type=float, default=360.0)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        receipt = evaluate(args.base_url, args.profile, args.prompt, args.timeout)
    except AcceptanceError as exc:
        print(json.dumps({"accepted": False, "error": str(exc)}, ensure_ascii=False))
        return 1
    encoded = json.dumps(receipt, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
    print(encoded, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
