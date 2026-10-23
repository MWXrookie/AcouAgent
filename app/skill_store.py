"""Auditable SQLite storage for simulation-skill candidates.

The store is deliberately internal-only.  It records evidence-bound candidates
but never decides that a skill is active; Q3/Q4 activation arrives with the
scenario physics gate in a later implementation package.
"""

from __future__ import annotations

import ast
import hashlib
import json
import math
import re
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping

SCHEMA_VERSION = 17
AUTHORING_SCHEMA_VERSION = 18
_LOCK = threading.Lock()
_STATUSES = {"candidate", "active", "questioned", "retired"}
SCENARIO_TYPES = {
    "point_source_2d", "point_source_3d", "plane_wave_2d", "initial_pressure_2d",
    "interface_2d", "attenuation_helmholtz_2d", "heterogeneous_2d",
}
_UNIT_KEYS = {
    "frequency_mhz": ("frequency_hz", 1_000_000.0),
    "source_frequency_mhz": ("frequency_hz", 1_000_000.0),
    "frequency_hz": ("frequency_hz", 1.0),
    "source_frequency": ("frequency_hz", 1.0),
    "dx_mm": ("dx_m", 0.001),
    "domain_dx_mm": ("dx_m", 0.001),
    "grid_step_mm": ("dx_m", 0.001),
    "dx_m": ("dx_m", 1.0),
    "domain_dx": ("dx_m", 1.0),
    "sound_speed_m_s": ("sound_speed_m_s", 1.0),
    "sound_speed": ("sound_speed_m_s", 1.0),
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical_json(value: Mapping[str, Any]) -> str:
    if not isinstance(value, Mapping):
        raise ValueError("structured value must be a JSON object")
    try:
        return json.dumps(dict(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError("structured value must be finite JSON") from exc


_PHYSICAL_VALUE_FIELDS = {
    "density_kg_m3", "initial_pressure_pa", "initial_sigma_m", "t_end_s",
    "secondary_sound_speed_m_s", "interface_position_m",
    "modal_amplitudes_pa", "modal_wavenumber_rad_per_m",
}


def _physical_value(value):
    # JS/JSON round trips erase 1.0 versus 1. Preserve physical meaning while
    # retaining integer grid/index contracts and the existing fixed-profile pins.
    if type(value) is list:
        return [_physical_value(item) for item in value]
    if type(value) not in (int, float):
        raise ValueError("physical value must be numeric")
    try:
        result = float(value)
    except OverflowError:
        raise ValueError("physical value must be finite") from None
    if not math.isfinite(result):
        raise ValueError("physical value must be finite")
    return result


def normalize_parameters(parameters: Mapping[str, Any]) -> dict[str, Any]:
    """Return a canonical, unit-explicit parameter object without guessing units."""
    if not isinstance(parameters, Mapping) or not parameters:
        raise ValueError("parameters must be a non-empty JSON object")
    normalized: dict[str, Any] = {}
    for key, value in parameters.items():
        if key in _UNIT_KEYS:
            target, multiplier = _UNIT_KEYS[key]
            values = value if type(value) is list and target == "dx_m" else [value]
            if not values or len(values) > 3:
                raise ValueError(f"{key} has invalid dimensions")
            converted_values = []
            for item in values:
                if type(item) not in (int, float):
                    raise ValueError(f"{key} must be numeric")
                try:
                    number = float(item) * multiplier
                except (ValueError, OverflowError):
                    raise ValueError(f"{key} must be finite") from None
                if not math.isfinite(number) or number <= 0:
                    raise ValueError(f"{key} must be finite and positive")
                converted_values.append(number)
            converted = converted_values if type(value) is list else converted_values[0]
            previous = normalized.get(target)
            if previous is not None and previous != converted:
                raise ValueError(f"conflicting values for {target}")
            normalized[target] = converted
        elif key in {"domain_N", "grid_size", "grid_shape"}:
            values = value if isinstance(value, list) else [value]
            if not values or any(isinstance(v, bool) or not isinstance(v, int) or v <= 0 for v in values):
                raise ValueError(f"{key} must be positive integer dimensions")
            if "grid_shape" in normalized and normalized["grid_shape"] != values:
                raise ValueError("conflicting values for grid_shape")
            normalized["grid_shape"] = values
        elif key in _PHYSICAL_VALUE_FIELDS:
            normalized[key] = _physical_value(value)
        elif key == "boundary_condition":
            if type(value) is not str or value not in {"periodic", "absorbing_pml", "rigid", "open"}:
                raise ValueError("explicit_boundary_condition_required")
            normalized[key] = value
        elif key in {"solver_mode", "source_type", "medium_type", "has_sensor", "attenuation_db_cm_mhz"}:
            normalized[key] = value
        else:
            # Unknown fields are kept only when already unit-explicit or structural.
            if key.endswith(("_hz", "_m", "_s", "_pa", "_kg_m3")) or key in {"pml_size", "cfl", "dimensions", "source_index", "sensor_index", "sensor_indices", "source_geometry", "smooth_initial", "initial_velocity"}:
                normalized[key] = value
            else:
                raise ValueError(f"unknown or unit-ambiguous parameter: {key}")
    _canonical_json(normalized)
    return normalized


def skill_fingerprint(scenario_type: str, normalized_params: Mapping[str, Any]) -> str:
    """Stable identity for one controlled scenario and its canonical parameters."""
    if scenario_type not in SCENARIO_TYPES:
        raise ValueError(f"unsupported scenario_type: {scenario_type}")
    material = _canonical_json({"scenario_type": scenario_type, "parameters": dict(normalized_params)})
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def validate_candidate_content(code: str, metadata: Mapping[str, Any]) -> None:
    """Known-pattern storage preflight; never substitutes for sandbox or trust."""
    if not isinstance(code, str) or not code.strip() or len(code.encode("utf-8")) > 20000:
        raise ValueError("candidate code must be non-empty and at most 20000 bytes")
    if chr(96) * 3 in code or re.search(r"</?think\b", code, re.I):
        raise ValueError("candidate code contains a generation wrapper")
    try:
        tree = ast.parse(code)
    except (SyntaxError, ValueError, RecursionError) as exc:
        raise ValueError("candidate code must be valid Python") from exc
    def absolute_path(value):
        return (value.startswith("/") or value.startswith(chr(92) * 2)
                or re.match(r"^[A-Za-z]:[\\/]", value) is not None)
    if any(isinstance(node, ast.Constant) and isinstance(node.value, str)
           and absolute_path(node.value) for node in ast.walk(tree)):
        raise ValueError("candidate code contains a local absolute path")
    forbidden = {"stdout", "stderr", "env", "environment", "api_key", "token",
                 "password", "secret", "credentials"}
    def check_metadata(value, depth=0):
        if depth > 16:
            raise ValueError("candidate metadata nesting exceeds budget")
        if isinstance(value, Mapping):
            for key, item in value.items():
                if not isinstance(key, str) or key.lower() in forbidden:
                    raise ValueError("candidate metadata contains a forbidden field")
                check_metadata(item, depth + 1)
        elif isinstance(value, (list, tuple)):
            for item in value:
                check_metadata(item, depth + 1)
        elif isinstance(value, str) and absolute_path(value):
            raise ValueError("candidate metadata contains a local absolute path")
    check_metadata(metadata)
    text = code + "\n" + _canonical_json(metadata)
    if len(text.encode("utf-8")) > 64000:
        raise ValueError("candidate content exceeds storage budget")
    if (re.search(r"(?i)(?:api[_-]?key|shared[_-]?token|password|secret)\s*[\"']?\s*[:=]", text)
            or re.search(r"(?i)bearer\s+[a-z0-9._-]{16,}", text)
            or re.search(r"\bsk-[a-zA-Z0-9_-]{16,}", text)):
        raise ValueError("candidate content contains a credential pattern")
    if (re.search(r"(?<![A-Za-z0-9_])[A-Za-z]:[\\/]", text)
            or re.search(r"/(?:home|Users|root|app|tmp|workspace|etc|var|opt|mnt|srv|data)/", text)):
        raise ValueError("candidate content contains a local absolute path")


def validate_preconditions(value: Mapping[str, Any]) -> dict[str, Any]:
    """Explicit optional bounds; an empty legacy declaration never proves a match."""
    if not isinstance(value, Mapping):
        raise ValueError("preconditions must be an object")
    result = dict(value)
    allowed = {"dimensions", "solver_mode", "medium_type", "frequency_hz", "dx_m", "parameters_sha256"}
    if set(result) - allowed:
        raise ValueError("unsupported precondition")
    if "dimensions" in result and (type(result["dimensions"]) is not int or result["dimensions"] not in (2, 3)):
        raise ValueError("dimensions must be 2 or 3")
    for key in ("solver_mode", "medium_type"):
        if key in result and (not isinstance(result[key], str) or not result[key].strip() or len(result[key]) > 80):
            raise ValueError(f"{key} must be a bounded non-empty string")
    for key in ("frequency_hz", "dx_m"):
        if key in result:
            bounds = result[key]
            if (not isinstance(bounds, list) or len(bounds) != 2
                    or any(type(v) not in (int, float) or not math.isfinite(v) or v <= 0 for v in bounds)
                    or bounds[0] > bounds[1]):
                raise ValueError(f"{key} must be positive finite [min, max]")
    if "parameters_sha256" in result:
        digest = result["parameters_sha256"]
        if type(digest) is not str or re.fullmatch(r"[0-9a-f]{64}",digest) is None:
            raise ValueError("parameters_sha256 must be a canonical SHA256")
    _canonical_json(result)
    return result


def preconditions_match(preconditions: Mapping[str, Any], parameters: Mapping[str, Any]) -> bool:
    """Fail closed on missing declarations/parameters; this does not grant trust."""
    bounds = validate_preconditions(preconditions)
    if not bounds:
        return False
    normalized = normalize_parameters(parameters)
    for key, expected in bounds.items():
        actual = normalized.get(key)
        if key == "parameters_sha256":
            actual = hashlib.sha256(_canonical_json(normalized).encode()).hexdigest()
        if key == "dimensions" and actual is None:
            shape = normalized.get("grid_shape")
            actual = len(shape) if isinstance(shape, list) else None
        if key in ("frequency_hz", "dx_m"):
            values = actual if key == "dx_m" and type(actual) is list else [actual]
            if not values or any(type(value) not in (int, float) or not math.isfinite(value)
                                 or not expected[0] <= value <= expected[1] for value in values):
                return False
        elif type(actual) is not type(expected) or actual != expected:
            return False
    return True


class SkillStore:
    """Own a SQLite skill database at a server-selected path."""

    def __init__(self, path: str | Path):
        self.path = Path(path)

    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA foreign_keys=ON")
            # First-process WAL initialization can return SQLITE_BUSY without
            # honoring the normal connection timeout. Bound only this setup
            # retry; never replay a migration or candidate transaction.
            db.execute("PRAGMA busy_timeout=250")
            deadline=time.monotonic()+2
            while True:
                try:
                    db.execute("PRAGMA journal_mode=WAL")
                    break
                except sqlite3.OperationalError as failure:
                    code=getattr(failure,"sqlite_errorcode",None)
                    if code is None or code & 255 != sqlite3.SQLITE_BUSY or time.monotonic()>=deadline:
                        raise
                    time.sleep(0.02)
            db.execute("PRAGMA busy_timeout=10000")
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    @contextmanager
    def _db_existing_write(self) -> Iterator[sqlite3.Connection]:
        """Bounded writer that never creates or migrates a missing database."""
        from app import skill_deadline
        db = sqlite3.connect(self.path.resolve().as_uri() + "?mode=rw", uri=True, timeout=skill_deadline.sqlite_timeout())
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA foreign_keys=ON")
            yield db
            skill_deadline.check()
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    @contextmanager
    def _read_db(self) -> Iterator[sqlite3.Connection]:
        # Consumers must not initialize/migrate a missing or legacy database.
        from app import skill_deadline
        db = sqlite3.connect(self.path.resolve().as_uri() + "?mode=ro", uri=True, timeout=skill_deadline.sqlite_timeout())
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA query_only=ON")
            # Bound legacy scans as well as returned bytes; reads fail closed.
            steps = [0]
            def read_budget():
                steps[0] += 1
                return int(steps[0] > 1000 or skill_deadline.expired())
            db.set_progress_handler(read_budget, 1000)
            db.execute("BEGIN")
            yield db
            skill_deadline.check()
        finally:
            db.close()

    def migrate(self) -> None:
        """Create schema atomically; rerunning is safe."""
        with _LOCK, self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)")
            applied = {row[0] for row in db.execute("SELECT version FROM schema_migrations")}
            from app.skill_authoring_schema import schema_ready
            extended = applied == set(range(1, AUTHORING_SCHEMA_VERSION + 1)) and schema_ready(db)
            if applied and not extended and (max(applied) > SCHEMA_VERSION or applied != set(range(1, max(applied) + 1))):
                raise ValueError("unsupported_or_incomplete_skill_schema")
            if 1 not in applied:
                schema_sql = """
                CREATE TABLE simulation_skills (
                    skill_id TEXT PRIMARY KEY,
                    scenario_type TEXT NOT NULL,
                    state_text TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('candidate','active','questioned','retired')),
                    quality_level TEXT NOT NULL CHECK(quality_level IN ('Q0','Q1','Q2','Q3','Q4')),
                    source_run_id TEXT NOT NULL,
                    validator_version TEXT NOT NULL,
                    success_count INTEGER NOT NULL DEFAULT 0,
                    failure_count INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE skill_versions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    skill_id TEXT NOT NULL REFERENCES simulation_skills(skill_id),
                    version INTEGER NOT NULL,
                    normalized_params TEXT NOT NULL,
                    code_template TEXT NOT NULL,
                    code_hash TEXT NOT NULL,
                    postconditions TEXT NOT NULL,
                    physics_evidence TEXT NOT NULL,
                    source_run_id TEXT NOT NULL,
                    validator_version TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(skill_id, version)
                );
                CREATE TABLE skill_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    skill_id TEXT NOT NULL REFERENCES simulation_skills(skill_id),
                    event_type TEXT NOT NULL,
                    detail TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE skill_retrievals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT,
                    query_text TEXT NOT NULL,
                    candidates_json TEXT NOT NULL,
                    latency_ms REAL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX idx_skills_status ON simulation_skills(status, quality_level);
                CREATE INDEX idx_skill_versions_hash ON skill_versions(code_hash);
                """
                # executescript implicitly commits an open transaction. These
                # simple DDL statements must stay inside BEGIN IMMEDIATE.
                for statement in schema_sql.split(";"):
                    if statement.strip():
                        db.execute(statement)
                db.execute("INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)", (1, _now()))
            if 2 not in applied:
                db.execute("ALTER TABLE simulation_skills ADD COLUMN fingerprint TEXT")
                db.execute("CREATE UNIQUE INDEX idx_skills_fingerprint ON simulation_skills(fingerprint)")
                db.execute("INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)", (2, _now()))

            if 3 not in applied:
                # Historical audit entries must survive lifecycle transitions.
                # Separate execute calls keep this migration in one transaction.
                db.execute("""CREATE TRIGGER skill_events_no_update
                    BEFORE UPDATE ON skill_events BEGIN
                    SELECT RAISE(ABORT, 'skill_events is append-only'); END""")
                db.execute("""CREATE TRIGGER skill_events_no_delete
                    BEFORE DELETE ON skill_events BEGIN
                    SELECT RAISE(ABORT, 'skill_events is append-only'); END""")
                db.execute("INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)", (3, _now()))

            if 4 not in applied:
                db.execute("ALTER TABLE simulation_skills ADD COLUMN preconditions TEXT NOT NULL DEFAULT '{}'")
                db.execute("ALTER TABLE skill_versions ADD COLUMN reference_skill_ids TEXT NOT NULL DEFAULT '[]'")
                db.execute("""CREATE TABLE skill_references (
                    skill_id TEXT NOT NULL REFERENCES simulation_skills(skill_id),
                    version INTEGER NOT NULL,
                    reference_skill_id TEXT NOT NULL REFERENCES simulation_skills(skill_id),
                    PRIMARY KEY(skill_id, version, reference_skill_id),
                    FOREIGN KEY(skill_id, version) REFERENCES skill_versions(skill_id, version)
                )""")
                db.execute("INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)", (4, _now()))

            if 5 not in applied:
                # SQLite REPLACE bypasses DELETE triggers when recursive_triggers
                # is off. Guard duplicate IDs before insertion as well.
                db.execute("""CREATE TRIGGER skill_events_no_replace
                    BEFORE INSERT ON skill_events
                    WHEN EXISTS (SELECT 1 FROM skill_events WHERE id=NEW.id)
                    BEGIN SELECT RAISE(ABORT, 'skill_events is append-only'); END""")
                db.execute("INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)", (5, _now()))

            if 6 not in applied:
                db.execute("ALTER TABLE simulation_skills ADD COLUMN archived INTEGER NOT NULL DEFAULT 0 CHECK(archived IN (0,1))")
                db.execute("INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)", (6, _now()))

            if 7 not in applied:
                db.execute("""CREATE TABLE skill_feedback (
                    skill_id TEXT NOT NULL REFERENCES simulation_skills(skill_id),
                    run_id TEXT NOT NULL, actor TEXT NOT NULL, reason TEXT NOT NULL,
                    created_at TEXT NOT NULL, PRIMARY KEY(skill_id,run_id))""")
                for operation in ("UPDATE", "DELETE"):
                    db.execute(f"""CREATE TRIGGER skill_feedback_no_{operation.lower()}
                        BEFORE {operation} ON skill_feedback BEGIN
                        SELECT RAISE(ABORT, 'skill_feedback is append-only'); END""")
                db.execute("""CREATE TRIGGER skill_feedback_no_replace BEFORE INSERT ON skill_feedback
                    WHEN EXISTS (SELECT 1 FROM skill_feedback
                                 WHERE skill_id=NEW.skill_id AND run_id=NEW.run_id)
                    BEGIN SELECT RAISE(ABORT, 'skill_feedback is append-only'); END""")
                db.execute("INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)", (7, _now()))

            if 8 not in applied:
                for operation in ("UPDATE", "DELETE"):
                    db.execute(f"""CREATE TRIGGER skill_retrievals_no_{operation.lower()}
                        BEFORE {operation} ON skill_retrievals BEGIN
                        SELECT RAISE(ABORT, 'skill_retrievals is append-only'); END""")
                db.execute("""CREATE TRIGGER skill_retrievals_no_replace BEFORE INSERT ON skill_retrievals
                    WHEN EXISTS (SELECT 1 FROM skill_retrievals WHERE id=NEW.id)
                    BEGIN SELECT RAISE(ABORT, 'skill_retrievals is append-only'); END""")
                db.execute("INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)", (8, _now()))

            if 9 not in applied:
                db.execute("""CREATE INDEX idx_skills_retrieval ON simulation_skills(
                    scenario_type,status,archived,quality_level DESC,updated_at DESC,skill_id)""")
                db.execute("INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)", (9, _now()))

            if 10 not in applied:
                db.execute("""CREATE TABLE skill_generation_decisions (
                    decision_id TEXT PRIMARY KEY, nonce TEXT NOT NULL, scenario_type TEXT NOT NULL,
                    parameters_sha256 TEXT NOT NULL, context_sha256 TEXT NOT NULL,
                    created_ns INTEGER NOT NULL, expires_ns INTEGER NOT NULL,
                    metadata_snapshot_json TEXT NOT NULL, fallback_reason TEXT NOT NULL)""")
                db.execute("""CREATE TABLE skill_generation_consumptions (
                    decision_id TEXT PRIMARY KEY REFERENCES skill_generation_decisions(decision_id),
                    generation_run_id TEXT UNIQUE NOT NULL, consumed_ns INTEGER NOT NULL,
                    metadata_current_json TEXT NOT NULL, fallback_reason TEXT NOT NULL)""")
                for table, key in (("skill_generation_decisions","decision_id"),
                                   ("skill_generation_consumptions","decision_id")):
                    for operation in ("UPDATE","DELETE"):
                        db.execute(f"""CREATE TRIGGER {table}_no_{operation.lower()}
                            BEFORE {operation} ON {table} BEGIN SELECT RAISE(ABORT,'append-only'); END""")
                    db.execute(f"""CREATE TRIGGER {table}_no_replace BEFORE INSERT ON {table}
                        WHEN EXISTS(SELECT 1 FROM {table} WHERE {key}=NEW.{key})
                        BEGIN SELECT RAISE(ABORT,'append-only'); END""")
                db.execute("""CREATE TRIGGER skill_generation_consumptions_no_run_replace
                    BEFORE INSERT ON skill_generation_consumptions
                    WHEN EXISTS(SELECT 1 FROM skill_generation_consumptions
                                WHERE generation_run_id=NEW.generation_run_id)
                    BEGIN SELECT RAISE(ABORT,'append-only'); END""")
                db.execute("""CREATE INDEX idx_skill_version_source
                    ON skill_versions(skill_id,source_run_id,version DESC)""")
                db.execute("""CREATE INDEX idx_skill_event_source
                    ON skill_events(skill_id,event_type,
                        json_extract(CASE WHEN json_valid(detail) THEN detail ELSE '{}' END,'$.source_run_id'),
                        id DESC) WHERE event_type='candidate_observed'""")
                db.execute("""CREATE INDEX idx_skill_feedback_request
                    ON skill_events(skill_id,event_type,
                        json_extract(CASE WHEN json_valid(detail) THEN detail ELSE '{}' END,'$.request_id'),
                        id DESC) WHERE event_type='feedback_statement'""")
                db.execute("""CREATE INDEX idx_skill_feedback_run
                    ON skill_events(skill_id,event_type,
                        json_extract(CASE WHEN json_valid(detail) THEN detail ELSE '{}' END,'$.run_id'),
                        id DESC) WHERE event_type='feedback_statement'""")
                db.execute("""CREATE INDEX idx_skill_version_recent_source
                    ON skill_versions(skill_id,created_at DESC,source_run_id)""")
                db.execute("""CREATE INDEX idx_skill_event_recent_source
                    ON skill_events(skill_id,created_at DESC,id DESC)
                    WHERE event_type='candidate_observed'""")
                db.execute("""CREATE TABLE skill_authority_roots (
                    root_id TEXT NOT NULL, revision INTEGER NOT NULL,
                    action TEXT NOT NULL, valid_from_ns INTEGER NOT NULL,
                    valid_until_ns INTEGER NOT NULL, statement_json TEXT NOT NULL,
                    PRIMARY KEY(root_id,revision))""")
                db.execute("""CREATE TABLE skill_host_dispatches (
                    run_id TEXT PRIMARY KEY, nonce TEXT UNIQUE NOT NULL,
                    root_id TEXT NOT NULL, revision INTEGER NOT NULL,
                    context_json TEXT NOT NULL, context_sha256 TEXT NOT NULL,
                    approval_json TEXT NOT NULL, registered_ns INTEGER NOT NULL)""")
                for table in ("skill_authority_roots","skill_host_dispatches"):
                    for operation in ("UPDATE","DELETE"):
                        db.execute(f"""CREATE TRIGGER {table}_no_{operation.lower()} BEFORE {operation}
                            ON {table} BEGIN SELECT RAISE(ABORT,'append-only'); END""")
                db.execute("""CREATE TRIGGER skill_authority_roots_no_replace BEFORE INSERT ON skill_authority_roots
                    WHEN EXISTS(SELECT 1 FROM skill_authority_roots WHERE root_id=NEW.root_id AND revision=NEW.revision)
                    BEGIN SELECT RAISE(ABORT,'append-only'); END""")
                db.execute("""CREATE TRIGGER skill_host_dispatches_no_replace BEFORE INSERT ON skill_host_dispatches
                    WHEN EXISTS(SELECT 1 FROM skill_host_dispatches WHERE run_id=NEW.run_id OR nonce=NEW.nonce)
                    BEGIN SELECT RAISE(ABORT,'append-only'); END""")
                db.execute("""CREATE TABLE skill_host_verifications (
                    run_id TEXT PRIMARY KEY, nonce TEXT UNIQUE NOT NULL,
                    skill_id TEXT NOT NULL, version INTEGER NOT NULL,
                    root_id TEXT NOT NULL, revision INTEGER NOT NULL,
                    code_sha256 TEXT NOT NULL, parameters_sha256 TEXT NOT NULL,
                    observation_sha256 TEXT NOT NULL, stdout_sha256 TEXT NOT NULL,
                    validator_sha256 TEXT NOT NULL, numeric_result_json TEXT NOT NULL,
                    registered_ns INTEGER NOT NULL, event_id INTEGER NOT NULL REFERENCES skill_events(id))""")
                for operation in ("UPDATE","DELETE"):
                    db.execute(f"""CREATE TRIGGER skill_host_verifications_no_{operation.lower()} BEFORE {operation}
                        ON skill_host_verifications BEGIN SELECT RAISE(ABORT,'append-only'); END""")
                db.execute("""CREATE TRIGGER skill_host_verifications_no_replace BEFORE INSERT ON skill_host_verifications
                    WHEN EXISTS(SELECT 1 FROM skill_host_verifications WHERE run_id=NEW.run_id OR nonce=NEW.nonce)
                    BEGIN SELECT RAISE(ABORT,'append-only'); END""")
                db.execute("INSERT INTO schema_migrations(version,applied_at) VALUES (?,?)",(10,_now()))

            if 11 not in applied:
                db.execute("""CREATE TABLE skill_reference_capsules (
                    policy_sha256 TEXT PRIMARY KEY,
                    skill_id TEXT NOT NULL, version INTEGER NOT NULL,
                    parameters_sha256 TEXT NOT NULL, source_run_id TEXT NOT NULL,
                    valid_until_ns INTEGER NOT NULL, registered_ns INTEGER NOT NULL,
                    capsule_json TEXT NOT NULL, capsule_sha256 TEXT NOT NULL,
                    FOREIGN KEY(skill_id,version) REFERENCES skill_versions(skill_id,version),
                    FOREIGN KEY(source_run_id) REFERENCES skill_host_verifications(run_id))""")
                db.execute("""CREATE INDEX idx_reference_capsule_lookup ON skill_reference_capsules(
                    skill_id,version,parameters_sha256,registered_ns DESC)""")
                for operation in ("UPDATE","DELETE"):
                    db.execute(f"""CREATE TRIGGER skill_reference_capsules_no_{operation.lower()}
                        BEFORE {operation} ON skill_reference_capsules
                        BEGIN SELECT RAISE(ABORT,'append-only'); END""")
                db.execute("""CREATE TRIGGER skill_reference_capsules_no_replace
                    BEFORE INSERT ON skill_reference_capsules
                    WHEN EXISTS(SELECT 1 FROM skill_reference_capsules WHERE policy_sha256=NEW.policy_sha256)
                    BEGIN SELECT RAISE(ABORT,'append-only'); END""")
                db.execute("INSERT INTO schema_migrations(version,applied_at) VALUES (?,?)",(11,_now()))

            if 12 not in applied:
                db.execute("""CREATE TABLE skill_reference_dispatches (
                    run_id TEXT PRIMARY KEY REFERENCES skill_host_dispatches(run_id),
                    generation_run_id TEXT NOT NULL UNIQUE REFERENCES skill_generation_consumptions(generation_run_id),
                    decision_id TEXT NOT NULL REFERENCES skill_generation_decisions(decision_id),
                    skill_id TEXT NOT NULL, version INTEGER NOT NULL,
                    scenario_type TEXT NOT NULL, parameters_json TEXT NOT NULL,
                    original_context TEXT NOT NULL, registered_ns INTEGER NOT NULL,
                    FOREIGN KEY(skill_id,version) REFERENCES skill_versions(skill_id,version))""")
                db.execute("""CREATE TABLE skill_reference_executions (
                    run_id TEXT PRIMARY KEY REFERENCES skill_reference_dispatches(run_id),
                    observation_sha256 TEXT NOT NULL, stdout_sha256 TEXT NOT NULL,
                    registered_ns INTEGER NOT NULL)""")
                for table in ("skill_reference_dispatches","skill_reference_executions"):
                    for operation in ("UPDATE","DELETE"):
                        db.execute(f"""CREATE TRIGGER {table}_no_{operation.lower()} BEFORE {operation} ON {table}
                            BEGIN SELECT RAISE(ABORT,'append-only'); END""")
                    db.execute(f"""CREATE TRIGGER {table}_no_replace BEFORE INSERT ON {table}
                        WHEN EXISTS(SELECT 1 FROM {table} WHERE run_id=NEW.run_id)
                        BEGIN SELECT RAISE(ABORT,'append-only'); END""")
                db.execute("INSERT INTO schema_migrations(version,applied_at) VALUES (?,?)",(12,_now()))

            if 13 not in applied:
                db.execute("""CREATE INDEX idx_candidate_source_version ON skill_versions(
                    validator_version,source_run_id,skill_id)""")
                db.execute("""CREATE INDEX idx_candidate_source_observation ON skill_events(
                    json_extract(CASE WHEN json_valid(detail) THEN detail ELSE '{}' END,'$.evidence_binding.validator_version'),
                    json_extract(CASE WHEN json_valid(detail) THEN detail ELSE '{}' END,'$.source_run_id'),skill_id)
                    WHERE event_type='candidate_observed' AND json_valid(detail)""")
                db.execute("INSERT INTO schema_migrations(version,applied_at) VALUES (?,?)",(13,_now()))

            if 14 not in applied:
                db.execute("CREATE INDEX idx_skill_event_history ON skill_events(skill_id,id DESC)")
                db.execute("""CREATE INDEX idx_skill_revalidation_epoch ON skill_events(skill_id,id DESC)
                    WHERE event_type='revalidation_requested'""")
                db.execute("INSERT INTO schema_migrations(version,applied_at) VALUES (?,?)",(14,_now()))


            if 15 not in applied:
                db.execute("""CREATE UNIQUE INDEX idx_skill_dispatch_batch_case ON skill_host_dispatches(
                    root_id,
                    json_extract(CASE WHEN json_valid(context_json) THEN context_json ELSE '{}' END,'$.batch_id'),
                    json_extract(CASE WHEN json_valid(context_json) THEN context_json ELSE '{}' END,'$.case'))
                    WHERE json_valid(context_json)
                    AND json_type(context_json,'$.batch_id')='text'
                    AND json_type(context_json,'$.case')='text'""")
                db.execute("INSERT INTO schema_migrations(version,applied_at) VALUES (?,?)",(15,_now()))

            if 16 not in applied:
                db.execute("""CREATE TABLE skill_local_sources (
                    source_run_id TEXT PRIMARY KEY, skill_id TEXT NOT NULL, version INTEGER NOT NULL,
                    capsule_json TEXT NOT NULL, capsule_sha256 TEXT NOT NULL,
                    FOREIGN KEY(skill_id,version) REFERENCES skill_versions(skill_id,version))""")
                db.execute("CREATE INDEX idx_local_source_skill ON skill_local_sources(skill_id,version)")
                for op in ("UPDATE","DELETE"):
                    db.execute(f"""CREATE TRIGGER skill_local_sources_no_{op.lower()}
                        BEFORE {op} ON skill_local_sources BEGIN SELECT RAISE(ABORT,'append-only'); END""")
                db.execute("""CREATE TRIGGER skill_local_sources_no_replace BEFORE INSERT ON skill_local_sources
                    WHEN EXISTS(SELECT 1 FROM skill_local_sources WHERE source_run_id=NEW.source_run_id)
                    BEGIN SELECT RAISE(ABORT,'append-only'); END""")
                db.execute("INSERT INTO schema_migrations(version,applied_at) VALUES (?,?)",(16,_now()))

            if 17 not in applied:
                db.execute("""CREATE TABLE skill_local_reference_dispatches (
                    run_id TEXT PRIMARY KEY,
                    generation_run_id TEXT NOT NULL UNIQUE REFERENCES skill_generation_consumptions(generation_run_id),
                    decision_id TEXT NOT NULL REFERENCES skill_generation_decisions(decision_id),
                    skill_id TEXT NOT NULL, version INTEGER NOT NULL,
                    reference_json TEXT NOT NULL, reference_sha256 TEXT NOT NULL,
                    request_nonce TEXT NOT NULL UNIQUE, request_sha256 TEXT NOT NULL,
                    registered_ns INTEGER NOT NULL,
                    FOREIGN KEY(skill_id,version) REFERENCES skill_versions(skill_id,version))""")
                db.execute("""CREATE TABLE skill_local_reference_completions (
                    run_id TEXT PRIMARY KEY REFERENCES skill_local_reference_dispatches(run_id),
                    outcome TEXT NOT NULL CHECK(outcome IN ('verified','failed')),
                    capsule_sha256 TEXT NOT NULL, stdout_sha256 TEXT NOT NULL,
                    registered_ns INTEGER NOT NULL)""")
                for table in ("skill_local_reference_dispatches","skill_local_reference_completions"):
                    for op in ("UPDATE","DELETE"):
                        db.execute(f"""CREATE TRIGGER {table}_no_{op.lower()}
                            BEFORE {op} ON {table} BEGIN SELECT RAISE(ABORT,'append-only'); END""")
                    identity="run_id=NEW.run_id"
                    if table=="skill_local_reference_dispatches":
                        identity+=" OR generation_run_id=NEW.generation_run_id OR request_nonce=NEW.request_nonce"
                    db.execute(f"""CREATE TRIGGER {table}_no_replace BEFORE INSERT ON {table}
                        WHEN EXISTS(SELECT 1 FROM {table} WHERE {identity})
                        BEGIN SELECT RAISE(ABORT,'append-only'); END""")
                db.execute("INSERT INTO schema_migrations(version,applied_at) VALUES (?,?)",(17,_now()))

    def migrate_skill_authoring(self) -> None:
        """Explicit schema18 extension; operator must back up before deployment.

        Default migrate() still initializes only the original schema17. Existing
        verified skills/versions and their fingerprints are never rewritten.
        """
        from app.skill_authoring_schema import install, schema_ready
        self.migrate()
        with _LOCK, self._db_existing_write() as db:
            db.execute("BEGIN IMMEDIATE")
            versions = [r[0] for r in db.execute("SELECT version FROM schema_migrations ORDER BY version")]
            if versions == list(range(1, AUTHORING_SCHEMA_VERSION + 1)):
                if not schema_ready(db):
                    raise ValueError("incomplete_authoring_schema")
                return
            if versions != list(range(1, SCHEMA_VERSION + 1)):
                raise ValueError("unsupported_or_incomplete_skill_schema")
            install(db)
            db.execute("INSERT INTO schema_migrations(version,applied_at) VALUES (?,?)",
                       (AUTHORING_SCHEMA_VERSION, _now()))
            if not schema_ready(db):
                raise ValueError("incomplete_authoring_schema")

    def record_fallback_retrieval(self, run_id, nonce, scenario_type, parameters_sha256, reason):
        """Internal denied-lookup audit only. Never records trusted hits."""
        if (not isinstance(run_id,str) or re.fullmatch(r"[A-Za-z0-9-]{1,128}",run_id) is None
                or not isinstance(nonce,str) or re.fullmatch(r"[0-9a-f]{32}",nonce) is None
                or scenario_type not in SCENARIO_TYPES
                or not isinstance(parameters_sha256,str) or re.fullmatch(r"[0-9a-f]{64}",parameters_sha256) is None
                or reason not in {"retrieval_disabled","no_applicable_skill","skill_store_unavailable",
                                  "provenance_authentication_unavailable"}):
            raise ValueError("invalid_fallback_audit")
        payload = {"scenario_type":scenario_type,"parameters_sha256":parameters_sha256,
                   "request_nonce":nonce,"reason":reason}
        legacy_query = _canonical_json(payload)
        query = _canonical_json(dict(payload,audit_kind="fallback_diagnostic",
                                     actual_use_authenticated=False))
        self.migrate()
        with _LOCK,self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            prior=db.execute("SELECT id FROM skill_retrievals WHERE run_id=? AND query_text IN (?,?) LIMIT 1",
                             (run_id,query,legacy_query)).fetchone()
            if prior is not None:
                return {"audit_recorded":True,"duplicate":True,"audit_id":prior[0],"trusted_run":False,
                        "audit_kind":"fallback_diagnostic","actual_use_authenticated":False}
            row=db.execute("""INSERT INTO skill_retrievals(run_id,query_text,candidates_json,latency_ms,created_at)
                VALUES (?,?,'[]',NULL,?)""",(run_id,query,_now()))
            return {"audit_recorded":True,"duplicate":False,"audit_id":row.lastrowid,"trusted_run":False,
                    "audit_kind":"fallback_diagnostic","actual_use_authenticated":False}

    def create_candidate(
        self,
        *,
        scenario_type: str,
        state_text: str,
        normalized_params: Mapping[str, Any],
        code_template: str,
        postconditions: Mapping[str, Any],
        physics_evidence: Mapping[str, Any],
        source_run_id: str,
        validator_version: str,
        quality_level: str,
        preconditions: Mapping[str, Any] | None = None,
        reference_skill_ids: list[str] | None = None,
        idempotent_source_run: bool = False,
        _existing_connection: sqlite3.Connection | None = None,
        _prepared_spec: Mapping[str, Any] | None = None,
    ) -> str:
        """Persist an evidence-bound candidate; callers cannot activate it here."""
        # Internal authority consumers may reuse their existing writer snapshot.
        # Do not migrate, commit, or open a second connection in that path.
        from contextlib import nullcontext
        if _existing_connection is None:
            self.migrate()
        else:
            if not isinstance(_existing_connection, sqlite3.Connection) or not _existing_connection.in_transaction:
                raise ValueError("existing_authority_transaction_required")
            databases = _existing_connection.execute("PRAGMA database_list").fetchall()
            main = next((row[2] for row in databases if row[1] == "main"), None)
            if main is None or Path(main).resolve() != self.path.resolve():
                raise ValueError("existing_authority_database_mismatch")
        if not isinstance(state_text, str) or not state_text.strip() or not isinstance(code_template, str) or not code_template.strip():
            raise ValueError("scenario_type, state_text, and code_template are required")
        # Until a deterministic Q3/Q4 verifier binds evidence to a source run,
        # this internal API must not persist a caller-supplied high-trust label.
        if quality_level != "Q2":
            raise ValueError("candidate currently requires Q2; Q3/Q4 need verified physics evidence")
        if (not isinstance(source_run_id, str) or not 1 <= len(source_run_id.strip()) <= 128
                or not isinstance(validator_version, str) or not 1 <= len(validator_version.strip()) <= 128):
            raise ValueError("source_run_id and validator_version are required")
        declared = validate_preconditions({} if preconditions is None else preconditions)
        references = [] if reference_skill_ids is None else reference_skill_ids
        if (not isinstance(references, list) or len(references) > 3
                or any(not isinstance(v, str) or not v.strip() or len(v) > 128 for v in references)
                or len(set(references)) != len(references)):
            raise ValueError("reference_skill_ids must contain at most 3 unique IDs")
        prepared = None
        if _prepared_spec is not None:
            from app.skill_spec import prepare_skill_spec
            from app.skill_authoring_schema import schema_ready
            if (_existing_connection is None or not schema_ready(_existing_connection)
                    or validator_version != "general-helmholtz.v1"):
                raise ValueError("general_candidate_authority_required")
            prepared = prepare_skill_spec(_canonical_json(_prepared_spec))
            if (prepared["spec"]["scenario_type"] != scenario_type
                    or prepared["spec"]["parameters"] != dict(normalized_params)
                    or prepared["spec"]["code_template"] != code_template
                    or physics_evidence.get("spec") != prepared["spec"]):
                raise ValueError("general_candidate_spec_mismatch")
            normalized = prepared["normalized_parameters"]
        else:
            normalized = normalize_parameters(normalized_params)
        validate_candidate_content(code_template, {
            "state_text": state_text, "parameters": normalized,
            "preconditions": declared, "postconditions": postconditions,
            "physics_evidence": physics_evidence, "source_run_id": source_run_id,
            "validator_version": validator_version, "reference_skill_ids": references})
        if prepared is not None:
            expected = {"dimensions": 2, "parameters_sha256": hashlib.sha256(_canonical_json(normalized).encode()).hexdigest()}
            if declared != expected:raise ValueError("general_candidate_preconditions")
        elif declared and not preconditions_match(declared, normalized):
            raise ValueError("parameters do not match preconditions")
        fingerprint = prepared["case_fingerprint"] if prepared is not None else skill_fingerprint(scenario_type, normalized)
        skill_id, now = str(uuid.uuid4()), _now()
        code_hash = hashlib.sha256(code_template.encode("utf-8")).hexdigest()
        # Serialize the read/decide/write sequence in this gateway process.
        # The unique fingerprint index remains the cross-process safety net.
        lock_context = _LOCK if _existing_connection is None else nullcontext()
        database_context = self._db() if _existing_connection is None else nullcontext(_existing_connection)
        with lock_context, database_context as db:
            if _existing_connection is None:
                db.execute("BEGIN IMMEDIATE")
            existing = db.execute("SELECT skill_id FROM simulation_skills WHERE fingerprint = ?", (fingerprint,)).fetchone()
            if idempotent_source_run:
                # A changed normalization must not create a second skill for
                # the same recorded source under the same validator.
                source_skills = db.execute("""SELECT skill_id FROM skill_versions
                    WHERE validator_version=? AND source_run_id=? LIMIT 2""",
                    (validator_version,source_run_id)).fetchall()
                source_skills += db.execute("""SELECT skill_id FROM skill_events
                    WHERE event_type='candidate_observed' AND json_valid(detail)
                    AND json_extract(CASE WHEN json_valid(detail) THEN detail ELSE '{}' END,'$.evidence_binding.validator_version')=?
                    AND json_extract(CASE WHEN json_valid(detail) THEN detail ELSE '{}' END,'$.source_run_id')=? LIMIT 2""",
                    (validator_version,source_run_id)).fetchall()
                if source_skills and (existing is None or any(
                        item["skill_id"] != existing["skill_id"] for item in source_skills)):
                    raise ValueError("source_run_parameter_identity_conflict")
            for reference_id in references:
                if (existing and reference_id == existing["skill_id"]) or db.execute(
                        "SELECT 1 FROM simulation_skills WHERE skill_id=?", (reference_id,)).fetchone() is None:
                    raise ValueError("unknown or self reference_skill_id")
            if existing:
                current = db.execute("SELECT preconditions FROM simulation_skills WHERE skill_id=?", (existing["skill_id"],)).fetchone()[0]
                if json.loads(current) != declared:
                    raise ValueError("preconditions conflict with existing skill")
                skill_id = existing["skill_id"]
                version = db.execute("SELECT COALESCE(MAX(version), 0) FROM skill_versions WHERE skill_id = ?", (skill_id,)).fetchone()[0]
                existing_hash = db.execute("SELECT code_hash FROM skill_versions WHERE skill_id = ? AND version = ?", (skill_id, version)).fetchone()[0]
                if idempotent_source_run:
                    seen = db.execute("""SELECT 1 FROM skill_versions WHERE skill_id=? AND source_run_id=?
                        UNION ALL SELECT 1 FROM skill_events WHERE skill_id=? AND event_type='candidate_observed'
                        AND json_valid(detail) AND json_extract(CASE WHEN json_valid(detail) THEN detail ELSE '{}' END,'$.source_run_id')=? LIMIT 1""",
                        (skill_id, source_run_id, skill_id, source_run_id)).fetchone()
                    if seen:
                        refs = json.loads(db.execute(
                            "SELECT reference_skill_ids FROM skill_versions WHERE skill_id=? AND version=?",
                            (skill_id, version)).fetchone()[0])
                        if existing_hash != code_hash or sorted(refs) != sorted(references):
                            raise ValueError("source_run_content_conflict")
                        original = db.execute(
                            "SELECT postconditions,physics_evidence,validator_version FROM skill_versions WHERE skill_id=? AND source_run_id=? ORDER BY version DESC LIMIT 1",
                            (skill_id, source_run_id)).fetchone()
                        expected = {"postconditions": postconditions, "physics_evidence": physics_evidence,
                                    "validator_version": validator_version}
                        if original is not None:
                            recorded = {"postconditions": json.loads(original["postconditions"]),
                                        "physics_evidence": json.loads(original["physics_evidence"]),
                                        "validator_version": original["validator_version"]}
                        else:
                            observation = db.execute(
                                """SELECT detail FROM skill_events WHERE skill_id=? AND event_type='candidate_observed'
                                AND json_valid(detail) AND json_extract(CASE WHEN json_valid(detail) THEN detail ELSE '{}' END,'$.source_run_id')=?
                                ORDER BY id DESC LIMIT 1""", (skill_id, source_run_id)).fetchone()
                            detail = json.loads(observation["detail"]) if observation else {}
                            if "evidence_binding" not in detail:
                                raise ValueError("source_run_evidence_unavailable")
                            recorded = detail["evidence_binding"]
                        if _canonical_json(recorded) != _canonical_json(expected):
                            raise ValueError("source_run_evidence_conflict")
                        return skill_id
                db.execute("UPDATE simulation_skills SET success_count = success_count + 1, updated_at = ? WHERE skill_id = ?", (now, skill_id))
                previous_refs = json.loads(db.execute(
                    "SELECT reference_skill_ids FROM skill_versions WHERE skill_id=? AND version=?",
                    (skill_id, version)).fetchone()[0])
                same_content = True
                if prepared is not None:
                    previous_evidence = db.execute(
                        "SELECT physics_evidence FROM skill_versions WHERE skill_id=? AND version=?",
                        (skill_id, version)).fetchone()[0]
                    previous_spec = prepare_skill_spec(_canonical_json(json.loads(previous_evidence)["spec"]))
                    same_content = previous_spec["content_sha256"] == prepared["content_sha256"]
                if existing_hash == code_hash and sorted(previous_refs) == sorted(references) and same_content:
                    db.execute("INSERT INTO skill_events(skill_id,event_type,detail,created_at) VALUES (?, 'candidate_observed', ?, ?)", (skill_id, _canonical_json({"source_run_id": source_run_id, "version": version,
                        "code_hash": code_hash, "evidence_binding": {"postconditions": postconditions,
                        "physics_evidence": physics_evidence, "validator_version": validator_version}}), now))
                    return skill_id
                next_version = version + 1
                # A new Q2 candidate must not inherit an older version's active
                # trust label. Keep archived/retired restrictions and retain all
                # historical evidence; revalidation is required before reuse.
                db.execute("""UPDATE simulation_skills
                    SET status=CASE WHEN status='active' THEN 'questioned' ELSE status END,
                        quality_level='Q2',source_run_id=?,validator_version=?
                    WHERE skill_id=?""", (source_run_id, validator_version, skill_id))
                db.execute(
                    "INSERT INTO skill_versions(skill_id,version,normalized_params,code_template,code_hash,postconditions,physics_evidence,source_run_id,validator_version,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (skill_id, next_version, _canonical_json(normalized), code_template, code_hash, _canonical_json(postconditions), _canonical_json(physics_evidence), source_run_id, validator_version, now),
                )
                db.execute("UPDATE skill_versions SET reference_skill_ids=? WHERE skill_id=? AND version=?",
                           (json.dumps(references), skill_id, next_version))
                db.executemany("INSERT INTO skill_references VALUES (?,?,?)",
                               [(skill_id, next_version, ref) for ref in references])
                db.execute("INSERT INTO skill_events(skill_id,event_type,detail,created_at) VALUES (?, 'candidate_versioned', ?, ?)", (skill_id, json.dumps({"version": next_version}), now))
                return skill_id
            db.execute(
                "INSERT INTO simulation_skills(skill_id,scenario_type,state_text,status,quality_level,source_run_id,validator_version,success_count,failure_count,created_at,updated_at,fingerprint) VALUES (?, ?, ?, 'candidate', ?, ?, ?, 1, 0, ?, ?, ?)",
                (skill_id, scenario_type, state_text.strip(), quality_level, source_run_id, validator_version, now, now, fingerprint),
            )
            db.execute(
                "INSERT INTO skill_versions(skill_id,version,normalized_params,code_template,code_hash,postconditions,physics_evidence,source_run_id,validator_version,created_at) VALUES (?,1,?,?,?,?,?,?,?,?)",
                (skill_id, _canonical_json(normalized), code_template, code_hash,
                 _canonical_json(postconditions), _canonical_json(physics_evidence), source_run_id, validator_version, now),
            )
            db.execute("UPDATE simulation_skills SET preconditions=? WHERE skill_id=?", (_canonical_json(declared), skill_id))
            db.execute("UPDATE skill_versions SET reference_skill_ids=? WHERE skill_id=? AND version=1",
                       (json.dumps(references), skill_id))
            db.executemany("INSERT INTO skill_references VALUES (?,?,?)",
                           [(skill_id, 1, ref) for ref in references])
            db.execute("INSERT INTO skill_events(skill_id,event_type,detail,created_at) VALUES (?, 'candidate_created', ?, ?)",
                       (skill_id, json.dumps({"quality_level": quality_level}), now))
        return skill_id

    def get_skill(self, skill_id: str) -> dict[str, Any] | None:
        self.migrate()
        with self._db() as db:
            row = db.execute("SELECT * FROM simulation_skills WHERE skill_id = ?", (skill_id,)).fetchone()
        return dict(row) if row else None

    def read_detail(self, skill_id: str, *, version: int | None = None) -> dict[str, Any]:
        """Bounded read-only view; recorded grades never authenticate provenance."""
        if not isinstance(skill_id, str) or not 1 <= len(skill_id) <= 128:
            raise ValueError("invalid_skill_id")
        if version is not None and (type(version) is not int or version < 1 or version > 2147483647):
            raise ValueError("invalid_version")
        unavailable = {"available": False, "item": None, "trust_verified": False}
        try:
            with self._read_db() as db:
                row = db.execute("""SELECT skill_id, scenario_type, substr(state_text,1,2049) state_text,
                    status, quality_level, substr(source_run_id,1,129) source_run_id,
                    substr(validator_version,1,129) validator_version, archived,
                    substr(preconditions,1,4097) preconditions, updated_at, failure_count
                    FROM simulation_skills WHERE skill_id=?""", (skill_id,)).fetchone()
                if row is None:
                    return {**unavailable, "available": True}
                item = dict(row)
                if (len(item["state_text"]) > 2048 or len(item["preconditions"]) > 4096
                        or len(item["source_run_id"]) > 128 or len(item["validator_version"]) > 128):
                    return unavailable
                item["preconditions"] = validate_preconditions(json.loads(item["preconditions"]))
                item["archived"] = bool(item["archived"])
                selected = db.execute("""SELECT version, substr(normalized_params,1,8193) normalized_params,
                    substr(code_template,1,20001) code_template, code_hash,
                    substr(reference_skill_ids,1,1025) reference_skill_ids,
                    substr(source_run_id,1,129) source_run_id,
                    substr(validator_version,1,129) validator_version,
                    substr(physics_evidence,1,64001) physics_evidence, created_at FROM skill_versions
                    WHERE skill_id=? AND (? IS NULL OR version=?) ORDER BY version DESC LIMIT 1""",
                    (skill_id, version, version)).fetchone()
                if selected is None:
                    return {**unavailable, "available": True}
                selected = dict(selected)
                if (len(selected["normalized_params"]) > 8192 or len(selected["reference_skill_ids"]) > 1024
                        or len(selected["code_template"].encode()) > 20000
                        or len(selected["source_run_id"]) > 128 or len(selected["validator_version"]) > 128):
                    return unavailable
                evidence_raw = selected.pop("physics_evidence")
                if selected["validator_version"] == "general-helmholtz.v1":
                    from app.skill_spec import prepare_skill_spec
                    if len(evidence_raw.encode()) > 64000:return unavailable
                    prepared = prepare_skill_spec(_canonical_json(json.loads(evidence_raw)["spec"]))
                    if (prepared["spec"]["parameters"] != json.loads(selected["normalized_params"])
                            or prepared["spec"]["code_template"] != selected["code_template"]
                            or prepared["spec"]["scenario_type"] != item["scenario_type"]):return unavailable
                    selected["normalized_params"] = prepared["normalized_parameters"]
                else:
                    selected["normalized_params"] = normalize_parameters(json.loads(selected["normalized_params"]))
                refs = json.loads(selected["reference_skill_ids"])
                if (not isinstance(refs, list) or len(refs) > 3 or any(not isinstance(x,str) for x in refs)
                        or len(set(refs)) != len(refs)):
                    return unavailable
                mirrored = [r[0] for r in db.execute(
                    "SELECT reference_skill_id FROM skill_references WHERE skill_id=? AND version=? ORDER BY reference_skill_id",
                    (skill_id, selected["version"]))]
                if sorted(refs) != mirrored:
                    return unavailable
                selected["reference_skill_ids"] = refs
                code = selected["code_template"]
                if hashlib.sha256(code.encode()).hexdigest() != selected["code_hash"]:
                    return unavailable
                validate_candidate_content(code, {"skill": item, "version": selected})
                item["selected_version"] = selected
                item["audit_revision"] = db.execute(
                    "SELECT COALESCE(MAX(id),0) FROM skill_events WHERE skill_id=?", (skill_id,)).fetchone()[0]
                item["trust_explanation"] = "此页展示保存的记录，不是复用许可；服务端在检索和生成时重核本机来源或独立宿主证据、参数、物理检查与撤回。跨主机认证及实际用户使用须另行验证。"
                item["reuse_statistics"] = None
                item["statistics_explanation"] = "历史观察次数不是可信成功率，暂无 Wilson 下限。"
                if len(json.dumps(item, ensure_ascii=False).encode()) > 32000:
                    return unavailable
                return {"available": True, "item": item, "trust_verified": False}
        except (sqlite3.Error, OSError, ValueError, TypeError, KeyError, RecursionError):
            return unavailable

    def read_events(self, skill_id: str, *, after: int = 0, limit: int = 20, until: int | None = None) -> dict[str, Any]:
        """Ascending keyset page, no mutations and no raw legacy event leakage."""
        if not isinstance(skill_id, str) or not 1 <= len(skill_id) <= 128:
            raise ValueError("invalid_skill_id")
        if type(after) is not int or not 0 <= after <= 9223372036854775807:
            raise ValueError("invalid_cursor")
        if type(limit) is not int or not 1 <= limit <= 50:
            raise ValueError("invalid_limit")
        if until is not None and (type(until) is not int or not after <= until <= 9223372036854775807):
            raise ValueError("invalid_snapshot")
        unavailable = {"available": False, "items": [], "next_after": after, "has_more": False, "snapshot_until": until}
        try:
            with self._read_db() as db:
                if until is None:
                    until = max(after, db.execute("SELECT COALESCE(MAX(id),0) FROM skill_events WHERE skill_id=?", (skill_id,)).fetchone()[0])
                rows = db.execute("""SELECT id, substr(event_type,1,129) event_type,
                    substr(detail,1,4097) detail, substr(created_at,1,129) created_at
                    FROM skill_events WHERE skill_id=? AND id>? AND id<=? ORDER BY id LIMIT ?""",
                    (skill_id, after, until, limit + 1)).fetchall()
                items = []
                for row in rows[:limit]:
                    item = dict(row)
                    try:
                        if len(item["detail"]) > 4096 or len(item["event_type"]) > 128 or len(item["created_at"]) > 128:
                            raise ValueError("oversize")
                        detail = json.loads(item["detail"])
                        validate_candidate_content("pass", {"event": item, "parsed": detail})
                        item["detail"] = detail
                        item["content_hidden"] = False
                    except (ValueError, TypeError, RecursionError):
                        item = {"id": row["id"], "event_type": "content_hidden",
                                "detail": None, "created_at": None, "content_hidden": True}
                    if len(json.dumps(items + [item], ensure_ascii=False).encode()) > 32000:
                        break
                    items.append(item)
                cursor = items[-1]["id"] if items else after
                return {"available": True, "items": items, "next_after": cursor,
                        "has_more": len(rows) > len(items), "snapshot_until": until}
        except (sqlite3.Error, OSError):
            return unavailable

    def list_metadata(self, *, limit: int = 20, include_archived: bool = False) -> dict[str, Any]:
        """Bounded read-only summaries, never code/evidence or trust credentials."""
        if type(limit) is not int or not 1 <= limit <= 50 or type(include_archived) is not bool:
            raise ValueError("invalid_skill_list_request")
        try:
            with self._read_db() as db:
                rows = db.execute("""SELECT skill_id,scenario_type,state_text,status,quality_level,
                    source_run_id,preconditions,archived,updated_at,failure_count
                    FROM simulation_skills WHERE (? OR archived=0)
                    ORDER BY updated_at DESC,skill_id LIMIT ?""", (include_archived, limit)).fetchall()
                items = []
                for row in rows:
                    item = dict(row)
                    item["preconditions"] = validate_preconditions(json.loads(item["preconditions"]))
                    item["archived"] = bool(item["archived"])
                    # Legacy records may predate content checks. Refuse known
                    # unsafe metadata without returning the rejected contents.
                    validate_candidate_content("pass", item)
                    if len(json.dumps(items + [item], ensure_ascii=False).encode()) > 32000:
                        break
                    items.append(item)
                return {"available": True, "items": items, "trust_verified": False}
        except (sqlite3.Error, OSError, ValueError, TypeError):
            return {"available": False, "items": [], "trust_verified": False}

    def read_execution_links(self, run_ids: list[str]) -> dict[str, dict[str, Any]]:
        """Return bounded, execution-backed links for the dashboard.

        A link proves only that the stored fixed reference completed verification.
        It does not authenticate a human user or model adoption. Ambiguous rows
        fail closed and are omitted.
        """
        if not isinstance(run_ids, (list, tuple)) or len(run_ids) > 500:
            raise ValueError("invalid_execution_link_request")
        unique = []
        for run_id in run_ids:
            if not isinstance(run_id, str) or not 1 <= len(run_id) <= 128:
                raise ValueError("invalid_execution_run_id")
            if run_id not in unique:
                unique.append(run_id)
        if not unique:
            return {}
        placeholders = ",".join("?" for _ in unique)
        try:
            with self._read_db() as db:
                rows = db.execute(f"""SELECT d.run_id,d.skill_id,d.version
                    FROM skill_local_reference_dispatches d
                    JOIN skill_local_reference_completions c ON c.run_id=d.run_id
                    WHERE c.outcome='verified' AND d.run_id IN ({placeholders})
                    UNION ALL
                    SELECT d.run_id,d.skill_id,d.version
                    FROM skill_reference_dispatches d
                    JOIN skill_reference_executions e ON e.run_id=d.run_id
                    WHERE d.run_id IN ({placeholders})""", (*unique, *unique)).fetchall()
            grouped: dict[str, set[tuple[str, int]]] = {}
            for row in rows:
                if (not isinstance(row["skill_id"], str) or not 1 <= len(row["skill_id"]) <= 128
                        or type(row["version"]) is not int or row["version"] < 1):
                    continue
                grouped.setdefault(row["run_id"], set()).add((row["skill_id"], row["version"]))
            result = {}
            for run_id, identities in grouped.items():
                if len(identities) != 1:
                    continue
                skill_id, version = identities.pop()
                result[run_id] = {
                    "skill_id": skill_id,
                    "version": version,
                    "stage": "recipe_execution_verified",
                    "execution_reuse_verified": True,
                    "actual_use_authenticated": False,
                    "model_adoption_authenticated": False,
                }
            return result
        except (sqlite3.Error, OSError, ValueError, TypeError):
            return {}

    def read_feedback_state(self, skill_id: str, run_id: str) -> dict[str, Any]:
        """Bounded read-only declaration status, without reasons or identities."""
        if any(not isinstance(v, str) or not 1 <= len(v) <= 128 for v in (skill_id, run_id)):
            raise ValueError("invalid_feedback_reference")
        denied = {"available": False, "confirmed": False, "event_id": None,
                  "identity_authenticated": False, "trust_verified": False}
        try:
            with self._read_db() as db:
                if db is None or db.execute("SELECT 1 FROM simulation_skills WHERE skill_id=?",
                                           (skill_id,)).fetchone() is None:
                    return denied
                row = db.execute("""SELECT id,json_extract(detail,'$.confirmed') AS confirmed,
                    json_type(detail,'$.confirmed') AS kind FROM skill_events
                    WHERE skill_id=? AND event_type='feedback_statement' AND json_valid(detail)
                    AND json_extract(CASE WHEN json_valid(detail) THEN detail ELSE '{}' END,'$.run_id')=? ORDER BY id DESC LIMIT 1""",
                    (skill_id, run_id)).fetchone()
                if row and row["kind"] not in {"true", "false"}:
                    return denied
                return dict(denied, available=True, confirmed=bool(row["confirmed"]) if row else False,
                            event_id=row["id"] if row else None)
        except (sqlite3.Error, OSError, ValueError, TypeError):
            return denied

    def _source_association_connection(self, db, skill_id, run_id):
        steps = [0]
        def budget():
            steps[0] += 1000
            return int(steps[0] > 1000000)
        db.set_progress_handler(budget,1000)
        try:
            return self._source_association_snapshot(db,skill_id,run_id)
        finally:
            db.set_progress_handler(None,0)

    def _source_association_snapshot(self, db, skill_id, run_id):
        """Audit historical evidence identity only, never actual user consumption.

        Multiple distinct versions for one run are ambiguous. Old observation
        events without a version remain explicitly unbound rather than being
        retroactively attached to the newest version.
        """
        versions = db.execute(
            "SELECT version,code_hash,physics_evidence,validator_version,postconditions FROM skill_versions WHERE skill_id=? AND source_run_id=? LIMIT 101",
            (skill_id,run_id)).fetchall()
        events = db.execute("""SELECT detail FROM skill_events WHERE skill_id=?
            AND event_type='candidate_observed' AND json_valid(detail)
            AND json_extract(CASE WHEN json_valid(detail) THEN detail ELSE '{}' END,'$.source_run_id')=? LIMIT 101""",(skill_id,run_id)).fetchall()
        if len(versions)+len(events)>100:
            raise ValueError("run_association_budget")
        bindings = []
        legacy = False
        for row in versions:
            binding = {"physics_evidence":json.loads(row["physics_evidence"]),
                       "postconditions":json.loads(row["postconditions"]),
                       "validator_version":row["validator_version"]}
            bindings.append((row["version"],row["code_hash"],
                hashlib.sha256(_canonical_json(binding).encode()).hexdigest()))
        for row in events:
            detail = json.loads(row["detail"])
            if (type(detail.get("version")) is not int or detail["version"]<1
                    or type(detail.get("code_hash")) is not str
                    or type(detail.get("evidence_binding")) is not dict):
                legacy = True
                continue
            version = db.execute("SELECT code_hash FROM skill_versions WHERE skill_id=? AND version=?",
                                 (skill_id,detail["version"])).fetchone()
            if version is None or version["code_hash"]!=detail["code_hash"]:
                raise ValueError("run_association_conflict")
            bindings.append((detail["version"],detail["code_hash"],
                hashlib.sha256(_canonical_json(detail["evidence_binding"]).encode()).hexdigest()))
        if not versions and not events:
            consistency=self._consistency_case_association(db,skill_id,run_id)
            if consistency is not None:return consistency
            # Indexed immutable execution association, never a caller run/code
            # claim. It identifies executed reuse but not the human actor.
            local_run=db.execute("""SELECT d.version,v.code_hash,d.reference_sha256,
                    c.capsule_sha256,c.stdout_sha256,d.generation_run_id,d.decision_id
                FROM skill_local_reference_dispatches d
                JOIN skill_local_reference_completions c ON c.run_id=d.run_id
                JOIN skill_versions v ON v.skill_id=d.skill_id AND v.version=d.version
                WHERE d.run_id=? AND d.skill_id=? AND c.outcome='verified'
                    AND v.validator_version='general-helmholtz.v1'""",(run_id,skill_id)).fetchone()
            if local_run is not None:
                for key in ("code_hash","reference_sha256","capsule_sha256","stdout_sha256"):
                    if type(local_run[key]) is not str or not re.fullmatch("[0-9a-f]{64}",local_run[key]):
                        raise ValueError("general_execution_association_invalid")
                proof={k:local_run[k] for k in ("reference_sha256","capsule_sha256","stdout_sha256",
                                               "generation_run_id","decision_id")}
                return dict(kind="general_validated_reference_execution",version=local_run["version"],
                    code_sha256=local_run["code_hash"],evidence_binding_sha256=hashlib.sha256(_canonical_json(proof).encode()).hexdigest(),
                    execution_reuse_verified=True,actual_use_authenticated=False)
            executed=db.execute("""SELECT d.version,v.code_hash,e.observation_sha256,e.stdout_sha256,
                    d.generation_run_id,d.decision_id
                FROM skill_reference_dispatches d
                JOIN skill_reference_executions e ON e.run_id=d.run_id
                JOIN skill_versions v ON v.skill_id=d.skill_id AND v.version=d.version
                WHERE d.run_id=? AND d.skill_id=?""",(run_id,skill_id)).fetchone()
            if executed is None:raise ValueError("run_not_associated_with_skill")
            for key in ("code_hash","observation_sha256","stdout_sha256"):
                if type(executed[key]) is not str or not re.fullmatch("[0-9a-f]{64}",executed[key]):
                    raise ValueError("execution_association_invalid")
            evidence={k:executed[k] for k in ("observation_sha256","stdout_sha256","generation_run_id","decision_id")}
            return {"kind":"independently_observed_reference_execution","version":executed["version"],
                "code_sha256":executed["code_hash"],
                "evidence_binding_sha256":hashlib.sha256(_canonical_json(evidence).encode()).hexdigest(),
                "execution_reuse_verified":True,"actual_use_authenticated":False}
        identities = set(bindings)
        if len(identities)>1:
            raise ValueError("run_association_ambiguous")
        if legacy or not identities:
            return {"kind":"legacy_unbound_source_observation","version":None,
                    "actual_use_authenticated":False}
        version,code_hash,evidence_hash = identities.pop()
        association={"kind":"source_observation","version":version,"code_sha256":code_hash,
                     "evidence_binding_sha256":evidence_hash,"actual_use_authenticated":False}
        host=db.execute("""SELECT version,code_sha256,observation_sha256,stdout_sha256,
            parameters_sha256,root_id,revision FROM skill_host_verifications
            WHERE run_id=? AND skill_id=?""",(run_id,skill_id)).fetchone()
        if host is not None:
            if host["version"]!=version or host["code_sha256"]!=code_hash:
                raise ValueError("host_source_association_conflict")
            for key in ("observation_sha256","stdout_sha256","parameters_sha256"):
                if type(host[key]) is not str or not re.fullmatch("[0-9a-f]{64}",host[key]):
                    raise ValueError("host_source_association_invalid")
            association["host_recorded_diagnostic"]={
                "observation_sha256":host["observation_sha256"],"stdout_sha256":host["stdout_sha256"],
                "parameters_sha256":host["parameters_sha256"],"root_id":host["root_id"],
                "revision":host["revision"],"trust_verified":False}
        return association

    def _consistency_case_association(self,db,skill_id,run_id):
        """Historical auxiliary experiment identity; never a use/trust grant.

        Run primary key -> batch/case unique index -> base receipt primary key.
        No historical scan and no inferred latest version. Root expiry does not
        erase a historical user's ability to withdraw a recorded observation.
        """
        from scripts.validation.approved_host_root import ROOT_ID
        from scripts.validation.host_forward_signature import unique,reject
        dispatch=db.execute("SELECT * FROM skill_host_dispatches WHERE run_id=?",(run_id,)).fetchone()
        if dispatch is None or dispatch["root_id"]!=ROOT_ID+"_consistency":return None
        if len(dispatch["context_json"].encode())>8192 or len(dispatch["approval_json"].encode())>8192:
            raise ValueError("consistency_association_budget")
        context=json.loads(dispatch["context_json"],object_pairs_hook=unique,parse_constant=reject)
        case=context.get("case");batch=context.get("batch_id")
        if (case not in {"base","grid","time","equivalent"} or type(batch) is not str
                or str(uuid.UUID(batch))!=batch or context.get("run_id")!=run_id
                or context.get("nonce")!=dispatch["nonce"]):
            raise ValueError("consistency_association_identity")
        base=db.execute("""SELECT run_id FROM skill_host_dispatches WHERE root_id=?
            AND json_valid(context_json) AND json_type(context_json,'$.batch_id')='text'
            AND json_type(context_json,'$.case')='text'
            AND json_extract(CASE WHEN json_valid(context_json) THEN context_json ELSE '{}' END,'$.batch_id')=?
            AND json_extract(CASE WHEN json_valid(context_json) THEN context_json ELSE '{}' END,'$.case')='base'
            LIMIT 2""",(dispatch["root_id"],batch)).fetchall()
        if len(base)!=1:raise ValueError("unique_consistency_batch_base_required")
        receipt=db.execute("SELECT * FROM skill_host_verifications WHERE run_id=? AND skill_id=?",
            (base[0]["run_id"],skill_id)).fetchone()
        if receipt is None:return None
        if len(receipt["numeric_result_json"].encode())>16384:raise ValueError("consistency_receipt_budget")
        detail=json.loads(receipt["numeric_result_json"],object_pairs_hook=unique,parse_constant=reject)
        event=db.execute("SELECT skill_id,event_type,detail FROM skill_events WHERE id=?",(receipt["event_id"],)).fetchone()
        if (event is None or event["skill_id"]!=skill_id or event["event_type"]!="consistency_profile_diagnostic"
                or event["detail"]!=receipt["numeric_result_json"] or detail.get("batch_id")!=batch
                or type(detail.get("version")) is not int or detail["version"]!=receipt["version"]
                or detail.get("physical_acceptance_passed") is not False):
            raise ValueError("consistency_receipt_identity")
        proof=detail["source"][case]
        if proof["run_id"]!=run_id or proof["nonce"]!=dispatch["nonce"]:
            raise ValueError("consistency_case_receipt_mismatch")
        approval=json.loads(dispatch["approval_json"],object_pairs_hook=unique,parse_constant=reject)["payload"]
        if (approval["case"]!=case or approval["batch_id"]!=batch or approval["run_id"]!=run_id
                or approval["nonce"]!=dispatch["nonce"] or approval["root_id"]!=dispatch["root_id"]
                or approval["revision"]!=receipt["revision"]):
            raise ValueError("consistency_case_approval_mismatch")
        code=approval["code_sha256"]
        for value in (code,proof["observation_sha256"],proof["stdout_sha256"]):
            if type(value) is not str or not re.fullmatch("[0-9a-f]{64}",value):
                raise ValueError("consistency_case_digest")
        version=db.execute("SELECT code_hash FROM skill_versions WHERE skill_id=? AND version=?",
            (skill_id,receipt["version"])).fetchone()
        if version is None or version["code_hash"]!=receipt["code_sha256"]:
            raise ValueError("consistency_original_version_changed")
        return dict(kind="source_consistency_experiment",version=receipt["version"],
            code_sha256=code,version_code_sha256=version["code_hash"],batch_id=batch,case=case,
            evidence_binding_sha256=hashlib.sha256(_canonical_json(proof).encode()).hexdigest(),
            host_recorded_diagnostic=dict(observation_sha256=proof["observation_sha256"],
                stdout_sha256=proof["stdout_sha256"],root_id=dispatch["root_id"],
                revision=receipt["revision"],trust_verified=False),actual_use_authenticated=False)

    def record_feedback_statement(self, skill_id: str, run_id: str, *, confirmed: bool,
                                  request_id: str, actor: str, reason: str) -> dict[str, Any]:
        """Internal user statement, not identity authentication or physical trust.

        Request IDs are scoped to a skill; conflicting retries are rejected.
        Withdrawal conservatively questions active skills, never reactivates.
        Existing append-only events preserve confirmation/withdrawal history.
        """
        if (type(confirmed) is not bool or actor not in {"portal-admin", "internal-system"}
                or any(not isinstance(v, str) or not 1 <= len(v) <= 128
                       for v in (skill_id, run_id, request_id))
                or not isinstance(reason, str) or not 1 <= len(reason.strip()) <= 256):
            raise ValueError("invalid_feedback_statement")
        validate_candidate_content("pass", {"reason": reason, "request_id": request_id})
        payload = {"run_id": run_id, "confirmed": confirmed, "request_id": request_id,
                   "actor": actor, "reason": reason}
        self.migrate()
        with _LOCK, self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            skill = db.execute("SELECT status FROM simulation_skills WHERE skill_id=?", (skill_id,)).fetchone()
            if skill is None:
                raise ValueError("unknown_skill")
            prior = db.execute("""SELECT id,detail FROM skill_events WHERE skill_id=?
                AND event_type='feedback_statement' AND json_valid(detail)
                AND json_extract(CASE WHEN json_valid(detail) THEN detail ELSE '{}' END,'$.request_id')=? LIMIT 1""", (skill_id, request_id)).fetchone()
            if prior:
                recorded = json.loads(prior["detail"])
                if {key:recorded.get(key) for key in payload} != payload:
                    raise ValueError("feedback_request_conflict")
                return {"recorded": False, "duplicate": True, "event_id": prior["id"],
                        "trusted_run": False, "identity_authenticated": False}
            association = self._source_association_connection(db,skill_id,run_id)
            payload["source_association"] = association
            latest = db.execute("""SELECT detail FROM skill_events WHERE skill_id=?
                AND event_type='feedback_statement' AND json_valid(detail)
                AND json_extract(CASE WHEN json_valid(detail) THEN detail ELSE '{}' END,'$.run_id')=? ORDER BY id DESC LIMIT 1""",
                (skill_id, run_id)).fetchone()
            if not confirmed and (not latest or json.loads(latest["detail"]).get("confirmed") is not True):
                raise ValueError("no_current_confirmation")
            now = _now()
            event = db.execute("INSERT INTO skill_events(skill_id,event_type,detail,created_at) VALUES (?,?,?,?)",
                (skill_id, "feedback_statement", _canonical_json(payload), now))
            if not confirmed and skill["status"] == "active":
                db.execute("UPDATE simulation_skills SET status='questioned',updated_at=? WHERE skill_id=?",
                           (now, skill_id))
            return {"recorded": True, "duplicate": False, "event_id": event.lastrowid,
                    "trusted_run": False, "identity_authenticated": False}

    def _recent_source_runs_connection(self, db, skill_id):
        """Exact latest-three distinct runs, or a bounded conservative refusal.

        Each ordered source has an indexed prefix of 101 rows. If its prefix
        cannot establish three distinct runs, truncated older rows could alter
        the policy: reject instead of pretending this is the complete history.
        """
        versions=db.execute("""SELECT source_run_id AS run_id,created_at AS observed_at
            FROM skill_versions WHERE skill_id=?
            ORDER BY created_at DESC,source_run_id LIMIT 101""",(skill_id,)).fetchall()
        events=db.execute("""SELECT detail,created_at AS observed_at FROM skill_events
            WHERE skill_id=? AND event_type='candidate_observed'
            ORDER BY created_at DESC,id DESC LIMIT 101""",(skill_id,)).fetchall()
        combined=[(row["observed_at"],row["run_id"]) for row in versions]
        for row in events:
            try:
                detail=json.loads(row["detail"])
                run=detail.get("source_run_id") if type(detail) is dict else None
            except (ValueError,TypeError,RecursionError):
                continue
            if type(run) is str:combined.append((row["observed_at"],run))
        latest={}
        for stamp,run in combined:
            if type(run) is str and (run not in latest or stamp>latest[run]):latest[run]=stamp
        ordered=sorted(latest,key=lambda run:(latest[run],run),reverse=True)
        if len(versions)==101 or len(events)==101:
            # Verify every omitted source is strictly older than the third
            # selected timestamp; ties require older run IDs to resolve.
            if len(ordered)<3:
                raise ValueError("recent_run_budget")
            cutoff=latest[ordered[2]]
            if ((len(versions)==101 and versions[-1]["observed_at"]>=cutoff)
                    or (len(events)==101 and events[-1]["observed_at"]>=cutoff)):
                raise ValueError("recent_run_budget")
        return set(ordered[:3])

    def record_negative_feedback(self, skill_id: str, run_id: str, *, actor: str,
                                 reason: str) -> dict[str, Any]:
        """Internal run-bound negative feedback, not a public authorization API.

        Idempotent per skill/run. Two failures among the latest three distinct
        observed source runs question an active skill. It never grants trust.
        """
        if (not isinstance(skill_id, str) or not 1 <= len(skill_id) <= 128
                or not isinstance(run_id, str) or not 1 <= len(run_id) <= 128
                or not isinstance(actor, str) or actor not in {"portal-admin", "internal-system"}
                or not isinstance(reason, str) or not 1 <= len(reason.strip()) <= 256):
            raise ValueError("invalid_feedback_request")
        validate_candidate_content("pass", {"reason": reason})
        self.migrate()
        with _LOCK, self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            skill = db.execute("SELECT status FROM simulation_skills WHERE skill_id=?", (skill_id,)).fetchone()
            if skill is None:
                raise ValueError("unknown_skill")
            # Association is taken from persisted versions/observations, not an
            # arbitrary caller-supplied claim that a run used this skill.
            association = self._source_association_connection(db,skill_id,run_id)
            duplicate = db.execute("SELECT 1 FROM skill_feedback WHERE skill_id=? AND run_id=?", (skill_id, run_id)).fetchone()
            if duplicate:
                return {"recorded": False, "duplicate": True, "status": skill["status"],
                        "trusted_run": False, "runtime_approved": False}
            now = _now()
            db.execute("INSERT INTO skill_feedback VALUES (?,?,?,?,?)", (skill_id, run_id, actor, reason, now))
            recent_ids = self._recent_source_runs_connection(db,skill_id)
            placeholders = ",".join("?" for _ in recent_ids)
            failed_recent = db.execute(
                f"SELECT COUNT(*) FROM skill_feedback WHERE skill_id=? AND run_id IN ({placeholders})",
                (skill_id, *sorted(recent_ids))).fetchone()[0] if recent_ids else 0
            status = "questioned" if skill["status"] == "active" and failed_recent >= 2 else skill["status"]
            db.execute("UPDATE simulation_skills SET failure_count=failure_count+1,status=?,updated_at=? WHERE skill_id=?",
                       (status, now, skill_id))
            db.execute("INSERT INTO skill_events(skill_id,event_type,detail,created_at) VALUES (?,?,?,?)",
                       (skill_id, "negative_feedback", _canonical_json({"run_id": run_id, "actor": actor,
                        "reason": reason, "recent_failed_runs": failed_recent,
                        "previous_status": skill["status"], "status": status,
                        "source_association": association}), now))
            return {"recorded": True, "duplicate": False, "status": status,
                    "recent_failed_runs": failed_recent, "trusted_run": False, "runtime_approved": False}

    def set_archived(self, skill_id: str, *, archived: bool, expected_updated_at: str,
                     actor: str, reason: str) -> dict[str, Any]:
        """Internal reversible lifecycle write; HTTP role authorization is separate.

        Optimistic concurrency and the audit insert share a transaction. Restore
        never automatically reactivates a previously active skill.
        """
        if (type(archived) is not bool or not isinstance(actor, str) or actor not in {"portal-admin", "internal-system"}
                or not isinstance(skill_id, str) or not 1 <= len(skill_id) <= 128
                or not isinstance(expected_updated_at, str) or not expected_updated_at
                or not isinstance(reason, str) or not 1 <= len(reason.strip()) <= 256):
            raise ValueError("invalid_archive_request")
        validate_candidate_content("pass", {"reason": reason})
        self.migrate()
        with _LOCK, self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT status,archived,updated_at FROM simulation_skills WHERE skill_id=?", (skill_id,)).fetchone()
            if row is None:
                raise ValueError("unknown_skill")
            if row["updated_at"] != expected_updated_at:
                raise ValueError("stale_skill_revision")
            if bool(row["archived"]) == archived:
                raise ValueError("archive_state_unchanged")
            status = "questioned" if not archived and row["status"] == "active" else row["status"]
            now = _now()
            db.execute("UPDATE simulation_skills SET archived=?,status=?,updated_at=? WHERE skill_id=?",
                       (int(archived), status, now, skill_id))
            detail = _canonical_json({"actor": actor, "reason": reason, "previous_status": row["status"],
                                      "previous_archived": bool(row["archived"]), "status": status})
            db.execute("INSERT INTO skill_events(skill_id,event_type,detail,created_at) VALUES (?,?,?,?)",
                       (skill_id, "skill_archived" if archived else "skill_restored", detail, now))
            return {"skill_id": skill_id, "status": status, "archived": archived,
                    "updated_at": now, "trusted_run": False, "runtime_approved": False}

    def request_revalidation(self,skill_id,*,expected_updated_at,expected_event_id,actor,reason):
        """Return a questioned record to Q2, never restore old authorization.

        Timestamp plus exact audit revision prevents stale same-second actions.
        A new independent dispatch and receipt must follow this immutable epoch.
        Missing DBs are not created and archived/retired records are untouched.
        """
        if (type(skill_id) is not str or not 1<=len(skill_id)<=128
                or type(expected_updated_at) is not str or not expected_updated_at
                or type(expected_event_id) is not int or expected_event_id<1
                or actor not in {"portal-admin","internal-system"}
                or type(reason) is not str or not 1<=len(reason.strip())<=256):
            raise ValueError("invalid_revalidation_request")
        validate_candidate_content("pass",{"reason":reason})
        with _LOCK,self._db_existing_write() as db:
            db.execute("BEGIN IMMEDIATE")
            from app.skill_authoring_schema import schema_ready
            if db.execute("SELECT max(version) FROM schema_migrations").fetchone()[0]!=SCHEMA_VERSION and not schema_ready(db):
                raise ValueError("explicit_schema_migration_required")
            row=db.execute("SELECT status,archived,updated_at FROM simulation_skills WHERE skill_id=?",(skill_id,)).fetchone()
            latest=db.execute("SELECT max(id) FROM skill_events WHERE skill_id=?",(skill_id,)).fetchone()[0]
            if (row is None or row["status"]!="questioned" or row["archived"]
                    or row["updated_at"]!=expected_updated_at or latest!=expected_event_id):
                raise ValueError("stale_or_ineligible_revalidation")
            version=db.execute("SELECT max(version) FROM skill_versions WHERE skill_id=?",(skill_id,)).fetchone()[0]
            requested=time.time_ns();stamp=_now()
            detail=dict(actor=actor,reason=reason,previous_status="questioned",status="candidate",
                quality_level="Q2",version=version,requested_ns=requested,
                previous_event_id=expected_event_id,requires_new_independent_dispatch=True,
                actual_use_authenticated=False)
            event=db.execute("INSERT INTO skill_events(skill_id,event_type,detail,created_at) VALUES (?,?,?,?)",
                (skill_id,"revalidation_requested",_canonical_json(detail),stamp))
            db.execute("UPDATE simulation_skills SET status='candidate',quality_level='Q2',updated_at=? WHERE skill_id=?",
                (stamp,skill_id))
        return dict(skill_id=skill_id,status="candidate",quality_level="Q2",event_id=event.lastrowid,
            trusted_run=False,runtime_approved=False,run_bound=False,parameters_bound=False,
            actual_use_authenticated=False,requires_new_independent_dispatch=True)

    def inspect_derivation(self, skill_id: str, version: int | None = None) -> dict[str, Any]:
        try:
            return self._inspect_derivation_snapshot(skill_id, version)
        except (sqlite3.Error, OSError, ValueError, TypeError):
            return {"references_current": False, "trusted_run": False,
                    "runtime_approved": False, "requires_review": True,
                    "reason": "dependency_store_unavailable"}

    def _inspect_derivation_snapshot(self, skill_id: str, version: int | None = None) -> dict[str, Any]:
        """Read a bounded consistent dependency snapshot, never grant physics trust.

        Each referenced skill uses its current latest version. Retirement or
        questioning therefore invalidates derived reuse at consumption time;
        historical versions/events are preserved. This is only a prerequisite:
        a caller must still verify runtime, physics, and parameter binding.
        """
        with self._read_db() as db:
            return self._inspect_derivation_connection(db, skill_id, version)

    def _inspect_derivation_connection(self, db, skill_id, version=None, *, candidate_root=False):
        """Internal prerequisite on an existing bounded transaction; never grants trust.

        The coordinator owns the connection, row factory and query budget.
        This helper neither opens another snapshot nor commits/rolls back.
        """
        denied = {"references_current": False, "trusted_run": False,
                  "runtime_approved": False, "requires_review": True}
        if not db.in_transaction:
            return dict(denied, reason="decision_transaction_required")
        if not isinstance(skill_id, str) or not skill_id or (version is not None and (type(version) is not int or version < 1)):
            return dict(denied, reason="invalid_skill_reference")
        visited: set[tuple[str, int]] = set()
        visiting: set[str] = set()

        def walk(current: str, selected: int | None, depth: int) -> str | None:
            if depth > 32 or len(visited) + len(visiting) >= 100:
                return "dependency_budget_exceeded"
            if current in visiting:
                return "dependency_cycle"
            row = db.execute("SELECT status,archived FROM simulation_skills WHERE skill_id=?", (current,)).fetchone()
            if row is None:
                return "missing_dependency"
            # Diagnostic Q2 intake may inspect its own candidate root; all
            # upstream dependencies retain the active lifecycle requirement.
            if row["archived"] or (row["status"] != "active"
                    and not (candidate_root is True and depth==0 and row["status"]=="candidate")):
                return "dependency_not_active"
            if selected is None:
                selected = db.execute("SELECT MAX(version) FROM skill_versions WHERE skill_id=?", (current,)).fetchone()[0]
            record = db.execute("SELECT reference_skill_ids FROM skill_versions WHERE skill_id=? AND version=?", (current, selected)).fetchone()
            if record is None:
                return "missing_version"
            key = (current, selected)
            if key in visited:
                return None
            try:
                declared = json.loads(record[0])
            except (TypeError, ValueError):
                return "invalid_dependency_record"
            rows = db.execute("SELECT reference_skill_id FROM skill_references WHERE skill_id=? AND version=? LIMIT 4", key).fetchall()
            references = [r[0] for r in rows]
            if (not isinstance(declared, list) or len(declared) > 3
                    or any(not isinstance(v, str) for v in declared)
                    or sorted(declared) != sorted(references)):
                return "dependency_record_mismatch"
            visiting.add(current)
            for reference in references:
                reason = walk(reference, None, depth + 1)
                if reason:
                    return reason
            visiting.remove(current)
            visited.add(key)
            return None

        reason = walk(skill_id, version, 0)
        if reason:
            return dict(denied, reason=reason)
        return dict(denied, references_current=True, requires_review=False,
                    reason="dependency_snapshot_only", checked_versions=len(visited))

    def backup_to(self, destination: str | Path) -> None:
        """Read-only source snapshot; publish exclusively, never replace a file.

        No migration or missing-source creation. A completed temporary SQLite
        file is linked into place atomically on the same filesystem. Lock waits
        are bounded; a failure removes only this call's temporary file.
        """
        import os
        import tempfile

        target = Path(destination).absolute()
        if target.resolve() == self.path.resolve():
            raise ValueError("backup_must_not_replace_source")
        source = sqlite3.connect(self.path.resolve().as_uri()+"?mode=ro",uri=True,timeout=1)
        temporary = None
        try:
            source.execute("PRAGMA query_only=ON")
            page_size = source.execute("PRAGMA page_size").fetchone()[0]
            max_pages = 256*1024*1024 // page_size
            if source.execute("PRAGMA page_count").fetchone()[0] > max_pages:
                raise ValueError("backup_size_budget")
            if target.exists() or target.is_symlink():
                raise FileExistsError("backup_destination_exists")
            target.parent.mkdir(parents=True,exist_ok=True)
            descriptor,name = tempfile.mkstemp(prefix=".skill-backup-",dir=target.parent)
            temporary = Path(name)
            os.close(descriptor)
            deadline = time.monotonic()+10
            def progress(status, remaining, total):
                if total > max_pages:
                    raise ValueError("backup_size_budget")
                if time.monotonic() > deadline:
                    raise TimeoutError("backup_deadline")
            destination_db = sqlite3.connect(temporary)
            try:
                source.backup(destination_db,pages=128,progress=progress,sleep=0.05)
                destination_db.set_progress_handler(lambda:int(time.monotonic()>deadline),1000)
                if destination_db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                    raise ValueError("backup_integrity_failed")
            finally:
                destination_db.close()
            # link is exclusive even if another writer creates target after
            # the preliminary check. No replace/rename-overwrite operation.
            os.link(temporary,target)
        finally:
            source.close()
            if temporary is not None:
                temporary.unlink()
