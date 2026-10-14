"""Server-owned pre-dispatch context, not an image or physics credential.

Only the internal pinned recipe may create records. No HTTP/MCP writer exists.
The gateway and its SQLite file are trust assumptions; hashes do not authenticate
an administrator. Consumers must also recheck approval and host observation.
"""
import hashlib
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from scripts.validation import plane_wave_run_binding as binding
from scripts.validation.plane_wave_evidence import EXPECTED_CONFIGURATION

VERSION = "controlled-dispatch.v1"


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def record(database, run_id, nonce, envelope=None):
    """Persist before execution; failure prevents controlled dispatch."""
    if (not isinstance(run_id, str) or not 1 <= len(run_id) <= 128
            or not isinstance(nonce, str) or not re.fullmatch(r"[0-9a-f]{32}", nonce)):
        raise ValueError("invalid_controlled_dispatch_reference")
    if envelope is not None:
        from app.controlled_request_adapter import verify_envelope
        if not verify_envelope(envelope, expected_run_id=run_id, expected_nonce=nonce)["request_context_matched"]:
            raise ValueError("invalid_controlled_request_envelope")
        context = _canonical(envelope["context"])
        context_digest = envelope["context_sha256"]
    configuration = _canonical(EXPECTED_CONFIGURATION)
    with sqlite3.connect(database, timeout=2) as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute("""CREATE TABLE IF NOT EXISTS controlled_dispatches (
            run_id TEXT PRIMARY KEY, request_nonce TEXT NOT NULL UNIQUE,
            context_version TEXT NOT NULL, code_sha256 TEXT NOT NULL,
            validator_sha256 TEXT NOT NULL, configuration_json TEXT NOT NULL,
            configuration_sha256 TEXT NOT NULL, timeout_seconds INTEGER NOT NULL,
            created_at TEXT NOT NULL)""")
        for operation in ("UPDATE", "DELETE"):
            db.execute(f"""CREATE TRIGGER IF NOT EXISTS controlled_dispatch_no_{operation.lower()}
                BEFORE {operation} ON controlled_dispatches BEGIN
                SELECT RAISE(ABORT, 'controlled_dispatches is append-only'); END""")
        db.execute("""CREATE TRIGGER IF NOT EXISTS controlled_dispatch_no_replace
            BEFORE INSERT ON controlled_dispatches
            WHEN EXISTS (SELECT 1 FROM controlled_dispatches
                         WHERE run_id=NEW.run_id OR request_nonce=NEW.request_nonce)
            BEGIN SELECT RAISE(ABORT, 'controlled_dispatches is append-only'); END""")
        db.execute("INSERT INTO controlled_dispatches VALUES (?,?,?,?,?,?,?,?,?)",
                   (run_id, nonce, VERSION, binding.RECIPE_SHA256, binding.VALIDATOR_SHA256,
                    configuration, hashlib.sha256(configuration.encode()).hexdigest(), 30,
                    datetime.now(timezone.utc).isoformat()))
        if envelope is not None:
            db.execute("""CREATE TABLE IF NOT EXISTS controlled_request_contexts (
                run_id TEXT PRIMARY KEY REFERENCES controlled_dispatches(run_id),
                request_nonce TEXT NOT NULL UNIQUE,
                context_json TEXT NOT NULL, context_sha256 TEXT NOT NULL)""")
            for operation in ("UPDATE", "DELETE"):
                db.execute(f"""CREATE TRIGGER IF NOT EXISTS request_context_no_{operation.lower()}
                    BEFORE {operation} ON controlled_request_contexts BEGIN
                    SELECT RAISE(ABORT, 'request context is append-only'); END""")
            db.execute("""CREATE TRIGGER IF NOT EXISTS request_context_no_replace
                BEFORE INSERT ON controlled_request_contexts
                WHEN EXISTS (SELECT 1 FROM controlled_request_contexts
                             WHERE run_id=NEW.run_id OR request_nonce=NEW.request_nonce)
                BEGIN SELECT RAISE(ABORT, 'request context is append-only'); END""")
            db.execute("INSERT INTO controlled_request_contexts VALUES (?,?,?,?)",
                       (run_id, nonce, context, context_digest))



def inspect(db, run_id, nonce):
    """Use the consumer's existing read snapshot; never accept caller context."""
    denied = {"dispatch_context_matched": False, "parameters_bound": False,
              "run_bound": False, "trusted_run": False, "runtime_approved": False,
              "request_context_matched": False}
    try:
        rows = db.execute("SELECT * FROM controlled_dispatches WHERE run_id=? LIMIT 2", (run_id,)).fetchall()
        if len(rows) != 1:
            return dict(denied, reason="missing_dispatch_context")
        # Independent of the caller connection's row_factory.
        row = tuple(rows[0])
        configuration = _canonical(EXPECTED_CONFIGURATION)
        expected = (run_id, nonce, VERSION, binding.RECIPE_SHA256,
                    binding.VALIDATOR_SHA256, configuration,
                    hashlib.sha256(configuration.encode()).hexdigest(), 30)
        if len(row) != 9 or any(type(a) is not type(b) or a != b for a, b in zip(row[:8], expected)):
            return dict(denied, reason="dispatch_context_mismatch")
        if not isinstance(nonce, str) or not re.fullmatch(r"[0-9a-f]{32}", nonce):
            return dict(denied, reason="invalid_dispatch_nonce")
        request_matched = False
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='controlled_request_contexts'").fetchone():
            from app.controlled_request_adapter import make_envelope
            envelope = make_envelope(run_id, nonce)
            context_row = db.execute(
                "SELECT run_id,request_nonce,context_json,context_sha256 FROM controlled_request_contexts WHERE run_id=?",
                (run_id,)).fetchone()
            expected_context = (run_id, nonce, _canonical(envelope["context"]), envelope["context_sha256"])
            if context_row is None or tuple(context_row) != expected_context:
                return dict(denied, reason="request_context_mismatch")
            request_matched = True
        return dict(denied, dispatch_context_matched=True,
                    request_context_matched=request_matched, reason="fixed_input_context_only")
    except (sqlite3.Error, ValueError, TypeError, IndexError):
        return dict(denied, reason="dispatch_context_unavailable")


def inspect_file(database, run_id, nonce):
    try:
        with sqlite3.connect(Path(database).resolve().as_uri() + "?mode=ro", uri=True, timeout=2) as db:
            db.execute("BEGIN")
            return inspect(db, run_id, nonce)
    except (sqlite3.Error, OSError, ValueError):
        return {"dispatch_context_matched": False, "parameters_bound": False,
                "run_bound": False, "trusted_run": False, "runtime_approved": False,
                "reason": "dispatch_context_unavailable"}

def fixed_retrieval_parameters():
    """Canonical query identity of this fixed profile, not runtime authentication.

    This initial-pressure experiment has no declared continuous frequency.
    Do not attach a caller's frequency or scene to its fallback audit.
    """
    from app.skill_store import normalize_parameters
    configuration = EXPECTED_CONFIGURATION
    return normalize_parameters({
        "domain_N": configuration["domain_N"], "dimensions":2,
        "dx_m": configuration["domain_dx_m"],
        "sound_speed_m_s": configuration["sound_speed_m_s"],
        "density_kg_m3": configuration["density_kg_m3"],
        "pml_size": configuration["pml_size"], "cfl": configuration["cfl"],
        "t_end_s": configuration["t_end_s"],
        "initial_pressure_pa": configuration["initial_pressure_pa"],
        "initial_velocity": configuration["initial_velocity"],
        "initial_sigma_m": configuration["sigma_grid"]*configuration["domain_dx_m"][0],
        "source_type": "gaussian_initial_pressure", "source_geometry": "planar_x_uniform_y",
        "source_index": [configuration["source_x_index"],configuration["domain_N"][1]//2],
        "sensor_indices": configuration["sensor_indices"], "smooth_initial": configuration["smooth_initial"],
        "solver_mode": "time_domain", "medium_type": "homogeneous_water"})
