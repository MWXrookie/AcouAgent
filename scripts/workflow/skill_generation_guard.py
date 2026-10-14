"""Portable Dify code-node guard proposal; not deployed.

Pure function: no imports outside stdlib, network, code execution, credentials,
storage or model calls. Tool JSON is untrusted and never grants injection.
Positive provenance consumption is not implemented, so output stays original.
"""
import json

REASONS = {"retrieval_disabled", "no_applicable_skill", "skill_store_unavailable",
           "provenance_authentication_unavailable", "retrieval_timeout",
           "retrieval_failed"}
MAX_RESPONSE_BYTES = 8192


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_key")
        result[key] = value
    return result


def _nonfinite(value):
    raise ValueError("nonfinite")


def main(original_context: str, retrieval_result_json: str = "",
         retrieval_enabled: bool = False, injection_enabled: bool = False) -> dict:
    if not isinstance(original_context, str):
        raise ValueError("original_context_must_be_text")
    output = {"generation_context": original_context, "skill_injected": False,
              "referenced_skill_ids": [], "fallback_reason": "injection_disabled"}
    # Exact booleans: user strings such as "false" must never enable a branch.
    if injection_enabled is not True:
        return output
    if retrieval_enabled is not True:
        return dict(output, fallback_reason="retrieval_disabled")
    try:
        if (not isinstance(retrieval_result_json, str)
                or len(retrieval_result_json) > MAX_RESPONSE_BYTES
                or len(retrieval_result_json.encode("utf-8")) > MAX_RESPONSE_BYTES):
            return dict(output, fallback_reason="invalid_retrieval_response")
        response = json.loads(retrieval_result_json, object_pairs_hook=_unique,
                              parse_constant=_nonfinite)
        expected = {"skills", "fallback", "trusted_run", "runtime_approved",
                    "parameters_bound", "run_bound", "reason"}
        if type(response) is not dict or set(response) != expected:
            return dict(output, fallback_reason="invalid_retrieval_response")
        if (response["skills"] != [] or type(response["skills"]) is not list
                or response["fallback"] is not True
                or any(response[k] is not False for k in
                       ("trusted_run", "runtime_approved", "parameters_bound", "run_bound"))):
            return dict(output, fallback_reason="positive_consumption_unavailable")
        reason = response["reason"]
        if not isinstance(reason, str) or reason not in REASONS:
            return dict(output, fallback_reason="invalid_retrieval_response")
        return dict(output, fallback_reason=reason)
    except (ValueError, TypeError, UnicodeError, RecursionError):
        return dict(output, fallback_reason="invalid_retrieval_response")
