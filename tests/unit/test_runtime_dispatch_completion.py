"""Per-dispatch completion receipt: recording, and consumption-time recheck.

The receipt is keyed by the gateway dispatch nonce (unique per attempt) rather
than the run id, because a retry loop reuses one run id across attempts.
Nothing here grants physical or runtime trust.
"""
import json
import sqlite3

import pytest

from app import runtime_registry_service as service
from app import tools
from scripts.validation import runtime_completion as completion
from scripts.validation import runtime_registry as registry
from tests.unit.test_runtime_registry import prepared
from tests.unit.test_runtime_registry_service import fixed  # noqa: F401  (pytest fixture)

NONCE = "a" * 32


def _approved(tmp_path):
    db, manifest, data = prepared(tmp_path)
    registry.transition(db, "test-only", 1, "approved", "operator", "fixture")
    with sqlite3.connect(db) as c:
        digest = c.execute("SELECT manifest_sha256 FROM runtime_packages").fetchone()[0]
    completion.initialize_dispatch(db)
    return db, digest


@pytest.mark.parametrize("mode,reason", [
    ("approved", "dispatch_registry_match_only"),
    ("retired", "not_currently_approved"),
    ("stale", "stale_revision"),
    ("hash", "manifest_mismatch"),
    ("missing", "missing_approval"),
])
def test_dispatch_receipt_rechecks_current_row(tmp_path, mode, reason):
    db, digest = _approved(tmp_path)
    if mode == "retired":
        registry.transition(db, "test-only", 2, "retired", "operator", "withdraw")
    out = completion.record_dispatch_completion(
        db, NONCE, "run-1", "unknown" if mode == "missing" else "test-only",
        1 if mode == "stale" else 2, "f" * 64 if mode == "hash" else digest)
    assert out["reason"] == reason
    assert out["dispatched_registry_matched"] == (mode == "approved")
    assert out["trusted_run"] is False and out["runtime_approved"] is False
    with sqlite3.connect(db) as c:
        assert c.execute("SELECT reason,trusted_run FROM runtime_dispatch_completions"
                         ).fetchone() == (reason, 0)
        with pytest.raises(sqlite3.IntegrityError):
            c.execute("UPDATE runtime_dispatch_completions SET reason='changed'")
        with pytest.raises(sqlite3.IntegrityError):
            c.execute("DELETE FROM runtime_dispatch_completions")


@pytest.mark.parametrize("nonce,run_id,revision,digest", [
    ("", "run-1", 2, "b" * 64),
    ("Z" * 32, "run-1", 2, "b" * 64),
    ("a" * 31, "run-1", 2, "b" * 64),
    (NONCE, "../escape", 2, "b" * 64),
    (NONCE, "run-1", 0, "b" * 64),
    (NONCE, "run-1", True, "b" * 64),
    (NONCE, "run-1", 2, "wrong"),
])
def test_invalid_dispatch_metadata_writes_nothing(tmp_path, nonce, run_id, revision, digest):
    db, _ = _approved(tmp_path)
    with pytest.raises(ValueError):
        completion.record_dispatch_completion(db, nonce, run_id, "test-only", revision, digest)
    with sqlite3.connect(db) as c:
        assert c.execute("SELECT COUNT(*) FROM runtime_dispatch_completions").fetchone()[0] == 0


def test_duplicate_dispatch_nonce_is_rejected(tmp_path):
    db, digest = _approved(tmp_path)
    completion.record_dispatch_completion(db, NONCE, "run-1", "test-only", 2, digest)
    with pytest.raises(sqlite3.IntegrityError):
        completion.record_dispatch_completion(db, NONCE, "run-1", "test-only", 2, digest)
    with sqlite3.connect(db) as c:
        assert c.execute("SELECT COUNT(*) FROM runtime_dispatch_completions").fetchone()[0] == 1


def test_consumption_recheck_is_invalidated_by_retirement(tmp_path):
    db, digest = _approved(tmp_path)
    completion.record_dispatch_completion(db, NONCE, "run-1", "test-only", 2, digest)
    live = completion.verify_dispatch_completion(db, NONCE)
    assert live["consumable"] is True and live["receipt_present"] is True
    assert live["trusted_run"] is False and live["runtime_approved"] is False
    registry.transition(db, "test-only", 2, "retired", "operator", "withdraw")
    dead = completion.verify_dispatch_completion(db, NONCE)
    assert dead["consumable"] is False and dead["reason"] == "not_currently_approved"
    assert dead["trusted_run"] is False and dead["runtime_approved"] is False


def test_consumption_recheck_rejects_missing_invalid_and_ineligible(tmp_path):
    db, digest = _approved(tmp_path)
    assert completion.verify_dispatch_completion(db, NONCE)["reason"] == "completion_receipt_missing"
    assert completion.verify_dispatch_completion(db, "not-a-nonce")["reason"] == "invalid_dispatch_nonce"
    completion.record_dispatch_completion(db, NONCE, "run-1", "unknown", 2, digest)
    out = completion.verify_dispatch_completion(db, NONCE)
    assert out["receipt_present"] is True
    assert out["consumable"] is False and out["reason"] == "completion_receipt_not_eligible"


def test_consumption_recheck_missing_database_is_not_created(tmp_path):
    absent = tmp_path / "absent.sqlite"
    out = completion.verify_dispatch_completion(absent, NONCE)
    assert out["consumable"] is False and out["reason"] == "registry_unavailable"
    assert not absent.exists()


def test_service_writes_nothing_without_a_matching_approval(fixed):  # noqa: F811
    assert service.capture_dispatch_contract()["contract_eligible"] is False
    out = service.record_dispatch_completion({"approval_window_matched": False}, NONCE, "run-1")
    assert out["completion_recorded"] is False
    assert out["reason"] == "completion_not_attempted"
    assert service.verify_completion_for_consumption(NONCE)["consumable"] is False
    assert not service.DATABASE.exists()


def test_service_receipt_and_consumption_follow_retirement(fixed):  # noqa: F811
    _, data = fixed
    token = service.config.PORTAL_ADMIN_TOKEN
    service.register_candidate(token, "fixture")
    service.change_status(token, 1, "approved", "fixture")
    window = service.recheck_dispatch_contract(
        service.capture_dispatch_contract(),
        {"matched": True, "receipt": {"runtime_sha256": data["runtime_sha256"]}})
    assert window["approval_window_matched"] is True
    out = service.record_dispatch_completion(window, NONCE, "run-1")
    assert out["completion_recorded"] is True and out["dispatched_registry_matched"] is True
    assert out["trusted_run"] is False and out["runtime_approved"] is False
    assert service.verify_completion_for_consumption(NONCE)["consumable"] is True
    service.change_status(token, 2, "retired", "withdraw")
    dead = service.verify_completion_for_consumption(NONCE)
    assert dead["consumable"] is False and dead["reason"] == "not_currently_approved"
    assert dead["trusted_run"] is False and dead["runtime_approved"] is False


@pytest.mark.parametrize("bad", ["", None, "nope"])
def test_service_consumption_rejects_bad_reference(fixed, bad):  # noqa: F811
    out = service.verify_completion_for_consumption(bad)
    assert out["consumable"] is False and out["reason"] == "missing_dispatch_reference"


def _mcp_with_tools():
    """Register the real MCP tools so the dispatch path is exercised end to end."""
    class MCP:
        def __init__(self):
            self.tools = {}

        def tool(self, function):
            self.tools[function.__name__] = function
            return function

    mcp = MCP()
    tools.register(mcp)
    return mcp


def _approval_fixture(fixed, monkeypatch, tmp_path, retire):
    """Approve the fixed package and script an executor response per dispatch."""
    from app import dashboard, execution
    from tests.unit.test_executor_evidence import envelope

    manifest, data = fixed
    _, receipt = envelope()
    data["runtime_sha256"] = receipt["runtime_sha256"]
    manifest.write_text(json.dumps(data))
    token = service.config.PORTAL_ADMIN_TOKEN
    service.register_candidate(token, "fixture")
    service.change_status(token, 1, "approved", "fixture")
    monkeypatch.setattr(dashboard, "DB_PATH", str(tmp_path / "history.db"))
    dashboard.init_db()

    def post(url, headers, json, timeout):
        if retire:
            service.change_status(token, 2, "retired", "in flight")
        result, rcp = envelope(code=json["code"], nonce=json["request_nonce"])
        result["executor_receipt"] = rcp

        class Response:
            def raise_for_status(self):
                pass

            def json(self):
                return result

        return Response()

    monkeypatch.setattr(execution.httpx, "post", post)
    return token


@pytest.mark.parametrize("retire", [False, True])
def test_gateway_dispatch_records_receipt_for_the_run(fixed, monkeypatch, tmp_path, retire):  # noqa: F811
    _approval_fixture(fixed, monkeypatch, tmp_path, retire)
    result = _mcp_with_tools().tools["run_jwave_code"]("print('ok')", run_id="run-1")
    evidence = result["execution_evidence"]
    info = evidence["approval_usage"]["completion"]
    assert info["trusted_run"] is False and info["runtime_approved"] is False
    assert info["completion_recorded"] == (not retire)
    completion.initialize_dispatch(service.DATABASE)
    with sqlite3.connect(service.DATABASE) as c:
        rows = c.execute("SELECT dispatch_nonce,run_id,eligible,trusted_run "
                         "FROM runtime_dispatch_completions").fetchall()
    # A dispatch whose approval was retired in flight is fail-closed: no receipt.
    assert len(rows) == (0 if retire else 1)
    if not retire:
        nonce, run_id, eligible, trusted = rows[0]
        assert run_id == "run-1" and eligible == 1 and trusted == 0
        assert nonce == evidence["gateway_request_nonce"]


def test_gateway_dispatch_writes_nothing_without_approval(monkeypatch, tmp_path):
    """Production equivalence: no approval package means no write and no DB."""
    from app import dashboard, execution
    from tests.unit.test_executor_evidence import envelope

    monkeypatch.setattr(service, "DATABASE", tmp_path / "registry.db")
    monkeypatch.setattr(service, "PACKAGE_ROOT", tmp_path / "absent-package")
    monkeypatch.setattr(dashboard, "DB_PATH", str(tmp_path / "history.db"))
    dashboard.init_db()

    def post(url, headers, json, timeout):
        result, rcp = envelope(code=json["code"], nonce=json["request_nonce"])
        result["executor_receipt"] = rcp

        class Response:
            def raise_for_status(self):
                pass

            def json(self):
                return result

        return Response()

    monkeypatch.setattr(execution.httpx, "post", post)
    result = _mcp_with_tools().tools["run_jwave_code"]("print('ok')", run_id="run-1")
    info = result["execution_evidence"]["approval_usage"]["completion"]
    assert info["completion_recorded"] is False
    assert info["reason"] == "completion_not_attempted"
    assert not service.DATABASE.exists()


def test_completion_cannot_be_replaced_or_consumed_for_another_run(tmp_path):
    db, digest = _approved(tmp_path)
    completion.record_dispatch_completion(db, NONCE, "run-1", "test-only", 2, digest)
    with sqlite3.connect(db) as c:
        before = c.execute("SELECT * FROM runtime_dispatch_completions").fetchall()
        with pytest.raises(sqlite3.IntegrityError, match="append_only"):
            c.execute("INSERT OR REPLACE INTO runtime_dispatch_completions SELECT * FROM runtime_dispatch_completions")
        assert c.execute("SELECT * FROM runtime_dispatch_completions").fetchall() == before
    assert completion.verify_dispatch_completion(db, NONCE, expected_run_id="run-1")["consumable"]
    denied = completion.verify_dispatch_completion(db, NONCE, expected_run_id="run-2")
    assert denied["reason"] == "completion_run_mismatch" and not denied["consumable"]
    assert not denied["trusted_run"] and not denied["runtime_approved"]


def test_completion_read_does_not_create_missing_database(tmp_path):
    path = tmp_path / "absent.db"
    denied = completion.verify_dispatch_completion(path, NONCE, expected_run_id="run-1")
    assert not denied["consumable"] and not path.exists()
