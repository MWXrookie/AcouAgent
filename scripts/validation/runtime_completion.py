"""Offline completion receipt prototype; never authorizes a physical result."""
import os
import re
import sqlite3
from pathlib import Path


def initialize(database):
    with sqlite3.connect(database, timeout=2) as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS runtime_completion_receipts(
          run_id TEXT PRIMARY KEY, approval_id TEXT NOT NULL,
          expected_revision INTEGER NOT NULL, observed_revision INTEGER,
          eligible INTEGER NOT NULL CHECK(eligible IN (0,1)),
          reason TEXT NOT NULL, trusted_run INTEGER NOT NULL CHECK(trusted_run=0));
        CREATE TRIGGER IF NOT EXISTS completion_no_update BEFORE UPDATE ON runtime_completion_receipts
          BEGIN SELECT RAISE(ABORT,'completion_append_only'); END;
        CREATE TRIGGER IF NOT EXISTS completion_no_delete BEFORE DELETE ON runtime_completion_receipts
          BEGIN SELECT RAISE(ABORT,'completion_append_only'); END;
        """)


def record_completion(database, run_id, approval_id, expected_revision, manifest_sha256):
    """Atomic registry recheck + immutable receipt in an operator test database.

    Call arguments remain untrusted metadata. Even eligible=True proves only
    that an approved row matched at this transaction; it is NOT a runtime grant.
    Real server observation, request binding and physics gates are still required.
    """
    if not isinstance(run_id, str) or not re.fullmatch(r"[A-Za-z0-9-]{1,128}", run_id):
        raise ValueError("invalid_run_id")
    if not isinstance(approval_id, str) or not 1 <= len(approval_id) <= 128:
        raise ValueError("invalid_approval_id")
    if type(expected_revision) is not int or expected_revision < 1:
        raise ValueError("invalid_revision")
    if not isinstance(manifest_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", manifest_sha256):
        raise ValueError("invalid_manifest_hash")
    with sqlite3.connect(database, timeout=2) as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT status,revision,manifest_sha256 FROM runtime_packages WHERE approval_id=?",
                         (approval_id,)).fetchone()
        reason = "missing_approval"
        eligible = False
        if row:
            if row[0] != "approved":
                reason = "not_currently_approved"
            elif row[1] != expected_revision:
                reason = "stale_revision"
            elif row[2] != manifest_sha256:
                reason = "manifest_mismatch"
            else:
                eligible, reason = True, "offline_registry_match_only"
        db.execute("INSERT INTO runtime_completion_receipts VALUES(?,?,?,?,?,?,0)",
                   (run_id, approval_id, expected_revision, row[1] if row else None, int(eligible), reason))
    return {"run_id": run_id, "registry_snapshot_matched": eligible,
            "reason": reason, "trusted_run": False, "runtime_approved": False}


# ---------------------------------------------------------------------------
# Per-dispatch completion receipts (additive; the run-keyed API above is
# unchanged). A retry loop reuses one run_id across attempts, so the immutable
# receipt is keyed by the gateway dispatch nonce, which is unique per attempt.
# ---------------------------------------------------------------------------

_NONCE = re.compile(r"[0-9a-f]{32}")
_RUN_ID = re.compile(r"[A-Za-z0-9-]{0,128}")
_HEX64 = re.compile(r"[0-9a-f]{64}")


def initialize_dispatch(database):
    """Create the append-only dispatch-receipt table and its guards."""
    with sqlite3.connect(database, timeout=2) as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS runtime_dispatch_completions(
          dispatch_nonce TEXT PRIMARY KEY,
          run_id TEXT NOT NULL DEFAULT '',
          approval_id TEXT NOT NULL,
          expected_revision INTEGER NOT NULL,
          observed_revision INTEGER,
          eligible INTEGER NOT NULL CHECK(eligible IN (0,1)),
          reason TEXT NOT NULL,
          trusted_run INTEGER NOT NULL CHECK(trusted_run=0));
        CREATE TRIGGER IF NOT EXISTS dispatch_no_update BEFORE UPDATE ON runtime_dispatch_completions
          BEGIN SELECT RAISE(ABORT,'completion_append_only'); END;
        CREATE TRIGGER IF NOT EXISTS dispatch_no_delete BEFORE DELETE ON runtime_dispatch_completions
          BEGIN SELECT RAISE(ABORT,'completion_append_only'); END;
        CREATE TRIGGER IF NOT EXISTS dispatch_no_replace BEFORE INSERT ON runtime_dispatch_completions
          WHEN EXISTS(SELECT 1 FROM runtime_dispatch_completions WHERE dispatch_nonce=NEW.dispatch_nonce)
          BEGIN SELECT RAISE(ABORT,'completion_append_only'); END;
        """)


def record_dispatch_completion(database, dispatch_nonce, run_id, approval_id,
                               expected_revision, manifest_sha256):
    """Atomic registry recheck plus immutable receipt for one dispatch.

    Call arguments stay untrusted metadata. eligible=1 proves only that an
    approved row matched inside this transaction; it is never a runtime grant,
    and it does not bind a real host observation or a physics result.
    """
    if not isinstance(dispatch_nonce, str) or not _NONCE.fullmatch(dispatch_nonce):
        raise ValueError("invalid_dispatch_nonce")
    if not isinstance(run_id, str) or not _RUN_ID.fullmatch(run_id):
        raise ValueError("invalid_run_id")
    if not isinstance(approval_id, str) or not 1 <= len(approval_id) <= 128:
        raise ValueError("invalid_approval_id")
    if type(expected_revision) is not int or expected_revision < 1:
        raise ValueError("invalid_revision")
    if not isinstance(manifest_sha256, str) or not _HEX64.fullmatch(manifest_sha256):
        raise ValueError("invalid_manifest_hash")
    with sqlite3.connect(database, timeout=2) as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT status,revision,manifest_sha256 FROM runtime_packages WHERE approval_id=?",
                         (approval_id,)).fetchone()
        reason, eligible = "missing_approval", False
        if row:
            if row[0] != "approved":
                reason = "not_currently_approved"
            elif row[1] != expected_revision:
                reason = "stale_revision"
            elif row[2] != manifest_sha256:
                reason = "manifest_mismatch"
            else:
                eligible, reason = True, "dispatch_registry_match_only"
        db.execute("INSERT INTO runtime_dispatch_completions VALUES(?,?,?,?,?,?,?,0)",
                   (dispatch_nonce, run_id, approval_id, expected_revision,
                    row[1] if row else None, int(eligible), reason))
    return {"dispatch_nonce": dispatch_nonce, "run_id": run_id,
            "dispatched_registry_matched": eligible, "reason": reason,
            "trusted_run": False, "runtime_approved": False}


def verify_dispatch_completion(database, dispatch_nonce, *, expected_run_id=None):
    """Consumption-time recheck. A historical receipt never authorizes later use.

    Reads the receipt and the current approval row in one snapshot: a receipt
    stays proof of a past transaction only, so retirement or a revision change
    invalidates consumption even when the receipt itself is intact.
    """
    result = {"dispatch_nonce": dispatch_nonce, "receipt_present": False,
              "consumable": False, "reason": "completion_receipt_missing",
              "trusted_run": False, "runtime_approved": False}
    if not isinstance(dispatch_nonce, str) or not _NONCE.fullmatch(dispatch_nonce):
        return dict(result, reason="invalid_dispatch_nonce")
    if expected_run_id is not None and (not isinstance(expected_run_id, str)
            or not expected_run_id or not _RUN_ID.fullmatch(expected_run_id)):
        return dict(result, reason="invalid_run_id")
    try:
        with sqlite3.connect(Path(database).resolve().as_uri()+"?mode=ro", uri=True, timeout=2) as db:
            db.execute("PRAGMA query_only=ON")
            db.execute("BEGIN")
            row = db.execute("SELECT approval_id,observed_revision,eligible,reason,run_id "
                             "FROM runtime_dispatch_completions WHERE dispatch_nonce=?",
                             (dispatch_nonce,)).fetchone()
            if row is None:
                return result
            result.update(receipt_present=True, approval_id=row[0],
                          observed_revision=row[1], receipt_reason=row[3])
            if expected_run_id is not None and row[4] != expected_run_id:
                return dict(result, reason="completion_run_mismatch")
            if not row[2]:
                return dict(result, reason="completion_receipt_not_eligible")
            current = db.execute("SELECT status,revision FROM runtime_packages WHERE approval_id=?",
                                 (row[0],)).fetchone()
            if current is None:
                return dict(result, reason="missing_approval")
            if current[0] != "approved":
                return dict(result, reason="not_currently_approved")
            if current[1] != row[1]:
                return dict(result, reason="revision_changed")
            return dict(result, consumable=True, reason="still_approved_and_unchanged")
    except (sqlite3.Error, TypeError, ValueError, IndexError):
        return dict(result, reason="registry_unavailable")
