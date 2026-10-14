"""Correlate the internal executor response; never grants physical trust.

Endpoint/network, gateway writes and DB remain trust assumptions. No signatures
or image attestation are claimed. Sandbox stdout cannot supply this envelope.
"""
import hashlib
import json

LIBRARIES = {"jwave", "jax", "jaxlib", "jaxdf", "numpy"}


def sha256(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _hash(value):
    return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


def verify_executor_receipt(receipt, result, code, timeout_seconds, nonce):
    evidence = {"matched": False, "scope": "internal_executor_response",
                "runtime_approved": False, "trusted_run": False}
    try:
        if not isinstance(receipt, dict) or receipt.get("version") != "executor-receipt.v1":
            return dict(evidence, reason="missing_or_legacy_executor_receipt")
        if set(receipt) != {"version", "request_nonce", "code_sha256", "stdout_sha256", "stderr_sha256", "exit_code", "timed_out", "timeout_seconds", "runtime", "runtime_sha256"}:
            return dict(evidence, reason="invalid_executor_receipt")
        expected = {"request_nonce": nonce, "code_sha256": sha256(code),
                    "stdout_sha256": sha256(result["stdout"]), "stderr_sha256": sha256(result["stderr"]),
                    "exit_code": result["exit_code"], "timed_out": result["timed_out"],
                    "timeout_seconds": timeout_seconds}
        # Exact types: bool must not impersonate exit 0/timeout 1.
        for key, value in expected.items():
            if type(receipt.get(key)) is not type(value) or receipt[key] != value:
                return dict(evidence, reason="executor_receipt_mismatch")
        if (not isinstance(nonce, str) or len(nonce) != 32
                or any(c not in "0123456789abcdef" for c in nonce)
                or type(result["exit_code"]) is not int or type(result["timed_out"]) is not bool):
            return dict(evidence, reason="invalid_executor_response")
        runtime = receipt["runtime"]
        if not isinstance(runtime, dict) or set(runtime) != {"executor_source_sha256", "python_version", "python_implementation", "machine", "library_versions"}:
            return dict(evidence, reason="invalid_runtime_identity")
        if not _hash(runtime["executor_source_sha256"]):
            return dict(evidence, reason="invalid_runtime_identity")
        for key in ("python_version", "python_implementation", "machine"):
            if not isinstance(runtime[key], str) or not 1 <= len(runtime[key]) <= 80:
                return dict(evidence, reason="invalid_runtime_identity")
        versions = runtime["library_versions"]
        if (not isinstance(versions, dict) or set(versions) != LIBRARIES
                or any(not isinstance(v, str) or not 1 <= len(v) <= 80 for v in versions.values())):
            return dict(evidence, reason="incomplete_runtime_identity")
        fingerprint = sha256(json.dumps(runtime, sort_keys=True, separators=(",", ":"), allow_nan=False))
        if not _hash(receipt.get("runtime_sha256")) or receipt["runtime_sha256"] != fingerprint:
            return dict(evidence, reason="runtime_fingerprint_mismatch")
        return dict(evidence, matched=True, reason="executor_response_correlated",
                    receipt=receipt)
    except (KeyError, TypeError, ValueError, AttributeError):
        return dict(evidence, reason="invalid_executor_receipt")
