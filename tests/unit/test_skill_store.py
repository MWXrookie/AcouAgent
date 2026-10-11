import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.skill_store import SkillStore, SCHEMA_VERSION, normalize_parameters, skill_fingerprint


def candidate(store: SkillStore, suffix: str = "") -> str:
    return store.create_candidate(
        scenario_type="point_source_2d",
        state_text="2D homogeneous point source",
        normalized_params={"frequency_hz": 500000, "dx_m": 0.00025},
        code_template=f"print('skill{suffix}')",
        postconditions={"has_signal": True},
        physics_evidence={"check_id": "numeric_valid", "passed": True},
        source_run_id=f"run-{suffix or 'one'}",
        validator_version="physics_gate.v1",
        quality_level="Q2",
    )


def test_migration_is_idempotent_and_creates_required_tables(tmp_path) -> None:
    store = SkillStore(tmp_path / "skills.db")
    store.migrate()
    store.migrate()
    db = sqlite3.connect(store.path)
    tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"simulation_skills", "skill_versions", "skill_events", "skill_retrievals", "schema_migrations"} <= tables


def test_candidate_is_evidence_bound_and_never_auto_activates(tmp_path) -> None:
    store = SkillStore(tmp_path / "skills.db")
    skill_id = candidate(store)
    stored = store.get_skill(skill_id)
    assert stored["status"] == "candidate"
    assert stored["quality_level"] == "Q2"
    with pytest.raises(ValueError, match="Q2"):
        store.create_candidate(scenario_type="x", state_text="x", normalized_params={}, code_template="x", postconditions={}, physics_evidence={}, source_run_id="run", validator_version="v", quality_level="Q1")


@pytest.mark.parametrize("claimed_level", ["Q3", "Q4"])
def test_candidate_rejects_unverified_high_trust_level(tmp_path, claimed_level) -> None:
    store = SkillStore(tmp_path / "skills.db")
    with pytest.raises(ValueError, match="Q3/Q4 need verified physics evidence"):
        store.create_candidate(
            scenario_type="point_source_2d",
            state_text="2D homogeneous point source",
            normalized_params={"frequency_hz": 500000, "dx_m": 0.00025},
            code_template="print('claimed success')",
            postconditions={"has_signal": True},
            physics_evidence={},
            source_run_id="unverified-run",
            validator_version="claimed-validator",
            quality_level=claimed_level,
        )
    with sqlite3.connect(store.path) as db:
        assert db.execute("SELECT COUNT(*) FROM simulation_skills").fetchone()[0] == 0


def test_concurrent_creation_and_backup_recovery(tmp_path) -> None:
    store = SkillStore(tmp_path / "skills.db")
    with ThreadPoolExecutor(max_workers=4) as pool:
        ids = list(pool.map(lambda i: candidate(store, str(i)), range(8)))
    assert len(set(ids)) == 1
    backup = tmp_path / "backup.db"
    store.backup_to(backup)
    db = sqlite3.connect(backup)
    assert db.execute("SELECT COUNT(*) FROM simulation_skills").fetchone()[0] == 1
    assert db.execute("SELECT COUNT(*) FROM skill_versions").fetchone()[0] == 8


def test_normalization_and_fingerprint_make_equivalent_units_identical() -> None:
    mhz = normalize_parameters({"frequency_mhz": 0.5, "dx_mm": 0.25, "domain_N": [128, 128]})
    hz = normalize_parameters({"frequency_hz": 500000, "dx_m": 0.00025, "grid_size": [128, 128]})
    assert mhz == hz
    assert skill_fingerprint("point_source_2d", mhz) == skill_fingerprint("point_source_2d", hz)
    with pytest.raises(ValueError, match="unit-ambiguous"):
        normalize_parameters({"frequency": 0.5})


def test_same_fingerprint_updates_statistics_and_new_code_versions(tmp_path) -> None:
    store = SkillStore(tmp_path / "skills.db")
    first = candidate(store, "a")
    assert candidate(store, "a") == first
    assert candidate(store, "b") == first
    db = sqlite3.connect(store.path)
    assert db.execute("SELECT success_count FROM simulation_skills WHERE skill_id = ?", (first,)).fetchone()[0] == 3
    assert db.execute("SELECT COUNT(*) FROM skill_versions WHERE skill_id = ?", (first,)).fetchone()[0] == 2


def test_audit_events_reject_mutation_and_survive_backup(tmp_path):
    store = SkillStore(tmp_path / "skills.db")
    skill_id = candidate(store)
    store.migrate()
    with sqlite3.connect(store.path) as db:
        before = db.execute("SELECT * FROM skill_events").fetchall()
        for statement in (
            "UPDATE skill_events SET event_type='forged'",
            "DELETE FROM skill_events",
            "DELETE FROM skill_events WHERE skill_id=?",
        ):
            with pytest.raises(sqlite3.IntegrityError, match="append-only"):
                db.execute(statement, (skill_id,) if "?" in statement else ())
            assert db.execute("SELECT * FROM skill_events").fetchall() == before
        db.execute("INSERT INTO skill_events(skill_id,event_type,detail,created_at) VALUES (?,?,?,?)",
                   (skill_id, "review_requested", "{}", "2026-10-08"))
    backup = tmp_path / "backup.db"
    store.backup_to(backup)
    with sqlite3.connect(backup) as db:
        assert db.execute("SELECT COUNT(*) FROM skill_events").fetchone()[0] == 2
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            db.execute("DELETE FROM skill_events")


def bounded_candidate(store, **overrides):
    kwargs = dict(scenario_type="plane_wave_2d", state_text="bounded plane",
        normalized_params={"frequency_hz": 500000, "dx_m": 0.00025, "domain_N": [128, 128]},
        code_template="print('bounded')", postconditions={}, physics_evidence={},
        source_run_id="bounded-run", validator_version="v1", quality_level="Q2",
        preconditions={"dimensions": 2, "frequency_hz": [400000, 600000]})
    kwargs.update(overrides)
    return store.create_candidate(**kwargs)


def test_preconditions_match_units_boundaries_and_missing_values():
    from app.skill_store import preconditions_match
    bounds = {"dimensions": 2, "frequency_hz": [400000, 600000]}
    assert preconditions_match(bounds, {"domain_N": [128, 128], "frequency_mhz": 0.4})
    assert preconditions_match(bounds, {"grid_size": [128, 128], "frequency_mhz": 0.6})
    assert not preconditions_match(bounds, {"domain_N": [128, 128], "frequency_mhz": 0.600001})
    assert not preconditions_match(bounds, {"frequency_mhz": 0.5})
    assert not preconditions_match({}, {"frequency_mhz": 0.5})


@pytest.mark.parametrize("bounds", [
    {"frequency_hz": [1, float("nan")]}, {"dx_m": [2, 1]},
    {"dimensions": True}, {"dimensions": 4}, {"unknown": 1},
    {"frequency_hz": [False, 2]}, {"medium_type": ""},
])
def test_invalid_preconditions_never_write_candidate(tmp_path, bounds):
    store = SkillStore(tmp_path / "skills.db")
    with pytest.raises(ValueError):
        bounded_candidate(store, preconditions=bounds)
    with sqlite3.connect(store.path) as db:
        assert db.execute("SELECT COUNT(*) FROM simulation_skills").fetchone()[0] == 0


def test_reference_traceability_and_rejected_writes_are_atomic(tmp_path):
    store = SkillStore(tmp_path / "skills.db")
    parent = candidate(store)
    child = bounded_candidate(store, reference_skill_ids=[parent])
    with sqlite3.connect(store.path) as db:
        assert db.execute("SELECT reference_skill_id FROM skill_references WHERE skill_id=?", (child,)).fetchone()[0] == parent
        assert db.execute("SELECT preconditions FROM simulation_skills WHERE skill_id=?", (child,)).fetchone()[0] != "{}"
    for overrides in (
        {"reference_skill_ids": ["missing"]},
        {"reference_skill_ids": [child]},
        {"reference_skill_ids": [parent, parent]},
        {"preconditions": {"frequency_hz": [1, 2]}},
        {"preconditions": {"frequency_hz": [400000, 600000]}},
    ):
        with pytest.raises(ValueError):
            bounded_candidate(store, **overrides)
    with sqlite3.connect(store.path) as db:
        assert db.execute("SELECT COUNT(*) FROM skill_versions WHERE skill_id=?", (child,)).fetchone()[0] == 1
        assert db.execute("SELECT success_count FROM simulation_skills WHERE skill_id=?", (child,)).fetchone()[0] == 1
    assert bounded_candidate(store, reference_skill_ids=[parent]) == child
    # Changing derivation is versioned even when code stays identical.
    assert bounded_candidate(store) == child
    with sqlite3.connect(store.path) as db:
        assert db.execute("SELECT COUNT(*) FROM skill_versions WHERE skill_id=?", (child,)).fetchone()[0] == 2
        assert db.execute("SELECT reference_skill_ids FROM skill_versions WHERE skill_id=? AND version=2", (child,)).fetchone()[0] == "[]"


def test_upgrade_existing_v2_database_preserves_records(tmp_path):
    store = SkillStore(tmp_path / "skills.db")
    skill_id = candidate(store)
    with sqlite3.connect(store.path) as db:
        db.execute("DROP INDEX idx_skills_retrieval")
        db.execute("DROP TABLE skill_references")
        db.execute("DROP TABLE skill_feedback")
        db.execute("ALTER TABLE simulation_skills DROP COLUMN preconditions")
        db.execute("ALTER TABLE simulation_skills DROP COLUMN archived")
        db.execute("ALTER TABLE skill_versions DROP COLUMN reference_skill_ids")
        db.execute("DROP TRIGGER skill_events_no_update")
        db.execute("DROP TRIGGER skill_events_no_delete")
        db.execute("DROP TRIGGER skill_events_no_replace")
        for operation in ("update","delete","replace"):
            db.execute("DROP TRIGGER skill_retrievals_no_"+operation)
        db.execute("DROP TABLE skill_local_reference_completions")
        db.execute("DROP TABLE skill_local_reference_dispatches")
        db.execute("DELETE FROM schema_migrations WHERE version=17")
        db.execute("DROP TABLE skill_local_sources")
        db.execute("DELETE FROM schema_migrations WHERE version=16")
        db.execute("DROP TABLE skill_reference_executions")
        db.execute("DROP TABLE skill_reference_dispatches")
        db.execute("DROP TABLE skill_reference_capsules")
        db.execute("DROP TABLE skill_host_verifications")
        db.execute("DROP TABLE skill_host_dispatches")
        db.execute("DROP TABLE skill_authority_roots")
        db.execute("DROP TABLE skill_generation_consumptions")
        db.execute("DROP TABLE skill_generation_decisions")
        for name in ("idx_skill_version_source","idx_skill_event_source","idx_skill_feedback_request","idx_skill_feedback_run","idx_skill_version_recent_source","idx_skill_event_recent_source","idx_candidate_source_version","idx_candidate_source_observation"):
            db.execute("DROP INDEX IF EXISTS "+name)
        db.execute("DROP INDEX idx_skill_event_history")
        db.execute("DROP INDEX idx_skill_revalidation_epoch")
        db.execute("DELETE FROM schema_migrations WHERE version>=3")
    store.migrate()
    store.migrate()
    assert store.get_skill(skill_id)["preconditions"] == "{}"
    assert store.get_skill(skill_id)["status"] == "candidate"
    with sqlite3.connect(store.path) as db:
        assert db.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] == SCHEMA_VERSION
        assert db.execute("SELECT reference_skill_ids FROM skill_versions").fetchone()[0] == "[]"
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            db.execute("UPDATE skill_events SET detail='forged'")


def set_test_status(store, skill_id, status):
    # Synthetic status rows test dependency bookkeeping only, not activation.
    with sqlite3.connect(store.path) as db:
        db.execute("UPDATE simulation_skills SET status=? WHERE skill_id=?", (status, skill_id))


def test_derivation_rechecks_current_status_without_granting_trust(tmp_path):
    store = SkillStore(tmp_path / "skills.db")
    parent = candidate(store)
    child = bounded_candidate(store, reference_skill_ids=[parent])
    assert not store.inspect_derivation(child)["references_current"]
    for skill in (parent, child):
        set_test_status(store, skill, "active")
    result = store.inspect_derivation(child)
    assert result["references_current"] and result["checked_versions"] == 2
    assert result["trusted_run"] is False and result["runtime_approved"] is False
    with sqlite3.connect(store.path) as db:
        history = db.execute("SELECT * FROM skill_events").fetchall()
    for status in ("questioned", "retired"):
        set_test_status(store, parent, status)
        assert store.inspect_derivation(child)["requires_review"]
        assert not store.inspect_derivation(child)["references_current"]
    with sqlite3.connect(store.path) as db:
        assert db.execute("SELECT * FROM skill_events").fetchall() == history
    assert not store.inspect_derivation("missing")["references_current"]
    assert not store.inspect_derivation(child, True)["references_current"]


def test_derivation_rejects_cycles_and_corrupt_reference_mirror(tmp_path):
    import json
    store = SkillStore(tmp_path / "skills.db")
    parent = candidate(store)
    child = bounded_candidate(store, reference_skill_ids=[parent])
    for skill in (parent, child):
        set_test_status(store, skill, "active")
    with sqlite3.connect(store.path) as db:
        db.execute("UPDATE skill_versions SET reference_skill_ids=? WHERE skill_id=?", (json.dumps([child]), parent))
        db.execute("INSERT INTO skill_references VALUES (?,1,?)", (parent, child))
    assert store.inspect_derivation(child)["reason"] == "dependency_cycle"
    with sqlite3.connect(store.path) as db:
        db.execute("DELETE FROM skill_references WHERE skill_id=?", (parent,))
    assert store.inspect_derivation(child)["reason"] == "dependency_record_mismatch"

def test_derivation_depth_budget_is_fail_closed(tmp_path):
    store = SkillStore(tmp_path / "skills.db")
    previous = None
    for i in range(34):
        current = bounded_candidate(store,
            normalized_params={"frequency_hz": 500000 + i, "dx_m": 0.00025, "domain_N": [128, 128]},
            reference_skill_ids=[] if previous is None else [previous])
        set_test_status(store, current, "active")
        previous = current
    assert store.inspect_derivation(previous)["reason"] == "dependency_budget_exceeded"


@pytest.mark.parametrize("overrides", [
    {"code_template": "x" * 20001},
    {"code_template": chr(96)*3 + "python\nprint(1)\n" + chr(96)*3},
    {"code_template": "<think>reasoning</think>print(1)"},
    {"code_template": "def broken("},
    {"code_template": "API_KEY = 'synthetic-placeholder'"},
    {"code_template": "print('/home/test/private/file')"},
    {"physics_evidence": {"api_key": "synthetic-placeholder"}},
    {"physics_evidence": {"stdout": "Bearer abcdefghijklmnopqrstuvwxyz"}},
    {"postconditions": {"path": "C:/Users/test/file"}},
    {"state_text": "/tmp/private/run"},
])
def test_candidate_content_preflight_rejects_without_partial_write(tmp_path, overrides):
    store = SkillStore(tmp_path / "skills.db")
    with pytest.raises(ValueError):
        bounded_candidate(store, **overrides)
    with sqlite3.connect(store.path) as db:
        assert db.execute("SELECT COUNT(*) FROM simulation_skills").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM skill_events").fetchone()[0] == 0


def test_candidate_content_byte_limit_and_metadata_budget():
    from app.skill_store import validate_candidate_content
    validate_candidate_content("#" + "a" * 19999, {})
    with pytest.raises(ValueError):
        validate_candidate_content("#" + "声" * 6667, {})
    with pytest.raises(ValueError, match="storage budget"):
        validate_candidate_content("print(1)", {"note": "a" * 64000})


def test_audit_replace_cannot_bypass_append_only_when_recursive_triggers_off(tmp_path):
    store = SkillStore(tmp_path / "skills.db")
    candidate(store)
    with sqlite3.connect(store.path) as db:
        db.execute("PRAGMA recursive_triggers=OFF")
        before = db.execute("SELECT * FROM skill_events").fetchall()
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            db.execute("INSERT OR REPLACE INTO skill_events SELECT * FROM skill_events")
        assert db.execute("SELECT * FROM skill_events").fetchall() == before


@pytest.mark.parametrize("field,value", [
    ("source_run_id", True), ("source_run_id", "x"*129),
    ("validator_version", None), ("validator_version", "x"*129),
    ("physics_evidence", {"stdout": "benign but uncleaned output"}),
    ("physics_evidence", {"nested": {"env": {}}}),
    ("physics_evidence", {"nested": {"path": chr(92)*2 + "server/share"}}),
])
def test_storage_rejects_unbounded_identifiers_and_raw_metadata(tmp_path, field, value):
    store = SkillStore(tmp_path / "skills.db")
    with pytest.raises(ValueError):
        bounded_candidate(store, **{field: value})
    with sqlite3.connect(store.path) as db:
        assert db.execute("SELECT COUNT(*) FROM simulation_skills").fetchone()[0] == 0


def test_failed_fresh_migration_rolls_back_all_schema_and_can_retry(tmp_path, monkeypatch):
    from contextlib import contextmanager
    store = SkillStore(tmp_path / "skills.db")
    original = store._db
    class FailingConnection:
        def __init__(self, db):
            self.db = db
        def execute(self, sql, *args):
            if sql.startswith("CREATE UNIQUE INDEX idx_skills_fingerprint"):
                raise sqlite3.OperationalError("synthetic migration interruption")
            return self.db.execute(sql, *args)
    @contextmanager
    def broken():
        with original() as db:
            yield FailingConnection(db)
    monkeypatch.setattr(store, "_db", broken)
    with pytest.raises(sqlite3.OperationalError, match="synthetic"):
        store.migrate()
    with sqlite3.connect(store.path) as db:
        assert not db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    monkeypatch.setattr(store, "_db", original)
    store.migrate()
    assert candidate(store)
    with sqlite3.connect(store.path) as db:
        assert db.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] == SCHEMA_VERSION


def test_normalization_rejects_conflicting_geometry_and_dimension_types():
    from app.skill_store import preconditions_match
    with pytest.raises(ValueError, match="conflicting"):
        normalize_parameters({"domain_N": [128, 128], "grid_shape": [256, 256]})
    assert not preconditions_match({"dimensions": 2}, {"dimensions": 2.0})


def test_consumption_is_read_only_missing_or_legacy_database_stays_untouched(tmp_path):
    path = tmp_path / "missing" / "skills.db"
    result = SkillStore(path).inspect_derivation("unknown")
    assert not result["references_current"] and not path.exists()
    assert not path.parent.exists()
    legacy = tmp_path / "legacy.db"
    with sqlite3.connect(legacy) as db:
        db.execute("CREATE TABLE legacy_marker(value TEXT)")
    before = legacy.read_bytes()
    assert not SkillStore(legacy).inspect_derivation("unknown")["references_current"]
    assert legacy.read_bytes() == before


def test_content_does_not_confuse_http_url_with_windows_drive():
    from app.skill_store import validate_candidate_content
    validate_candidate_content("print('https://example.invalid/report')", {})
    with pytest.raises(ValueError, match="absolute path"):
        validate_candidate_content("print('C:/Users/test')", {})


def archive(store, skill_id, value, **overrides):
    kwargs = dict(archived=value, expected_updated_at=store.get_skill(skill_id)["updated_at"],
                  actor="portal-admin", reason="manual review")
    kwargs.update(overrides)
    return store.set_archived(skill_id, **kwargs)


def test_archive_restore_is_audited_without_regranting_active(tmp_path):
    store = SkillStore(tmp_path / "skills.db")
    parent = candidate(store)
    child = bounded_candidate(store, reference_skill_ids=[parent])
    for skill in (parent, child):
        set_test_status(store, skill, "active")
    assert store.inspect_derivation(child)["references_current"]
    version_count = None
    with sqlite3.connect(store.path) as db:
        version_count = db.execute("SELECT COUNT(*) FROM skill_versions").fetchone()[0]
    archived = archive(store, parent, True)
    assert archived["archived"]
    assert not store.inspect_derivation(child)["references_current"]
    restored = archive(store, parent, False)
    assert restored["status"] == "questioned"
    assert not store.inspect_derivation(child)["references_current"]
    assert restored["trusted_run"] is False
    with sqlite3.connect(store.path) as db:
        assert db.execute("SELECT COUNT(*) FROM skill_versions").fetchone()[0] == version_count
        events = [r[0] for r in db.execute("SELECT event_type FROM skill_events WHERE skill_id=? ORDER BY id", (parent,))]
        assert events[-2:] == ["skill_archived", "skill_restored"]


def test_archive_stale_or_invalid_writes_leave_state_unchanged(tmp_path):
    store = SkillStore(tmp_path / "skills.db")
    skill = candidate(store)
    before = store.get_skill(skill)
    for overrides in (
        {"expected_updated_at": "stale"}, {"actor": "unknown"}, {"reason": ""},
        {"archived": 1}, {"reason": "API_KEY=synthetic"},
    ):
        with pytest.raises(ValueError):
            archive(store, skill, True, **overrides)
        assert store.get_skill(skill) == before
    archive(store, skill, True)
    with pytest.raises(ValueError, match="unchanged"):
        archive(store, skill, True)


def test_archive_audit_failure_rolls_back_state(tmp_path):
    store = SkillStore(tmp_path / "skills.db")
    skill = candidate(store)
    before = store.get_skill(skill)
    with sqlite3.connect(store.path) as db:
        db.execute("""CREATE TRIGGER reject_archive_audit BEFORE INSERT ON skill_events
            WHEN NEW.event_type='skill_archived'
            BEGIN SELECT RAISE(ABORT, 'synthetic audit failure'); END""")
    with pytest.raises(sqlite3.IntegrityError, match="audit"):
        archive(store, skill, True)
    assert store.get_skill(skill) == before


def test_negative_feedback_threshold_idempotency_and_derivation_recheck(tmp_path):
    store = SkillStore(tmp_path / "skills.db")
    parent = candidate(store, "first")
    candidate(store, "second")
    candidate(store, "third")
    child = bounded_candidate(store, reference_skill_ids=[parent])
    for skill in (parent, child):
        set_test_status(store, skill, "active")
    first = store.record_negative_feedback(parent, "run-first", actor="portal-admin", reason="wrong result")
    assert first["status"] == "active" and first["recent_failed_runs"] == 1
    duplicate = store.record_negative_feedback(parent, "run-first", actor="portal-admin", reason="repeat")
    assert duplicate["duplicate"] and not duplicate["recorded"]
    second = store.record_negative_feedback(parent, "run-second", actor="portal-admin", reason="wrong result")
    assert second["status"] == "questioned" and second["recent_failed_runs"] == 2
    assert store.get_skill(parent)["failure_count"] == 2
    assert not store.inspect_derivation(child)["references_current"]
    with sqlite3.connect(store.path) as db:
        assert db.execute("SELECT COUNT(*) FROM skill_feedback").fetchone()[0] == 2
        for statement in ("DELETE FROM skill_feedback", "UPDATE skill_feedback SET reason='changed'",
                          "INSERT OR REPLACE INTO skill_feedback SELECT * FROM skill_feedback"):
            with pytest.raises(sqlite3.IntegrityError, match="append-only"):
                db.execute(statement)


def test_negative_feedback_for_same_code_observation_and_unknown_run(tmp_path):
    store = SkillStore(tmp_path / "skills.db")
    skill = candidate(store, "one")
    # Same code but a new run association lives in candidate_observed.
    store.create_candidate(scenario_type="point_source_2d", state_text="point",
        normalized_params={"frequency_hz":500000,"dx_m":0.00025},
        code_template="print('skillone')",postconditions={},physics_evidence={},
        source_run_id="observed-only",validator_version="v1",quality_level="Q2")
    result = store.record_negative_feedback(skill, "observed-only", actor="portal-admin", reason="wrong result")
    assert result["recorded"] and result["status"] == "candidate"
    before = store.get_skill(skill)
    with pytest.raises(ValueError, match="not_associated"):
        store.record_negative_feedback(skill, "unrelated", actor="portal-admin", reason="wrong")
    assert store.get_skill(skill) == before


def test_feedback_audit_failure_rolls_back_feedback_and_counter(tmp_path):
    store = SkillStore(tmp_path / "skills.db")
    skill = candidate(store)
    before = store.get_skill(skill)
    with sqlite3.connect(store.path) as db:
        db.execute("""CREATE TRIGGER reject_feedback_audit BEFORE INSERT ON skill_events
            WHEN NEW.event_type='negative_feedback'
            BEGIN SELECT RAISE(ABORT,'synthetic audit failure'); END""")
    with pytest.raises(sqlite3.IntegrityError):
        store.record_negative_feedback(skill, "run-one", actor="portal-admin", reason="wrong")
    assert store.get_skill(skill) == before
    with sqlite3.connect(store.path) as db:
        assert db.execute("SELECT COUNT(*) FROM skill_feedback").fetchone()[0] == 0


def test_feedback_threshold_only_counts_last_three_distinct_runs(tmp_path):
    store = SkillStore(tmp_path / "skills.db")
    parent = candidate(store, "0")
    for i in range(1, 5):
        candidate(store, str(i))
    set_test_status(store, parent, "active")
    for run_id in ("run-0", "run-1"):
        result = store.record_negative_feedback(parent, run_id, actor="portal-admin", reason="wrong")
        assert result["recent_failed_runs"] == 0 and result["status"] == "active"
    result = store.record_negative_feedback(parent, "run-2", actor="portal-admin", reason="wrong")
    assert result["recent_failed_runs"] == 1 and result["status"] == "active"
    result = store.record_negative_feedback(parent, "run-4", actor="portal-admin", reason="wrong")
    assert result["recent_failed_runs"] == 2 and result["status"] == "questioned"


def test_concurrent_archive_only_one_writer_and_one_audit(tmp_path):
    store = SkillStore(tmp_path / "skills.db")
    skill = candidate(store)
    revision = store.get_skill(skill)["updated_at"]
    def attempt(_):
        try:
            archive(store, skill, True, expected_updated_at=revision)
            return "changed"
        except ValueError:
            return "rejected"
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, range(2)))
    assert sorted(results) == ["changed", "rejected"]
    with sqlite3.connect(store.path) as db:
        assert db.execute("SELECT COUNT(*) FROM skill_events WHERE event_type='skill_archived'").fetchone()[0] == 1


@pytest.mark.parametrize("version", [SCHEMA_VERSION+1, 0])
def test_unknown_or_incomplete_schema_is_rejected_without_rewriting(tmp_path, version):
    store = SkillStore(tmp_path / "skills.db")
    store.migrate()
    with sqlite3.connect(store.path) as db:
        if version == 0:
            db.execute("DELETE FROM schema_migrations WHERE version=3")
        else:
            db.execute("INSERT INTO schema_migrations VALUES (?, 'synthetic')", (version,))
        before = db.execute("SELECT * FROM schema_migrations ORDER BY version").fetchall()
    with pytest.raises(ValueError, match="unsupported"):
        store.migrate()
    with sqlite3.connect(store.path) as db:
        assert db.execute("SELECT * FROM schema_migrations ORDER BY version").fetchall() == before


def test_new_candidate_version_cannot_inherit_previous_active_quality(tmp_path):
    store = SkillStore(tmp_path / "skills.db")
    parent = candidate(store, "old")
    child = bounded_candidate(store, reference_skill_ids=[parent])
    for skill in (parent, child):
        set_test_status(store, skill, "active")
    with sqlite3.connect(store.path) as db:
        db.execute("UPDATE simulation_skills SET quality_level='Q4' WHERE skill_id=?", (parent,))
    assert store.inspect_derivation(child)["references_current"]
    assert candidate(store, "new") == parent
    updated = store.get_skill(parent)
    assert updated["status"] == "questioned" and updated["quality_level"] == "Q2"
    assert updated["source_run_id"] == "run-new"
    assert not store.inspect_derivation(child)["references_current"]


def test_new_candidate_version_does_not_restore_retired_or_archived_skill(tmp_path):
    store = SkillStore(tmp_path / "skills.db")
    skill = candidate(store, "old")
    archive(store, skill, True)
    set_test_status(store, skill, "retired")
    candidate(store, "new")
    updated = store.get_skill(skill)
    assert updated["archived"] == 1 and updated["status"] == "retired"


def _candidate_process(path, output):
    try:
        output.put(("ok", candidate(SkillStore(path))))
    except Exception as failure:
        import traceback
        from pathlib import Path
        frames=traceback.extract_tb(failure.__traceback__)
        output.put(("error", type(failure).__name__,[(Path(f.filename).name,f.lineno) for f in frames]))


def test_independent_process_writers_serialize_migration_and_candidate_observation(tmp_path):
    import multiprocessing
    context = multiprocessing.get_context("spawn")
    output = context.Queue()
    path = tmp_path / "skills.db"
    processes = [context.Process(target=_candidate_process, args=(str(path), output)) for _ in range(2)]
    try:
        for process in processes:
            process.start()
        for process in processes:
            process.join(timeout=10)
        assert all(not p.is_alive() and p.exitcode == 0 for p in processes)
        results = [output.get(timeout=2) for _ in processes]
        assert all(row[0] == "ok" for row in results), results
        assert len({row[1] for row in results}) == 1
        with sqlite3.connect(path) as db:
            assert db.execute("SELECT success_count FROM simulation_skills").fetchone()[0] == 2
            assert db.execute("SELECT COUNT(*) FROM skill_versions").fetchone()[0] == 1
            assert db.execute("SELECT COUNT(*) FROM skill_events").fetchone()[0] == 2
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
                process.join(timeout=2)
        output.close()
        output.join_thread()


@pytest.mark.parametrize("code,metadata", [
    ("print('/custom-root/private')", {}),
    ("print('text')", {"path": "/custom-root/private"}),
    ("print('text') # /etc/private/file", {}),
    ("print(" + repr(chr(92)*2 + "server/share") + ")", {}),
])
def test_candidate_absolute_path_checks_cover_custom_unix_roots_and_unc(code, metadata):
    from app.skill_store import validate_candidate_content
    with pytest.raises(ValueError, match="absolute path"):
        validate_candidate_content(code, metadata)


def test_idempotent_source_rejects_changed_code_without_count_or_audit(tmp_path):
    store=SkillStore(tmp_path/"idempotent.db")
    args=dict(scenario_type="initial_pressure_2d",state_text="test",
        normalized_params={"dx_m":0.001},code_template="pass",postconditions={},
        physics_evidence={},source_run_id="same-run",validator_version="v",
        quality_level="Q2",idempotent_source_run=True)
    sid=store.create_candidate(**args)
    assert store.create_candidate(**args) == sid
    args["code_template"]="x=1"
    with pytest.raises(ValueError,match="source_run_content_conflict"):
        store.create_candidate(**args)
    with sqlite3.connect(store.path) as db:
        assert db.execute("SELECT success_count FROM simulation_skills").fetchone()[0] == 1
        assert db.execute("SELECT count(*) FROM skill_events").fetchone()[0] == 1
        assert db.execute("SELECT count(*) FROM skill_versions").fetchone()[0] == 1


def test_wal_bootstrap_waits_for_other_process_lock_without_replaying_write(tmp_path):
    import threading,time
    path=tmp_path/"bootstrap.db"
    holder=sqlite3.connect(path,check_same_thread=False)
    holder.execute("CREATE TABLE unrelated(value TEXT)")
    holder.execute("BEGIN EXCLUSIVE")
    def release():
        time.sleep(0.15)
        holder.rollback()
    thread=threading.Thread(target=release);thread.start()
    try:
        store=SkillStore(path)
        skill=candidate(store)
        assert store.get_skill(skill)["success_count"]==1
    finally:
        thread.join(timeout=2);holder.close()

def test_anisotropic_spacing_units_are_preserved():
    assert normalize_parameters({"domain_dx_mm":[.25,.5]}) == {"dx_m":[.00025,.0005]}
    assert normalize_parameters({"domain_dx_mm":[.25,.5],"dx_m":[.00025,.0005]}) == {"dx_m":[.00025,.0005]}
    with pytest.raises(ValueError):
        normalize_parameters({"domain_dx_mm":[.25,.5],"dx_m":[.00025,.0006]})

@pytest.mark.parametrize("value", [True,0,-1,float("inf"),float("nan"),10**1000,[],[.1]*4,[True,.1]])
def test_unit_conversion_rejects_invalid_spacing_without_overflow(value):
    with pytest.raises(ValueError):
        normalize_parameters({"domain_dx":value})


def test_spacing_boundary_applies_to_every_grid_axis():
    from app.skill_store import preconditions_match
    assert preconditions_match({"dx_m":[.0001,.0005]},{"domain_dx":[.00025,.0005]})
    assert not preconditions_match({"dx_m":[.0001,.0005]},{"domain_dx":[.00025,.0006]})


def test_retrieval_index_migration_and_query_plan(tmp_path):
    store=SkillStore(tmp_path/"catalog.db")
    candidate(store)
    with sqlite3.connect(store.path) as db:
        indexes={row[1] for row in db.execute("PRAGMA index_list(simulation_skills)")}
        assert "idx_skills_retrieval" in indexes
        plan=db.execute("""EXPLAIN QUERY PLAN SELECT skill_id FROM simulation_skills
            WHERE scenario_type=? AND status='active' AND archived=0
            AND quality_level IN ('Q3','Q4')
            ORDER BY quality_level DESC,updated_at DESC,skill_id LIMIT 50""",
            ("plane_wave_2d",)).fetchall()
        assert any("idx_skills_retrieval" in row[3] for row in plan),plan
        assert not any("TEMP B-TREE" in row[3] for row in plan),plan


def test_v8_to_v9_index_upgrade_preserves_candidates_and_audits(tmp_path):
    store=SkillStore(tmp_path/"v8.db")
    skill=candidate(store)
    with sqlite3.connect(store.path) as db:
        db.execute("DROP INDEX idx_skills_retrieval")
        db.execute("DROP TABLE skill_local_reference_completions")
        db.execute("DROP TABLE skill_local_reference_dispatches")
        db.execute("DELETE FROM schema_migrations WHERE version=17")
        db.execute("DROP TABLE skill_local_sources")
        db.execute("DELETE FROM schema_migrations WHERE version=16")
        db.execute("DROP TABLE skill_reference_executions")
        db.execute("DROP TABLE skill_reference_dispatches")
        db.execute("DROP TABLE skill_reference_capsules")
        db.execute("DROP TABLE skill_host_verifications")
        db.execute("DROP TABLE skill_host_dispatches")
        db.execute("DROP TABLE skill_authority_roots")
        db.execute("DROP TABLE skill_generation_consumptions")
        db.execute("DROP TABLE skill_generation_decisions")
        for name in ("idx_skill_version_source","idx_skill_event_source","idx_skill_feedback_request","idx_skill_feedback_run","idx_skill_version_recent_source","idx_skill_event_recent_source","idx_candidate_source_version","idx_candidate_source_observation"):
            db.execute("DROP INDEX IF EXISTS "+name)
        db.execute("DROP INDEX idx_skill_event_history")
        db.execute("DROP INDEX idx_skill_revalidation_epoch")
        db.execute("DELETE FROM schema_migrations WHERE version>=9")
        before=db.execute("SELECT * FROM skill_events ORDER BY id").fetchall()
    store.migrate()
    assert store.get_skill(skill)["status"]=="candidate"
    with sqlite3.connect(store.path) as db:
        assert db.execute("SELECT * FROM skill_events ORDER BY id").fetchall()==before
        assert db.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]==SCHEMA_VERSION


def test_backup_missing_source_does_not_create_or_migrate(tmp_path):
    source=tmp_path/"missing.db"
    store=SkillStore(source)
    with pytest.raises(sqlite3.OperationalError):
        store.backup_to(tmp_path/"backup.db")
    assert not source.exists() and not (tmp_path/"backup.db").exists()
    assert not list(tmp_path.glob(".skill-backup-*"))

def test_backup_never_overwrites_existing_data_or_source(tmp_path):
    store=SkillStore(tmp_path/"skills.db");candidate(store,"original")
    target=tmp_path/"keep.db";target.write_bytes(b"existing user data")
    with pytest.raises(FileExistsError):store.backup_to(target)
    assert target.read_bytes()==b"existing user data"
    with pytest.raises(ValueError,match="replace_source"):store.backup_to(store.path)
    assert not list(tmp_path.glob(".skill-backup-*"))

def test_backup_restores_feedback_and_lifecycle_with_append_only_audit(tmp_path):
    store=SkillStore(tmp_path/"skills.db")
    sid=candidate(store,"original")
    store.record_negative_feedback(sid,"run-original",actor="internal-system",reason="review failed")
    with sqlite3.connect(store.path) as db:
        stamp=db.execute("SELECT updated_at FROM simulation_skills").fetchone()[0]
    store.set_archived(sid,archived=True,expected_updated_at=stamp,actor="internal-system",reason="test archive")
    target=tmp_path/"restored.db";store.backup_to(target)
    restored=SkillStore(target)
    with sqlite3.connect(target) as db:
        assert db.execute("PRAGMA integrity_check").fetchone()[0]=="ok"
        assert db.execute("SELECT archived,failure_count,status FROM simulation_skills").fetchone()==(1,1,"candidate")
        assert db.execute("SELECT count(*) FROM skill_feedback").fetchone()[0]==1
        assert db.execute("SELECT count(*) FROM skill_events").fetchone()[0]==3
        with pytest.raises(sqlite3.IntegrityError):db.execute("DELETE FROM skill_events")
    assert restored.get_skill(sid)["quality_level"]=="Q2"

def test_backup_fault_cleans_only_own_temporary_file(tmp_path,monkeypatch):
    import os
    store=SkillStore(tmp_path/"skills.db");candidate(store,"original")
    target=tmp_path/"backup.db"
    def collision(source,destination):
        target.write_bytes(b"concurrent user data")
        raise FileExistsError("test concurrent destination")
    monkeypatch.setattr(os,"link",collision)
    with pytest.raises(FileExistsError):store.backup_to(target)
    assert target.read_bytes()==b"concurrent user data"
    assert not list(tmp_path.glob(".skill-backup-*"))

@pytest.mark.parametrize("field,value", [
    ("physics_evidence", {"error": .01}), ("postconditions", {"finite": True}),
    ("validator_version", "changed")])
@pytest.mark.parametrize("observed", [False, True])
def test_source_run_evidence_is_immutable(tmp_path, field, value, observed):
    store = SkillStore(tmp_path / "evidence.db")
    args = dict(scenario_type="initial_pressure_2d", state_text="test",
        normalized_params={"dx_m": .001}, code_template="pass", postconditions={},
        physics_evidence={"error": .001}, source_run_id="original",
        validator_version="v", quality_level="Q2", idempotent_source_run=True)
    sid = store.create_candidate(**args)
    if observed:
        args["source_run_id"] = "observed"
        store.create_candidate(**args)
    assert store.create_candidate(**args) == sid
    with sqlite3.connect(store.path) as db:
        before = db.execute("SELECT success_count FROM simulation_skills").fetchone()[0]
        events = db.execute("SELECT count(*) FROM skill_events").fetchone()[0]
        if observed:
            detail = json.loads(db.execute("SELECT detail FROM skill_events WHERE event_type='candidate_observed'").fetchone()[0])
            assert detail["evidence_binding"]["physics_evidence"] == {"error": .001}
    args[field] = value
    with pytest.raises(ValueError, match="source_run_evidence_conflict"):
        store.create_candidate(**args)
    with sqlite3.connect(store.path) as db:
        assert db.execute("SELECT success_count FROM simulation_skills").fetchone()[0] == before
        assert db.execute("SELECT count(*) FROM skill_events").fetchone()[0] == events

def test_same_candidate_source_cannot_cross_parameter_fingerprint(tmp_path):
    store=SkillStore(tmp_path/"skills.db")
    values=dict(scenario_type="point_source_2d",state_text="old normalization",
        normalized_params={"frequency_hz":100000,"dx_m":0.00025},
        code_template="print('same')",postconditions={"has_signal":True},
        physics_evidence={"stdout_sha256":"a"*64},source_run_id="legacy-source",
        validator_version="candidate-intake.v1",quality_level="Q2",idempotent_source_run=True)
    first=store.create_candidate(**values)
    assert store.create_candidate(**values)==first
    with pytest.raises(ValueError,match="parameter_identity"):
        store.create_candidate(**dict(values,normalized_params=dict(values["normalized_params"],dimensions=2)))
    with sqlite3.connect(store.path) as db:
        assert db.execute("SELECT count(*),max(success_count) FROM simulation_skills").fetchone()==(1,1)
        assert db.execute("SELECT count(*) FROM skill_events").fetchone()[0]==1
        plan=db.execute("EXPLAIN QUERY PLAN SELECT skill_id FROM skill_versions WHERE validator_version=? AND source_run_id=? LIMIT 2",
            ("candidate-intake.v1","legacy-source")).fetchall()
        assert "idx_candidate_source_version" in str(plan)


def test_schema14_index_collision_rolls_back_entire_upgrade(tmp_path):
    store=SkillStore(tmp_path/"skills.db")
    sid=candidate(store)
    with sqlite3.connect(store.path) as db:
        before=db.execute("SELECT count(*) FROM skill_events").fetchone()[0]
        db.execute("DROP INDEX idx_skill_event_history")
        db.execute("DROP INDEX idx_skill_revalidation_epoch")
        db.execute("DROP INDEX idx_skill_dispatch_batch_case")
        db.execute("DROP TABLE skill_local_reference_completions")
        db.execute("DROP TABLE skill_local_reference_dispatches")
        db.execute("DELETE FROM schema_migrations WHERE version=17")
        db.execute("DROP TABLE skill_local_sources")
        db.execute("DELETE FROM schema_migrations WHERE version>=14")
        db.execute("CREATE INDEX idx_skill_revalidation_epoch ON skill_events(created_at)")
    with pytest.raises(sqlite3.OperationalError):store.migrate()
    with sqlite3.connect(store.path) as db:
        assert db.execute("SELECT max(version) FROM schema_migrations").fetchone()[0]==13
        assert db.execute("SELECT count(*) FROM skill_events").fetchone()[0]==before
        assert not db.execute("SELECT 1 FROM sqlite_master WHERE name='idx_skill_event_history'").fetchone()
        db.execute("DROP INDEX idx_skill_revalidation_epoch")
    store.migrate()
    assert store.get_skill(sid)["status"]=="candidate"


def test_schema15_batch_case_duplicate_history_rolls_back_without_deleting(tmp_path):
    import uuid
    store=SkillStore(tmp_path/"skills.db");sid=candidate(store)
    with sqlite3.connect(store.path) as db:
        db.execute("DROP INDEX idx_skill_dispatch_batch_case")
        db.execute("DROP TABLE skill_local_reference_completions")
        db.execute("DROP TABLE skill_local_reference_dispatches")
        db.execute("DELETE FROM schema_migrations WHERE version=17")
        db.execute("DROP TABLE skill_local_sources")
        db.execute("DELETE FROM schema_migrations WHERE version>=15")
        rows=[(str(uuid.uuid4()),uuid.uuid4().hex,"root",1,
            '{"batch_id":"same","case":"base"}',"a"*64,"{}",1) for _ in range(2)]
        db.executemany("INSERT INTO skill_host_dispatches VALUES (?,?,?,?,?,?,?,?)",rows)
        before=db.execute("SELECT COUNT(*) FROM skill_events").fetchone()[0]
    with pytest.raises(sqlite3.IntegrityError):store.migrate()
    with sqlite3.connect(store.path) as db:
        assert db.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]==14
        assert db.execute("SELECT COUNT(*) FROM skill_host_dispatches").fetchone()[0]==2
        assert db.execute("SELECT COUNT(*) FROM skill_events").fetchone()[0]==before
        assert not db.execute("SELECT 1 FROM sqlite_master WHERE name='idx_skill_dispatch_batch_case'").fetchone()


def test_schema15_legacy_nonbatch_and_malformed_contexts_preserved(tmp_path):
    import uuid
    store=SkillStore(tmp_path/"skills.db");sid=candidate(store)
    with sqlite3.connect(store.path) as db:
        db.execute("DROP INDEX idx_skill_dispatch_batch_case")
        db.execute("DROP TABLE skill_local_reference_completions")
        db.execute("DROP TABLE skill_local_reference_dispatches")
        db.execute("DELETE FROM schema_migrations WHERE version=17")
        db.execute("DROP TABLE skill_local_sources")
        db.execute("DELETE FROM schema_migrations WHERE version>=15")
        documents=['{}','{bad json}','{"batch_id":null,"case":"base"}']
        db.executemany("INSERT INTO skill_host_dispatches VALUES (?,?,?,?,?,?,?,?)",
            [(str(uuid.uuid4()),uuid.uuid4().hex,"legacy",1,raw,"a"*64,"{}",1) for raw in documents])
    store.migrate()
    with sqlite3.connect(store.path) as db:
        assert db.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]==SCHEMA_VERSION
        assert sorted(x[0] for x in db.execute("SELECT context_json FROM skill_host_dispatches"))==sorted(documents)
    assert store.get_skill(sid)["status"]=="candidate"
