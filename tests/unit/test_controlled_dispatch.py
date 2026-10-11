import json
import sqlite3

import pytest

from app import controlled_dispatch, controlled_validation, dashboard, execution
from scripts.validation import plane_wave_run_binding as binding
from tests.unit.test_controlled_validation import fake_execute


@pytest.fixture
def history(tmp_path, monkeypatch):
    database = tmp_path / "history.db"
    monkeypatch.setattr(dashboard, "DB_PATH", str(database))
    dashboard.init_db()
    return database


def test_context_is_persisted_before_http_and_correlated_after(history, monkeypatch):
    def dispatch(code, timeout, *, request_nonce):
        with sqlite3.connect(history) as db:
            row = db.execute("SELECT run_id,request_nonce FROM controlled_dispatches").fetchone()
        assert row[1] == request_nonce
        assert controlled_dispatch.inspect_file(history, *row)["dispatch_context_matched"]
        return fake_execute(code, timeout, request_nonce=request_nonce)
    monkeypatch.setattr(execution, "_execute_code", dispatch)
    checked = controlled_validation.run_controlled_plane_wave()["validation"]
    assert checked["dispatch_context_matched"]
    assert checked["provenance_prerequisites_passed"]
    for field in ("trusted_run", "parameters_bound", "runtime_approved"):
        assert checked[field] is False


def test_context_is_append_only_including_replace_and_duplicate_nonce(history):
    controlled_dispatch.record(history, "run-one", "a" * 32)
    with sqlite3.connect(history) as db:
        before = db.execute("SELECT * FROM controlled_dispatches").fetchall()
        for statement in (
            "UPDATE controlled_dispatches SET timeout_seconds=1",
            "DELETE FROM controlled_dispatches",
            "INSERT OR REPLACE INTO controlled_dispatches SELECT * FROM controlled_dispatches",
        ):
            with pytest.raises(sqlite3.IntegrityError, match="append-only"):
                db.execute(statement)
            assert db.execute("SELECT * FROM controlled_dispatches").fetchall() == before
    with pytest.raises(sqlite3.IntegrityError):
        controlled_dispatch.record(history, "run-two", "a" * 32)
    assert not controlled_dispatch.inspect_file(history, "run-two", "a" * 32)["dispatch_context_matched"]


@pytest.mark.parametrize("run_id,nonce", [("", "a"*32), ("x"*129, "a"*32), ("run", "A"*32), ("run", True)])
def test_invalid_context_input_never_creates_row(history, run_id, nonce):
    with pytest.raises(ValueError):
        controlled_dispatch.record(history, run_id, nonce)


def test_context_write_failure_stops_before_execution(history, monkeypatch):
    def fail(*args):
        raise sqlite3.OperationalError("synthetic failure")
    monkeypatch.setattr(controlled_dispatch, "record", fail)
    monkeypatch.setattr(execution, "_execute_code", lambda *args, **kwargs: pytest.fail("must not dispatch"))
    with pytest.raises(sqlite3.OperationalError):
        controlled_validation.run_controlled_plane_wave()


def test_forged_executor_nonce_cannot_replace_preexisting_context(history, monkeypatch):
    monkeypatch.setattr(execution, "_execute_code", fake_execute)
    run = controlled_validation.run_controlled_plane_wave()
    with sqlite3.connect(history) as db:
        stored = json.loads(db.execute("SELECT executor_evidence_json FROM executions").fetchone()[0])
        # An internally consistent executor envelope with a different nonce
        # still must not replace the server's earlier dispatch context.
        stored["gateway_request_nonce"] = "f"*32
        stored["receipt"]["request_nonce"] = "f"*32
        stored["dispatch_context_matched"] = True
        db.execute("UPDATE executions SET executor_evidence_json=?", (json.dumps(stored),))
    checked = binding.inspect_run(history, run["run_id"])
    assert checked["execution_receipt_matched"]
    assert not checked["dispatch_context_matched"]
    assert not checked["provenance_prerequisites_passed"]
    assert checked["parameters_bound"] is False


def test_legacy_database_is_not_backfilled(history):
    assert not controlled_dispatch.inspect_file(history, "run", "a"*32)["dispatch_context_matched"]
    with sqlite3.connect(history) as db:
        assert not db.execute("SELECT 1 FROM sqlite_master WHERE name='controlled_dispatches'").fetchall()


def test_controlled_path_records_completion_with_server_run_and_nonce(history, monkeypatch):
    from app import runtime_registry_service as service
    monkeypatch.setattr(execution, "_execute_code", fake_execute)
    calls = []
    def record(window, nonce, run_id):
        with sqlite3.connect(history) as db:
            assert db.execute("SELECT COUNT(*) FROM executions WHERE run_id=?", (run_id,)).fetchone()[0] == 1
            assert db.execute("SELECT request_nonce FROM controlled_dispatches WHERE run_id=?", (run_id,)).fetchone()[0] == nonce
        calls.append((nonce, run_id))
        return {"completion_recorded":False,"trusted_run":False,"runtime_approved":False}
    monkeypatch.setattr(service, "record_dispatch_completion", record)
    result = controlled_validation.run_controlled_plane_wave()
    assert len(calls) == 1 and calls[0][1] == result["run_id"]
    assert not result["approval_completion"]["trusted_run"]
