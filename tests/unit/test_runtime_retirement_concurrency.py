"""Approval snapshots are prerequisites, never completion-time authorization."""
import concurrent.futures
import sqlite3
import pytest
from app import runtime_registry_service as service
from tests.unit.test_runtime_registry_service import fixed, observed


def approve(data):
    token = service.config.PORTAL_ADMIN_TOKEN
    service.register_candidate(token, "isolated test")
    service.change_status(token, 1, "approved", "isolated review")
    return token, service.inspect_eligibility(*observed(data))


def test_retirement_invalidates_next_read_not_old_snapshot(fixed):
    _, data = fixed
    token, before = approve(data)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        pool.submit(service.change_status, token, 2, "retired", "concurrent withdrawal").result(timeout=5)
    after = service.inspect_eligibility(*observed(data))
    assert before["contract_eligible"] and before["revision"] == 2
    assert not before["runtime_approved"] and not before["trusted_run"]
    assert not after["contract_eligible"]
    assert after["reason"] == "not_currently_approved"
    with sqlite3.connect(service.DATABASE) as db:
        assert db.execute("SELECT to_status,revision FROM runtime_audit ORDER BY event_id").fetchall() == [
            ("candidate", 1), ("approved", 2), ("retired", 3)]


def test_two_retirements_commit_exactly_one_audit_event(fixed):
    _, data = fixed
    token, _ = approve(data)
    def retire():
        try:
            service.change_status(token, 2, "retired", "simultaneous withdrawal")
            return "committed"
        except ValueError as error:
            return str(error)
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(retire) for _ in range(2)]
        results = [future.result(timeout=5) for future in futures]
    assert sorted(results) == ["committed", "missing_or_stale_revision"]
    with sqlite3.connect(service.DATABASE) as db:
        assert db.execute("SELECT status,revision FROM runtime_packages").fetchone() == ("retired", 3)
        assert db.execute("SELECT COUNT(*) FROM runtime_audit").fetchone()[0] == 3


def test_failed_retirement_audit_rolls_back_and_keeps_eligibility(fixed):
    _, data = fixed
    token, _ = approve(data)
    with sqlite3.connect(service.DATABASE) as db:
        db.execute("CREATE TRIGGER reject_retire BEFORE INSERT ON runtime_audit "
                   "WHEN NEW.to_status='retired' BEGIN SELECT RAISE(ABORT,'injected'); END")
    with pytest.raises(sqlite3.IntegrityError):
        service.change_status(token, 2, "retired", "injected audit failure")
    result = service.inspect_eligibility(*observed(data))
    assert result["contract_eligible"] and result["revision"] == 2
    assert not result["runtime_approved"] and not result["trusted_run"]
    with sqlite3.connect(service.DATABASE) as db:
        assert db.execute("SELECT COUNT(*) FROM runtime_audit").fetchone()[0] == 2
