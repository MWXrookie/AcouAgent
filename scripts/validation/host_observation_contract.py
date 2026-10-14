"""Offline format/correlation checks only; never authenticates a host.

Expected fields must come from operator/server-owned dispatch context, not the
observation itself. Even a wholly forged matching document remains untrusted.
No HTTP/MCP writer, Docker access, credential handling or grade mutation.
"""
import json
import re

VERSION = "host-observation-offline.v1"
FIELDS = {"version", "run_id", "request_nonce", "container_id", "image_id",
          "container_started_ns", "window_start_ns", "window_end_ns",
          "observed_before_ns", "observed_after_ns"}
CONTEXT_FIELDS = FIELDS - {"version", "observed_before_ns", "observed_after_ns"}
MAX_BYTES = 8192
MAX_WINDOW_NS = 60_000_000_000
MAX_AGE_NS = 300_000_000_000
MAX_TIME_NS = 9223372036854775807


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_key")
        result[key] = value
    return result


def _constant(value):
    raise ValueError("nonfinite_json")


def _valid_context(context):
    if type(context) is not dict or set(context) != CONTEXT_FIELDS:
        return False
    for key, pattern in (("run_id", r"[A-Za-z0-9-]{1,128}"),
                         ("request_nonce", r"[0-9a-f]{32}"),
                         ("container_id", r"[0-9a-f]{64}"),
                         ("image_id", r"sha256:[0-9a-f]{64}")):
        if not isinstance(context[key], str) or not re.fullmatch(pattern, context[key]):
            return False
    for key in ("container_started_ns", "window_start_ns", "window_end_ns"):
        if type(context[key]) is not int or not 0 < context[key] <= MAX_TIME_NS:
            return False
    return (context["container_started_ns"] <= context["window_start_ns"]
            < context["window_end_ns"]
            <= context["window_start_ns"] + MAX_WINDOW_NS)


def inspect_observation(raw, *, expected, now_ns):
    """Reject mismatched/stale replay, not same-run diagnostic rereads.

    No one-time consumption or authenticated replay journal is claimed.
    Clock domain is explicit UTC Unix nanoseconds; no clock skew is inferred.
    """
    denied = {"format_matched": False, "context_matched": False,
              "authenticated": False, "trusted_run": False,
              "runtime_approved": False, "run_bound": False,
              "parameters_bound": False, "reason": "invalid_observation"}
    try:
        if not _valid_context(expected):
            return dict(denied, reason="invalid_expected_context")
        if type(now_ns) is not int or not 0 < now_ns <= MAX_TIME_NS:
            return dict(denied, reason="invalid_clock")
        if not isinstance(raw, (str, bytes)) or len(raw) > MAX_BYTES:
            return dict(denied, reason="observation_size_limit")
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="strict")
        if len(raw.encode("utf-8")) > MAX_BYTES:
            return dict(denied, reason="observation_size_limit")
        document = json.loads(raw, object_pairs_hook=_object, parse_constant=_constant)
        if type(document) is not dict or set(document) != FIELDS or document["version"] != VERSION:
            return denied
        context = {k: document[k] for k in CONTEXT_FIELDS}
        if not _valid_context(context):
            return denied
        for key in ("observed_before_ns", "observed_after_ns"):
            if type(document[key]) is not int or not 0 < document[key] <= MAX_TIME_NS:
                return denied
        if not (context["container_started_ns"] <= document["observed_before_ns"]
                <= context["window_start_ns"] < context["window_end_ns"]
                <= document["observed_after_ns"] <= now_ns):
            return dict(denied, reason="invalid_observation_window")
        if document["observed_after_ns"] - document["observed_before_ns"] > MAX_WINDOW_NS:
            return dict(denied, reason="observation_window_too_wide")
        if now_ns - document["observed_before_ns"] > MAX_AGE_NS:
            return dict(denied, reason="stale_observation")
        if any(type(context[k]) is not type(expected[k]) or context[k] != expected[k] for k in CONTEXT_FIELDS):
            return dict(denied, format_matched=True, reason="dispatch_context_mismatch")
        return dict(denied, format_matched=True, context_matched=True,
                    reason="offline_correlation_only")
    except (ValueError, TypeError, UnicodeError, RecursionError, OverflowError):
        return denied


def inspect_snapshot_pair(before, after, *, expected, observed_before_ns,
                          observed_after_ns, now_ns):
    """Adapt minimal Docker lifecycle snapshots without collecting or executing.

    Request identity is from independent expected context, not observed by this
    adapter. It can reject lifecycle drift but cannot bind an actual request.
    """
    from datetime import datetime, timezone
    from scripts.validation import executor_host_observation as collector
    denied = {"format_matched": False, "context_matched": False,
              "authenticated": False, "trusted_run": False,
              "runtime_approved": False, "run_bound": False,
              "parameters_bound": False, "request_observed": False,
              "reason": "invalid_snapshot_pair"}
    try:
        if not _valid_context(expected):
            return dict(denied, reason="invalid_expected_context")
        if not collector._valid(before) or not collector._valid(after):
            return denied
        if before != after:
            return dict(denied, reason="container_lifecycle_changed")
        # datetime.fromisoformat truncates sub-microsecond precision. Parse the
        # whole UTC second independently and append the exact fractional ns.
        match = re.fullmatch(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d{1,9}))?Z",
                             before["started_at"])
        whole = datetime.strptime(match[1], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
        delta = whole - datetime(1970, 1, 1, tzinfo=timezone.utc)
        started_ns = (delta.days * 86400 + delta.seconds) * 1_000_000_000 + int((match[2] or "").ljust(9, "0"))
        document = {**expected, "version": VERSION,
                    "container_id": before["container_id"], "image_id": before["image_id"],
                    "container_started_ns": started_ns, "observed_before_ns": observed_before_ns,
                    "observed_after_ns": observed_after_ns}
        result = inspect_observation(json.dumps(document), expected=expected, now_ns=now_ns)
        return dict(result, request_observed=False)
    except (ValueError, TypeError, KeyError, IndexError, AttributeError, OverflowError):
        return denied
