#!/usr/bin/env python3
"""Run a secret-safe multi-model and local/Dify acceptance matrix.

The runner keeps only compact contract evidence. Prompts, model responses,
generated code, solver output, credentials, endpoints and raw SSE traces are
never written to the result file.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import time
import urllib.parse
import uuid
from collections import Counter
from pathlib import Path
from typing import Any

import httpx

from scripts.validation import first_simulation_acceptance as first_uat
from scripts.validation import skill_reuse_live_batch as dify_batch
from scripts.validation.skill_reuse_evaluation import digest, load

SCHEMA = "acouagent-model-matrix.v1"
MAX_DIFY_BYTES = 24 * 1024 * 1024
MAX_DIFY_EVENTS = 4096
_LANE = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")
_SAFE_ERROR = re.compile(r"[a-z0-9_.:-]{1,96}")
_REVISION = re.compile(r"[0-9a-f]{7,64}")
_MODEL_IDENTITY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/+-]{0,127}")
_BYOK_AUDIT_ID = re.compile(r"byok-session:[0-9a-f]{16}")
_FORBIDDEN_RECORD_FIELDS = {
    "api_key", "code", "content", "endpoint", "prompt", "report",
    "response", "stderr", "stdout", "trace",
}
_RECORD_FIELDS = {
    "backend",
    "deterministic_gate_pass",
    "duration_ms",
    "error_code",
    "execution_pass",
    "expected_behavior",
    "lane_id",
    "model",
    "model_identity",
    "outcome",
    "profile_id",
    "prompt_sha256",
    "quality_level",
    "reference_delivered",
    "revision",
    "run_id",
    "schema",
    "task_id",
    "task_kind",
    "tasks_sha256",
    "workflow_run_id",
    "workflow_status",
}


class MatrixError(RuntimeError):
    pass


def load_tasks(path: str | Path) -> tuple[dict[str, Any], str]:
    document = load(path)
    if not isinstance(document, dict) or not isinstance(document.get("tasks"), list):
        raise MatrixError("tasks_document_invalid")
    body = {key: value for key, value in document.items() if key != "sha256"}
    if digest(body) != document.get("sha256"):
        raise MatrixError("tasks_digest_mismatch")
    tasks = document["tasks"]
    if any(not isinstance(task, dict) for task in tasks):
        raise MatrixError("tasks_contract_changed")
    counts = Counter(task.get("kind") for task in tasks if isinstance(task, dict))
    if len(tasks) != 30 or counts != {
        "exact": 15,
        "rephrase": 5,
        "outside": 5,
        "missing": 5,
    }:
        raise MatrixError("tasks_contract_changed")
    ids = [task.get("task_id") for task in tasks]
    if len(set(ids)) != 30 or any(not isinstance(item, str) for item in ids):
        raise MatrixError("task_ids_invalid")
    required = {
        "task_id", "kind", "prompt", "expected_reference", "expected_behavior"
    }
    if any(not required <= set(task) for task in tasks):
        raise MatrixError("task_fields_missing")
    return document, document["sha256"]


def _sha_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _safe_error(value: object, fallback: str) -> str:
    return value if isinstance(value, str) and _SAFE_ERROR.fullmatch(value) else fallback


def _validate_lane(lane_id: str) -> str:
    if not isinstance(lane_id, str) or not _LANE.fullmatch(lane_id):
        raise MatrixError("lane_id_invalid")
    return lane_id


def validate_local_profile(base_url: str, profile_id: str) -> dict[str, Any]:
    profiles = first_uat._request_json(
        "GET", first_uat._url(base_url, "/api/model-profiles"), None, 15
    )
    if profiles.get("orchestration_backend") != "local":
        raise MatrixError("local_backend_not_active")
    profile = next(
        (
            item for item in profiles.get("profiles", [])
            if isinstance(item, dict) and item.get("id") == profile_id
        ),
        None,
    )
    is_session = profile is None and profile_id.startswith("byok-")
    if profile is None and not is_session:
        raise MatrixError("model_profile_unavailable")
    if profile is not None and profile.get("configured") is not True:
        raise MatrixError("model_profile_unavailable")
    preflight = first_uat._request_json(
        "POST",
        first_uat._url(base_url, "/api/model-profiles/test"),
        {"profile_id": profile_id},
        45,
    )
    if preflight.get("ok") is not True:
        raise MatrixError("model_preflight_failed")
    model = profile.get("model") if profile is not None else preflight.get("model")
    record_profile_id = profile_id
    if is_session:
        record_profile_id = preflight.get("profile_id")
        if (
            not isinstance(record_profile_id, str)
            or not _BYOK_AUDIT_ID.fullmatch(record_profile_id)
        ):
            raise MatrixError("model_preflight_identity_invalid")
    return {
        "profile_id": record_profile_id,
        "model": model,
        "preflight_status": preflight.get("status"),
    }


def _execution_for(base_url: str, run_id: str) -> dict[str, Any] | None:
    for _ in range(5):
        payload = first_uat._request_json(
            "GET",
            first_uat._url(base_url, "/dashboard/api/executions?limit=500"),
            None,
            15,
        )
        execution = next(
            (
                item for item in payload.get("executions", [])
                if isinstance(item, dict) and item.get("run_id") == run_id
            ),
            None,
        )
        if execution is not None:
            return execution
        time.sleep(1)
    return None


def run_local_task(
    base_url: str,
    profile_id: str,
    lane_id: str,
    task: dict[str, Any],
    tasks_sha256: str,
    timeout: float,
    record_profile_id: str | None = None,
) -> dict[str, Any]:
    if profile_id.startswith("byok-") and (
        not isinstance(record_profile_id, str)
        or not _BYOK_AUDIT_ID.fullmatch(record_profile_id)
    ):
        raise MatrixError("byok_audit_identity_required")
    safe_profile_id = record_profile_id or profile_id
    started = time.monotonic()
    try:
        result = first_uat._request_json(
            "POST",
            first_uat._url(base_url, "/chat"),
            {
                "message": task["prompt"],
                "session_id": "matrix-" + uuid.uuid4().hex[:12],
                "model_profile": profile_id,
            },
            timeout,
        )
        workflow_status = result.get("workflow_status")
        run_id = result.get("run_id")
        execution = (
            _execution_for(base_url, run_id)
            if isinstance(run_id, str) and run_id
            else None
        )
        execution_pass = bool(
            execution
            and execution.get("exit_code") == 0
            and execution.get("timed_out") is False
        )
        quality = result.get("quality_level")
        completed = bool(
            workflow_status == "succeeded"
            and execution_pass
            and quality in {"Q1", "Q2", "Q3", "Q4"}
        )
        notices = result.get("skill_usage")
        reference = any(
            isinstance(item, dict) and item.get("stage") == "reference_delivered"
            for item in (notices if isinstance(notices, list) else [])
        )
        return {
            "schema": SCHEMA,
            "lane_id": lane_id,
            "backend": "local",
            "profile_id": safe_profile_id,
            "model": result.get("model"),
            "tasks_sha256": tasks_sha256,
            "task_id": task["task_id"],
            "task_kind": task["kind"],
            "expected_behavior": task["expected_behavior"],
            "prompt_sha256": _sha_text(task["prompt"]),
            "outcome": "completed" if completed else "failed",
            "workflow_status": workflow_status,
            "duration_ms": round((time.monotonic() - started) * 1000, 3),
            "run_id": run_id if isinstance(run_id, str) else None,
            "workflow_run_id": result.get("workflow_run_id"),
            "quality_level": quality,
            "execution_pass": execution_pass,
            "deterministic_gate_pass": completed,
            "reference_delivered": reference,
            "error_code": None if completed else _safe_error(
                result.get("error_code"), "workflow_failed"
            ),
        }
    except (first_uat.AcceptanceError, TimeoutError, OSError, ValueError, TypeError):
        return {
            "schema": SCHEMA,
            "lane_id": lane_id,
            "backend": "local",
            "profile_id": safe_profile_id,
            "model": None,
            "tasks_sha256": tasks_sha256,
            "task_id": task["task_id"],
            "task_kind": task["kind"],
            "expected_behavior": task["expected_behavior"],
            "prompt_sha256": _sha_text(task["prompt"]),
            "outcome": "failed",
            "workflow_status": "error",
            "duration_ms": round((time.monotonic() - started) * 1000, 3),
            "run_id": None,
            "workflow_run_id": None,
            "quality_level": None,
            "execution_pass": False,
            "deterministic_gate_pass": False,
            "reference_delivered": False,
            "error_code": "request_failed",
        }


def normalize_dify_record(
    compact: dict[str, Any], lane_id: str, task: dict[str, Any], tasks_sha256: str
) -> dict[str, Any]:
    completed = bool(
        compact.get("outcome") == "completed"
        and compact.get("verdict") == "normal"
        and compact.get("independent_numeric_pass") is True
    )
    return {
        "schema": SCHEMA,
        "lane_id": lane_id,
        "backend": "dify",
        "profile_id": None,
        "model": None,
        "tasks_sha256": tasks_sha256,
        "task_id": task["task_id"],
        "task_kind": task["kind"],
        "expected_behavior": task["expected_behavior"],
        "prompt_sha256": _sha_text(task["prompt"]),
        "outcome": compact.get("outcome"),
        "workflow_status": compact.get("outcome"),
        "duration_ms": compact.get("duration_ms"),
        "run_id": compact.get("run_id"),
        "workflow_run_id": compact.get("workflow_run_id"),
        "quality_level": None,
        "execution_pass": compact.get("outcome") == "completed",
        "deterministic_gate_pass": completed,
        "reference_delivered": compact.get("reference_delivered") is True,
        "error_code": (
            None if compact.get("outcome") == "completed"
            else _safe_error(compact.get("transport_error"), "workflow_failed")
        ),
    }


def run_dify_task(
    client: httpx.Client,
    base_url: str,
    api_key: str,
    lane_id: str,
    task: dict[str, Any],
    tasks_sha256: str,
    wall_timeout: float,
) -> dict[str, Any]:
    headers = {"Authorization": "Bearer " + api_key}
    user = "stage7-matrix-" + task["task_id"] + "-" + uuid.uuid4().hex[:8]
    payload = {
        "inputs": {"query": task["prompt"], "skill_reuse_mode": "on"},
        "user": user,
        "response_mode": "streaming",
    }
    events: list[dict[str, Any]] = []
    lines: list[str] = []
    total = 0
    hasher = hashlib.sha256()
    task_id = None
    transport_error = None
    started = time.monotonic()
    deadline = started + wall_timeout
    try:
        with client.stream(
            "POST", base_url.rstrip("/") + "/workflows/run",
            headers=headers, json=payload,
        ) as response:
            response.raise_for_status()
            if "text/event-stream" not in response.headers.get("content-type", ""):
                raise ValueError("invalid_stream_content_type")
            for line in response.iter_lines():
                if time.monotonic() > deadline:
                    raise TimeoutError("dify_wall_timeout")
                encoded = (line + "\n").encode("utf-8")
                total += len(encoded)
                if total > MAX_DIFY_BYTES:
                    raise ValueError("dify_response_too_large")
                hasher.update(encoded)
                if line:
                    lines.append(line)
                    continue
                value = dify_batch._frame(lines)
                lines = []
                if value is None:
                    continue
                events.append(value)
                if len(events) > MAX_DIFY_EVENTS:
                    raise ValueError("too_many_dify_events")
                task_id = value.get("task_id") or task_id
        if lines:
            value = dify_batch._frame(lines)
            if value is not None:
                events.append(value)
    except (httpx.TimeoutException, TimeoutError):
        transport_error = "timeout"
    except (httpx.HTTPError, ValueError, TypeError, UnicodeError):
        transport_error = "dify_transport_failed"
    finally:
        finished = any(event.get("event") == "workflow_finished" for event in events)
        if task_id and not finished:
            try:
                client.post(
                    base_url.rstrip("/") + "/workflows/tasks/"
                    + urllib.parse.quote(str(task_id), safe="") + "/stop",
                    headers=headers,
                    json={"user": user},
                    timeout=10,
                ).raise_for_status()
            except httpx.HTTPError:
                pass
    compact = dify_batch.compact_record(
        task,
        0,
        "on",
        tasks_sha256,
        events,
        (time.monotonic() - started) * 1000,
        hasher.hexdigest(),
        transport_error,
    )
    return normalize_dify_record(compact, lane_id, task, tasks_sha256)


def load_records(path: str | Path) -> list[dict[str, Any]]:
    target = Path(path)
    if not target.exists():
        return []
    if target.stat().st_size > 8 * 1024 * 1024:
        raise MatrixError("records_too_large")
    rows = []
    for line in target.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise MatrixError("record_invalid_json") from exc
            if not isinstance(value, dict):
                raise MatrixError("record_invalid")
            if _FORBIDDEN_RECORD_FIELDS & set(value):
                raise MatrixError("record_contains_forbidden_field")
            if set(value) - _RECORD_FIELDS:
                raise MatrixError("record_contains_unknown_field")
            if any(isinstance(item, (dict, list)) for item in value.values()):
                raise MatrixError("record_contains_nested_value")
            rows.append(value)
    keys = [(row.get("lane_id"), row.get("task_id")) for row in rows]
    if len(keys) != len(set(keys)):
        raise MatrixError("duplicate_record")
    return rows


def evaluate_matrix(
    tasks_document: dict[str, Any], tasks_sha256: str,
    records: list[dict[str, Any]], required_lanes: list[str],
) -> dict[str, Any]:
    lanes = [_validate_lane(lane) for lane in required_lanes]
    if len(lanes) < 2 or len(lanes) != len(set(lanes)):
        raise MatrixError("at_least_two_unique_lanes_required")
    tasks = {task["task_id"]: task for task in tasks_document["tasks"]}
    expected = {(lane, task_id) for lane in lanes for task_id in tasks}
    actual: dict[tuple[str, str], dict[str, Any]] = {}
    run_ids: set[str] = set()
    for row in records:
        key = (row.get("lane_id"), row.get("task_id"))
        if key not in expected or key in actual:
            raise MatrixError("unexpected_or_duplicate_record")
        if row.get("schema") != SCHEMA or row.get("tasks_sha256") != tasks_sha256:
            raise MatrixError("record_contract_mismatch")
        task = tasks[key[1]]
        if (
            row.get("task_kind") != task["kind"]
            or row.get("expected_behavior") != task["expected_behavior"]
        ):
            raise MatrixError("record_task_contract_mismatch")
        if row.get("backend") not in {"local", "dify"}:
            raise MatrixError("record_backend_invalid")
        if (
            not isinstance(row.get("model_identity"), str)
            or not _MODEL_IDENTITY.fullmatch(row["model_identity"])
        ):
            raise MatrixError("record_model_identity_missing")
        if not isinstance(row.get("revision"), str) or not _REVISION.fullmatch(row["revision"]):
            raise MatrixError("record_revision_invalid")
        if row.get("prompt_sha256") != _sha_text(tasks[key[1]]["prompt"]):
            raise MatrixError("record_prompt_mismatch")
        run_id = row.get("run_id")
        if isinstance(run_id, str) and run_id:
            if run_id in run_ids:
                raise MatrixError("duplicate_run_id")
            run_ids.add(run_id)
        actual[key] = row

    lane_metrics: dict[str, Any] = {}
    lane_pass: dict[str, bool] = {}
    positive_ids = {task_id for task_id, task in tasks.items() if task["expected_reference"]}
    negative_ids = set(tasks) - positive_ids
    missing_ids = {task_id for task_id, task in tasks.items() if task["kind"] == "missing"}
    for lane in lanes:
        rows = {task_id: actual[(lane, task_id)] for task_id in tasks if (lane, task_id) in actual}
        revisions = {row["revision"] for row in rows.values()}
        models = {row["model_identity"] for row in rows.values()}
        backends = {row["backend"] for row in rows.values()}
        positive_pass = sum(
            rows[task_id].get("deterministic_gate_pass") is True
            and rows[task_id].get("reference_delivered") is True
            for task_id in positive_ids if task_id in rows
        )
        negative_references = sum(
            rows[task_id].get("reference_delivered") is True
            for task_id in negative_ids if task_id in rows
        )
        missing_protected = sum(
            rows[task_id].get("outcome") != "completed"
            and rows[task_id].get("reference_delivered") is not True
            for task_id in missing_ids if task_id in rows
        )
        metrics = {
            "observed": len(rows),
            "positive_pass_count": positive_pass,
            "positive_total": len(positive_ids),
            "positive_pass_rate": positive_pass / len(positive_ids),
            "negative_reference_count": negative_references,
            "missing_protected_count": missing_protected,
            "missing_total": len(missing_ids),
            "revisions": sorted(revisions),
            "model_identities": sorted(models),
            "backends": sorted(backends),
        }
        lane_metrics[lane] = metrics
        lane_pass[lane] = bool(
            len(rows) == len(tasks)
            and len(revisions) == 1
            and len(models) == 1
            and len(backends) == 1
            and metrics["positive_pass_rate"] >= 0.95
            and negative_references == 0
            and missing_protected == len(missing_ids)
        )

    mismatches = []
    anchor = lanes[0]
    for lane in lanes[1:]:
        for task_id, task in tasks.items():
            left = actual.get((anchor, task_id))
            right = actual.get((lane, task_id))
            if left is None or right is None:
                continue
            if task["expected_reference"]:
                left_value = (
                    left.get("deterministic_gate_pass") is True,
                    left.get("reference_delivered") is True,
                )
                right_value = (
                    right.get("deterministic_gate_pass") is True,
                    right.get("reference_delivered") is True,
                )
            else:
                left_value = (
                    left.get("reference_delivered") is True,
                    left.get("outcome") == "completed" if task["kind"] == "missing" else None,
                )
                right_value = (
                    right.get("reference_delivered") is True,
                    right.get("outcome") == "completed" if task["kind"] == "missing" else None,
                )
            if left_value != right_value:
                mismatches.append({"task_id": task_id, "lanes": [anchor, lane]})

    missing = sorted(expected - set(actual))
    targets_met = not missing and all(lane_pass.values()) and not mismatches
    report = {
        "schema": "acouagent-model-matrix-report.v1",
        "tasks_sha256": tasks_sha256,
        "required_lanes": lanes,
        "complete": not missing,
        "missing_count": len(missing),
        "missing": [list(item) for item in missing[:100]],
        "lane_metrics": lane_metrics,
        "lane_pass": lane_pass,
        "cross_lane_mismatches": mismatches,
        "targets_met": targets_met,
        "formal_acceptance": False,
        "review_required": (
            "Verify deployment identity, model identities, Dify graph revision and run provenance; "
            "compact records deliberately omit prompts, responses, code and raw traces."
        ),
    }
    report["report_sha256"] = digest(report)
    return report


def _selected_tasks(
    document: dict[str, Any], ids: list[str], limit: int | None
) -> list[dict[str, Any]]:
    tasks = document["tasks"]
    if ids:
        unknown = set(ids) - {task["task_id"] for task in tasks}
        if unknown:
            raise MatrixError("unknown_task_id")
        tasks = [task for task in tasks if task["task_id"] in set(ids)]
    if limit is not None:
        if limit < 1:
            raise MatrixError("limit_invalid")
        tasks = tasks[:limit]
    return tasks


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    run = subparsers.add_parser("run")
    run.add_argument("--backend", choices=("local", "dify"), required=True)
    run.add_argument("--lane", required=True)
    run.add_argument("--profile")
    run.add_argument("--model-label")
    run.add_argument("--revision", required=True)
    run.add_argument("--tasks-file", default="docs/validation/stage1/tasks.json")
    run.add_argument("--output", required=True, type=Path)
    run.add_argument("--base-url", default="http://127.0.0.1:8001")
    run.add_argument("--dify-base-url", default=os.environ.get("DIFY_BASE_URL", "http://nginx/v1"))
    run.add_argument("--timeout", type=float, default=360.0)
    run.add_argument("--task-id", action="append", default=[])
    run.add_argument("--limit", type=int)
    run.add_argument("--dry-run", action="store_true")

    evaluate = subparsers.add_parser("evaluate")
    evaluate.add_argument("--tasks-file", default="docs/validation/stage1/tasks.json")
    evaluate.add_argument("--records", required=True)
    evaluate.add_argument("--require-lane", action="append", required=True)
    evaluate.add_argument("--output", type=Path)
    args = parser.parse_args(argv)

    document, tasks_sha256 = load_tasks(args.tasks_file)
    if args.command == "evaluate":
        report = evaluate_matrix(
            document, tasks_sha256, load_records(args.records), args.require_lane
        )
        encoded = json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(encoded, encoding="utf-8")
        print(encoded, end="")
        return 0 if report["targets_met"] else 1

    lane_id = _validate_lane(args.lane)
    revision = args.revision.lower()
    if not _REVISION.fullmatch(revision):
        raise MatrixError("revision_invalid")
    if not math.isfinite(args.timeout) or not 1 <= args.timeout <= 1800:
        raise MatrixError("timeout_invalid")
    selected = _selected_tasks(document, args.task_id, args.limit)
    rows = load_records(args.output)
    if any(
        row.get("schema") != SCHEMA or row.get("tasks_sha256") != tasks_sha256
        for row in rows
    ):
        raise MatrixError("existing_records_contract_mismatch")
    complete = {(row["lane_id"], row["task_id"]) for row in rows}
    pending = [task for task in selected if (lane_id, task["task_id"]) not in complete]
    if args.dry_run:
        print(json.dumps({
            "lane_id": lane_id,
            "backend": args.backend,
            "tasks_sha256": tasks_sha256,
            "pending": [task["task_id"] for task in pending],
        }, ensure_ascii=False, sort_keys=True))
        return 0

    if args.backend == "local":
        if not args.profile:
            raise MatrixError("profile_required")
        profile_meta = validate_local_profile(args.base_url, args.profile)
        model_identity = args.model_label or profile_meta.get("model")
        def run_local_selected(task: dict[str, Any]) -> dict[str, Any]:
            return run_local_task(
                args.base_url, args.profile, lane_id, task, tasks_sha256, args.timeout,
                record_profile_id=profile_meta["profile_id"],
            )

        runner = run_local_selected
        client = None
    else:
        api_key = os.environ.get("DIFY_API_KEY", "")
        if not api_key:
            raise MatrixError("DIFY_API_KEY_required")
        model_identity = args.model_label
        if not model_identity:
            raise MatrixError("model_label_required")
        client = httpx.Client(
            timeout=httpx.Timeout(connect=10, read=args.timeout, write=30, pool=10),
            follow_redirects=False,
        )
        def run_dify_selected(task: dict[str, Any]) -> dict[str, Any]:
            return run_dify_task(
                client, args.dify_base_url, api_key, lane_id, task, tasks_sha256,
                args.timeout,
            )

        runner = run_dify_selected

    if (
        not isinstance(model_identity, str)
        or not _MODEL_IDENTITY.fullmatch(model_identity.strip())
    ):
        if client is not None:
            client.close()
        raise MatrixError("model_identity_invalid")
    model_identity = model_identity.strip()
    lane_rows = [row for row in rows if row.get("lane_id") == lane_id]
    if any(
        row.get("model_identity") != model_identity or row.get("revision") != revision
        for row in lane_rows
    ):
        if client is not None:
            client.close()
        raise MatrixError("existing_lane_identity_mismatch")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    try:
        with args.output.open("a", encoding="utf-8") as writer:
            for task in pending:
                row = runner(task)
                row["model_identity"] = model_identity
                row["revision"] = revision
                encoded = json.dumps(
                    row, ensure_ascii=False, sort_keys=True, allow_nan=False
                )
                writer.write(encoded + "\n")
                writer.flush()
                os.fsync(writer.fileno())
                print(json.dumps({
                    "lane_id": lane_id,
                    "task_id": task["task_id"],
                    "outcome": row["outcome"],
                    "deterministic_gate_pass": row["deterministic_gate_pass"],
                    "reference_delivered": row["reference_delivered"],
                }, ensure_ascii=False, sort_keys=True), flush=True)
    finally:
        if client is not None:
            client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
