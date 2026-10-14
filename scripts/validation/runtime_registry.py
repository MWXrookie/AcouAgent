"""Offline SQLite lifecycle prototype; no production authorization or trust.

Only an operator-selected disposable database is exercised. Caller actor labels
are audit metadata, NOT authenticated identities. No gateway tool imports this.
"""
import sqlite3
from scripts.validation.runtime_approval_contract import check_package


def initialize(database):
    with sqlite3.connect(database, timeout=2) as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS runtime_packages(
            approval_id TEXT PRIMARY KEY, manifest_sha256 TEXT NOT NULL,
            scope TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('candidate','approved','retired')),
            revision INTEGER NOT NULL CHECK(revision>=1));
        CREATE TABLE IF NOT EXISTS runtime_audit(
            event_id INTEGER PRIMARY KEY, approval_id TEXT NOT NULL,
            from_status TEXT, to_status TEXT NOT NULL, revision INTEGER NOT NULL,
            manifest_sha256 TEXT NOT NULL, actor TEXT NOT NULL, reason TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(approval_id,revision));
        CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON runtime_audit
        BEGIN SELECT RAISE(ABORT,'audit_append_only'); END;
        CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON runtime_audit
        BEGIN SELECT RAISE(ABORT,'audit_append_only'); END;
        """)


def _metadata(actor, reason):
    if any(not isinstance(x, str) or not x.strip() or len(x) > 512 for x in (actor, reason)):
        raise ValueError("missing_audit_metadata")


def register(database, manifest, evidence_root, actor, reason):
    _metadata(actor, reason)
    package = check_package(manifest, evidence_root)
    if not package["package_valid"] or package["status"] != "candidate":
        raise ValueError("invalid_candidate_package")
    with sqlite3.connect(database, timeout=2) as db:
        db.execute("BEGIN IMMEDIATE")
        values = (package["approval_id"], package["manifest_sha256"], package["scope"])
        db.execute("INSERT INTO runtime_packages VALUES(?,?,?,'candidate',1)", values)
        db.execute("INSERT INTO runtime_audit(approval_id,from_status,to_status,revision,manifest_sha256,actor,reason) VALUES(?,NULL,'candidate',1,?,?,?)",
                   (values[0], values[1], actor, reason))
    return {"approval_id": values[0], "revision": 1, "runtime_approved": False}


def transition(database, approval_id, expected_revision, target, actor, reason):
    _metadata(actor, reason)
    if type(expected_revision) is not int or expected_revision < 1:
        raise ValueError("invalid_revision")
    if target not in {"approved", "retired"}:
        raise ValueError("invalid_target")
    with sqlite3.connect(database, timeout=2) as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT manifest_sha256,status,revision FROM runtime_packages WHERE approval_id=?", (approval_id,)).fetchone()
        if row is None or row[2] != expected_revision:
            raise ValueError("missing_or_stale_revision")
        if (row[1], target) not in {("candidate", "approved"), ("candidate", "retired"), ("approved", "retired")}:
            raise ValueError("invalid_transition")
        revision = row[2] + 1
        db.execute("UPDATE runtime_packages SET status=?,revision=? WHERE approval_id=?", (target, revision, approval_id))
        db.execute("INSERT INTO runtime_audit(approval_id,from_status,to_status,revision,manifest_sha256,actor,reason) VALUES(?,?,?,?,?,?,?)",
                   (approval_id, row[1], target, revision, row[0], actor, reason))
    return {"approval_id": approval_id, "revision": revision, "runtime_approved": False}
