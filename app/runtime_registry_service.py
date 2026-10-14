"""Internal fixed-path approval service. Not registered as HTTP/MCP API.

Shared portal-admin credentials identify a role, not an individual human.
Eligibility remains a prerequisite and never grants runtime or physics trust.
"""
from pathlib import Path
import re
import secrets
import sqlite3

from app import config
from scripts.validation import runtime_completion
from scripts.validation import runtime_registry as registry
from scripts.validation.runtime_approval_contract import check_package

DATABASE = Path("/app/data/runtime_registry.db")
PACKAGE_ROOT = Path("/app/data/runtime-approvals/val1-plane-wave-v2")
APPROVAL_ID = "val1-plane-wave-v2"
_NONCE = re.compile(r"[0-9a-f]{32}")


def _authorize(token):
    expected = config.PORTAL_ADMIN_TOKEN
    if (not isinstance(expected, str) or len(expected) < 32
            or not isinstance(token, str)
            or not secrets.compare_digest(token.encode(), expected.encode())):
        raise PermissionError("runtime_registry_admin_required")


def _package():
    checked = check_package(PACKAGE_ROOT / "manifest.json", PACKAGE_ROOT)
    if not checked["package_valid"] or checked.get("approval_id") != APPROVAL_ID:
        raise ValueError("invalid_fixed_approval_package")
    return checked


def register_candidate(token, reason):
    _authorize(token)
    _package()
    registry.initialize(DATABASE)
    return registry.register(DATABASE, PACKAGE_ROOT / "manifest.json", PACKAGE_ROOT,
                             "portal-admin", reason)


def change_status(token, expected_revision, target, reason):
    _authorize(token)
    if target == "approved":
        package = _package()
        with sqlite3.connect(DATABASE.resolve().as_uri() + "?mode=ro", uri=True, timeout=2) as db:
            row = db.execute("SELECT manifest_sha256 FROM runtime_packages WHERE approval_id=?", (APPROVAL_ID,)).fetchone()
        if row is None or row[0] != package["manifest_sha256"]:
            raise ValueError("registered_package_changed")
    # Retirement remains possible even if the evidence package disappeared.
    return registry.transition(DATABASE, APPROVAL_ID, expected_revision, target,
                               "portal-admin", reason)


def inspect_eligibility(runtime_sha256, image_id, recipe_sha256, validator_sha256):
    result = {"contract_eligible": False, "runtime_approved": False,
              "trusted_run": False, "reason": "missing_or_invalid_registry"}
    try:
        package = _package()
        with sqlite3.connect(DATABASE.resolve().as_uri() + "?mode=ro", uri=True, timeout=2) as db:
            db.execute("BEGIN")
            row = db.execute("SELECT manifest_sha256,scope,status,revision FROM runtime_packages WHERE approval_id=?", (APPROVAL_ID,)).fetchone()
        if row is None or row[2] != "approved":
            result["reason"] = "not_currently_approved"
            return result
        if row[0] != package["manifest_sha256"] or row[1] != "production":
            result["reason"] = "package_or_scope_mismatch"
            return result
        if any(package[key] != value for key, value in (
                ("runtime_sha256", runtime_sha256), ("image_id", image_id),
                ("recipe_sha256", recipe_sha256), ("validator_sha256", validator_sha256))):
            result["reason"] = "observed_contract_mismatch"
            return result
        result.update(contract_eligible=True, revision=row[3],
                      approval_id=APPROVAL_ID, manifest_sha256=row[0],
                      reason="trusted_observation_binding_pending")
    except (OSError, sqlite3.Error, ValueError, TypeError):
        pass
    return result


def capture_dispatch_contract():
    """Server fixed-path prerequisite snapshot; no caller-selected package."""
    denied = {"contract_eligible": False, "trusted_run": False,
              "runtime_approved": False, "reason": "dispatch_contract_unavailable"}
    try:
        package = _package()
        result = inspect_eligibility(*[package[k] for k in
            ("runtime_sha256", "image_id", "recipe_sha256", "validator_sha256")])
        if result["contract_eligible"]:
            result["expected_contract"] = {k: package[k] for k in
                ("runtime_sha256", "image_id", "recipe_sha256", "validator_sha256")}
        return result
    except (OSError, sqlite3.Error, ValueError, TypeError, KeyError):
        return denied


def recheck_dispatch_contract(before, evidence):
    """Correlate an internal receipt and approval window, never grant trust.

    Image identity is approval metadata, not a host observation. This incomplete
    prerequisite is deliberately not named run_bound or runtime_approved.

    Malformed or hostile evidence must degrade to a denied result, never raise:
    this runs on the dispatch path, and an exception there would turn a rejected
    approval into an execution failure.
    """
    result = {"approval_window_matched": False, "trusted_run": False,
              "runtime_approved": False, "run_bound": False,
              "reason": "dispatch_contract_unavailable"}
    try:
        if not before.get("contract_eligible"):
            return result
        result.update(approval_id=before["approval_id"],
                      dispatch_revision=before["revision"],
                      dispatch_manifest_sha256=before["manifest_sha256"])
        if not evidence.get("matched"):
            return dict(result, reason="executor_receipt_unmatched")
        expected = before["expected_contract"]
        receipt = evidence["receipt"]
        if receipt["runtime_sha256"] != expected["runtime_sha256"]:
            return dict(result, reason="executor_runtime_mismatch")
        after = inspect_eligibility(*[expected[k] for k in
            ("runtime_sha256", "image_id", "recipe_sha256", "validator_sha256")])
        if not after["contract_eligible"]:
            return dict(result, reason=after["reason"])
        if (after["revision"] != before["revision"]
                or after["manifest_sha256"] != before["manifest_sha256"]):
            return dict(result, reason="approval_changed_during_execution")
        return dict(result, approval_window_matched=True,
                    reason="host_image_and_physics_binding_pending")
    except (KeyError, TypeError, ValueError, AttributeError,
            OSError, sqlite3.Error):
        return dict(result, reason="invalid_dispatch_evidence")


def record_dispatch_completion(approval_window, dispatch_nonce, run_id=""):
    """Persist an immutable per-dispatch completion receipt; never raises.

    Only attempted when the dispatch window actually matched a current approval.
    Without an approval package there is nothing to bind, so this writes nothing
    and does not create the registry database.
    """
    denied = {"completion_recorded": False, "dispatched_registry_matched": False,
              "trusted_run": False, "runtime_approved": False,
              "reason": "completion_not_attempted"}
    try:
        if not isinstance(approval_window, dict) or not approval_window.get("approval_window_matched"):
            return denied
        runtime_completion.initialize_dispatch(DATABASE)
        recorded = runtime_completion.record_dispatch_completion(
            DATABASE, dispatch_nonce, run_id, APPROVAL_ID,
            approval_window["dispatch_revision"], approval_window["dispatch_manifest_sha256"])
        matched = bool(recorded["dispatched_registry_matched"])
        return dict(denied, completion_recorded=matched,
                    dispatched_registry_matched=matched, reason=recorded["reason"])
    except (OSError, sqlite3.Error, ValueError, TypeError, KeyError, AttributeError):
        return dict(denied, reason="completion_write_failed")


def verify_completion_for_consumption(dispatch_nonce, *, expected_run_id=None):
    """Consumption-time recheck; retirement or drift makes a receipt unusable."""
    denied = {"consumable": False, "receipt_present": False,
              "trusted_run": False, "runtime_approved": False,
              "reason": "registry_unavailable"}
    if not isinstance(dispatch_nonce, str) or not _NONCE.fullmatch(dispatch_nonce):
        return dict(denied, reason="missing_dispatch_reference")
    try:
        if not DATABASE.exists():
            return denied
        return runtime_completion.verify_dispatch_completion(DATABASE, dispatch_nonce, expected_run_id=expected_run_id)
    except (OSError, sqlite3.Error, ValueError, TypeError, IndexError):
        return denied
