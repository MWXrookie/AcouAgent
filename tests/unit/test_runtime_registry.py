import json
import sqlite3
import pytest
from scripts.validation import runtime_registry as registry
from tests.unit.test_runtime_approval_contract import package


def prepared(tmp_path):
    manifest, data = package(tmp_path)
    database = tmp_path / "registry.sqlite"
    registry.initialize(database)
    registry.register(database, manifest, tmp_path, "test-operator", "fixture")
    return database, manifest, data


def test_lifecycle_audit(tmp_path):
    db, _, _ = prepared(tmp_path)
    assert not registry.transition(db, "test-only", 1, "approved", "operator", "reviewed fixture")["runtime_approved"]
    registry.transition(db, "test-only", 2, "retired", "operator", "withdrawn")
    with sqlite3.connect(db) as connection:
        assert connection.execute("SELECT to_status,revision FROM runtime_audit ORDER BY event_id").fetchall() == [("candidate",1),("approved",2),("retired",3)]
        assert connection.execute("SELECT status FROM runtime_packages").fetchone() == ("retired",)
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("DELETE FROM runtime_audit")
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("UPDATE runtime_audit SET actor='fake'")


@pytest.mark.parametrize("kind", ["stale", "missing", "bool", "target", "actor"])
def test_bad_transition_no_partial_audit(tmp_path, kind):
    db, _, _ = prepared(tmp_path)
    args = ["test-only", 1, "approved", "operator", "review"]
    if kind == "stale": args[1] = 2
    if kind == "missing": args[0] = "unknown"
    if kind == "bool": args[1] = True
    if kind == "target": args[2] = "candidate"
    if kind == "actor": args[3] = ""
    with pytest.raises(ValueError): registry.transition(db, *args)
    with sqlite3.connect(db) as c:
        assert c.execute("SELECT COUNT(*) FROM runtime_audit").fetchone()[0] == 1
        assert c.execute("SELECT revision FROM runtime_packages").fetchone()[0] == 1


def test_retired_cannot_reactivate(tmp_path):
    db, _, _ = prepared(tmp_path)
    registry.transition(db, "test-only", 1, "retired", "operator", "withdraw")
    with pytest.raises(ValueError): registry.transition(db, "test-only", 2, "approved", "operator", "again")


def test_duplicate_and_self_approved_package(tmp_path):
    db, manifest, data = prepared(tmp_path)
    with pytest.raises(sqlite3.IntegrityError): registry.register(db, manifest, tmp_path, "operator", "duplicate")
    data["status"] = "approved"
    manifest.write_text(json.dumps(data))
    with pytest.raises(ValueError): registry.register(db, manifest, tmp_path, "operator", "caller approved")


def test_audit_failure_rolls_back_state(tmp_path):
    db, _, _ = prepared(tmp_path)
    with sqlite3.connect(db) as c:
        c.execute("CREATE TRIGGER fail_insert BEFORE INSERT ON runtime_audit BEGIN SELECT RAISE(ABORT,'injected'); END")
    with pytest.raises(sqlite3.IntegrityError): registry.transition(db, "test-only", 1, "approved", "operator", "review")
    with sqlite3.connect(db) as c:
        assert c.execute("SELECT status,revision FROM runtime_packages").fetchone() == ("candidate",1)
