"""Read-only offline code contract check; NOT a production physics credential.

Accept only a run_id in a server-selected SQLite history, never caller outputs
or params. No code execution, migrations, grade changes, or learning. The local
recipe and validator are pinned by reviewed literals; changes require review.
The gateway/database and this installed verifier are assumed trusted. Runtime
image identity is not recorded yet, so trusted_run/parameters_bound stay false.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3

from scripts.validation.plane_wave_evidence import PROFILE, VERSION, validate_plane_wave
from app.executor_evidence import verify_executor_receipt

RECIPE_VERSION = "val1-plane-wave-code.v1"
RECIPE_SHA256 = "4561ef66b6daa4ffdd40ff6d9c35005fbdb1a3958fa70fb9309a29d4928d6b2b"
VALIDATOR_SHA256 = "a50a04db1d11c2681f9126971b5359dba4cebfa386688cc5bfdf6762c5528f7f"
# Observed real isolated HTTP baseline; NOT production environment approval.
RUNTIME_CONTRACT_VERSION = "plane-wave-isolated-runtime.v1"
ISOLATED_RUNTIME_SHA256 = "88c3cf85e907de4fad86c319f705472c74b2a6f74f9e5e2d80e0038d3ee945c6"
DIRECTORY = Path(__file__).resolve().parent


def digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_json_key")
        result[key] = value
    return result


def reject_constant(value):
    raise ValueError("nonfinite_json_constant")


def inspect_run(database, run_id):
    receipt = {"run_id": run_id, "recipe_version": RECIPE_VERSION,
               "validator_version": VERSION, "code_contract_matched": False,
               "numeric_pass": False, "trusted_run": False,
               "parameters_bound": False, "runtime_contract_version": RUNTIME_CONTRACT_VERSION,
               "runtime_contract_scope": "isolated_validation_only", "runtime_approved": False,
               "execution_receipt_matched": False, "runtime_contract_matched": False,
               "dispatch_context_matched": False,
               "provenance_prerequisites_passed": False, "reason": "invalid_run_reference"}
    if not isinstance(run_id, str) or not run_id:
        return receipt
    db = None
    try:
        recipe = (DIRECTORY / "recipes/val1_plane_wave_v2.py").read_text().strip()
        validator_bytes = (DIRECTORY / "plane_wave_evidence.py").read_bytes()
        if digest(recipe) != RECIPE_SHA256 or hashlib.sha256(validator_bytes).hexdigest() != VALIDATOR_SHA256:
            receipt["reason"] = "unreviewed_recipe_or_validator"
            return receipt
        db = sqlite3.connect(Path(database).resolve().as_uri() + "?mode=ro", uri=True, timeout=2)
        db.row_factory = sqlite3.Row
        db.execute("BEGIN")
        rows = db.execute("SELECT * FROM executions WHERE run_id=? LIMIT 2", (run_id,)).fetchall()
        if len(rows) != 1:
            receipt["reason"] = "missing_or_ambiguous_execution"
            return receipt
        row = rows[0]
        if row["record_version"] != "gateway-execution.v1":
            receipt["reason"] = "legacy_unverified"
            return receipt
        if not isinstance(row["code"], str) or digest(row["code"]) != row["code_sha256"]:
            receipt["reason"] = "recorded_code_mismatch"
            return receipt
        if row["code_sha256"] != RECIPE_SHA256 or row["code"] != recipe:
            receipt["reason"] = "unapproved_execution_code"
            return receipt
        if row["exit_code"] != 0 or row["timed_out"] != 0:
            receipt["reason"] = "execution_failed_or_timed_out"
            return receipt
        if not isinstance(row["stdout"], str) or len(row["stdout"].encode()) > 1_000_000:
            receipt["reason"] = "invalid_output_size"
            return receipt
        # Strict whole JSON: no arbitrary marker extraction or caller receipt.
        result = json.loads(row["stdout"], object_pairs_hook=unique_object, parse_constant=reject_constant)
        if not isinstance(result, dict) or result.get("raw_evidence", {}).get("profile") != PROFILE:
            receipt["reason"] = "unsupported_profile"
            return receipt
        numeric = validate_plane_wave(result)
        receipt.update(code_contract_matched=True, execution_id=row["id"],
                       code_sha256=row["code_sha256"], validator_sha256=VALIDATOR_SHA256,
                       stdout_sha256=digest(row["stdout"]), stderr_sha256=digest(row["stderr"]), numeric_pass=numeric["numeric_pass"],
                       numeric_receipt=numeric,
                       reason="runtime_identity_not_bound" if numeric["numeric_pass"] else "numeric_checks_failed")
        # Legacy offline rows can still be numerically diagnosed, never trusted.
        if row["tool_name"] != "run_controlled_plane_wave.v1" or not row["executor_evidence_json"]:
            return receipt
        stored = json.loads(row["executor_evidence_json"], object_pairs_hook=unique_object, parse_constant=reject_constant)
        context_nonce = stored.get("gateway_request_nonce")
        context_timeout = stored.get("gateway_timeout_seconds")
        if type(context_timeout) is not int or context_timeout != 30:
            receipt["reason"] = "controlled_timeout_context_mismatch"
            return receipt
        response = {"stdout": row["stdout"], "stderr": row["stderr"],
                    "exit_code": row["exit_code"], "timed_out": bool(row["timed_out"])}
        correlated = verify_executor_receipt(stored.get("receipt"), response, recipe, 30, context_nonce)
        receipt["execution_receipt_matched"] = correlated["matched"]
        if not correlated["matched"]:
            receipt["reason"] = correlated["reason"]
            return receipt
        runtime_hash = correlated["receipt"]["runtime_sha256"]
        receipt["runtime_sha256"] = runtime_hash
        receipt["runtime_contract_matched"] = runtime_hash == ISOLATED_RUNTIME_SHA256
        if not receipt["runtime_contract_matched"]:
            receipt["reason"] = "isolated_runtime_contract_mismatch"
            return receipt
        from app import controlled_dispatch
        context = controlled_dispatch.inspect(db, run_id, context_nonce)
        receipt["dispatch_context_matched"] = context["dispatch_context_matched"]
        if not context["dispatch_context_matched"]:
            receipt["reason"] = context["reason"]
            return receipt
        receipt["provenance_prerequisites_passed"] = receipt["numeric_pass"]
        receipt["reason"] = "production_runtime_approval_pending" if receipt["numeric_pass"] else "numeric_checks_failed"
        # No grade is issued and actual parameter/runtime trust stays closed.
        return receipt
    except (OSError, sqlite3.Error, KeyError, IndexError, TypeError, ValueError, AttributeError, RecursionError):
        receipt["reason"] = "missing_or_invalid_evidence"
        return receipt
    finally:
        if db is not None:
            db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True, help="operator-selected server history; read only")
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    checked = inspect_run(args.database, args.run_id)
    print(json.dumps(checked, allow_nan=False))
    # Exit 0 means only this offline prerequisite passed, not trusted_run.
    raise SystemExit(0 if checked["code_contract_matched"] and checked["numeric_pass"] else 1)
