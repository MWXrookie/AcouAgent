import sqlite3
import pytest
from scripts.validation import runtime_registry as registry, runtime_completion as completion
from tests.unit.test_runtime_registry import prepared


@pytest.mark.parametrize("mode,reason", [
    ("approved","offline_registry_match_only"), ("retired","not_currently_approved"),
    ("stale","stale_revision"), ("hash","manifest_mismatch"), ("missing","missing_approval")])
def test_completion_rechecks_current_row(tmp_path, mode, reason):
    db, _, _ = prepared(tmp_path)
    registry.transition(db,"test-only",1,"approved","operator","fixture")
    completion.initialize(db)
    with sqlite3.connect(db) as c:
        digest=c.execute("SELECT manifest_sha256 FROM runtime_packages").fetchone()[0]
    if mode=="retired":
        registry.transition(db,"test-only",2,"retired","operator","withdraw")
    result=completion.record_completion(db,"run-1","unknown" if mode=="missing" else "test-only",
        1 if mode=="stale" else 2,"f"*64 if mode=="hash" else digest)
    assert result["reason"]==reason
    assert result["registry_snapshot_matched"]==(mode=="approved")
    assert not result["trusted_run"] and not result["runtime_approved"]
    with sqlite3.connect(db) as c:
        assert c.execute("SELECT reason,trusted_run FROM runtime_completion_receipts").fetchone()==(reason,0)
        with pytest.raises(sqlite3.IntegrityError):
            c.execute("UPDATE runtime_completion_receipts SET reason='changed'")
        with pytest.raises(sqlite3.IntegrityError):
            c.execute("DELETE FROM runtime_completion_receipts")


def test_duplicate_run_and_audit_failure_no_partial_receipts(tmp_path):
    db,_,_=prepared(tmp_path)
    completion.initialize(db)
    completion.record_completion(db,"run-1","test-only",1,"a"*64)
    with pytest.raises(sqlite3.IntegrityError):
        completion.record_completion(db,"run-1","test-only",1,"a"*64)
    with sqlite3.connect(db) as c:
        c.execute("CREATE TRIGGER reject_receipt BEFORE INSERT ON runtime_completion_receipts "
                  "BEGIN SELECT RAISE(ABORT,'injected'); END")
    with pytest.raises(sqlite3.IntegrityError):
        completion.record_completion(db,"run-2","test-only",1,"a"*64)
    with sqlite3.connect(db) as c:
        assert c.execute("SELECT COUNT(*) FROM runtime_completion_receipts").fetchone()[0]==1


@pytest.mark.parametrize("run,revision,digest", [("",2,"a"*64),("run",True,"a"*64),
    ("run",0,"a"*64),("run",2,"wrong"),("../escape",2,"a"*64)])
def test_invalid_completion_metadata(tmp_path,run,revision,digest):
    db,_,_=prepared(tmp_path)
    completion.initialize(db)
    with pytest.raises(ValueError):
        completion.record_completion(db,run,"test-only",revision,digest)
    with sqlite3.connect(db) as c:
        assert c.execute("SELECT COUNT(*) FROM runtime_completion_receipts").fetchone()[0]==0


def test_completion_and_retirement_are_serializable(tmp_path):
    import concurrent.futures
    import threading
    db, _, _ = prepared(tmp_path)
    registry.transition(db, "test-only", 1, "approved", "operator", "fixture")
    completion.initialize(db)
    with sqlite3.connect(db) as c:
        digest = c.execute("SELECT manifest_sha256 FROM runtime_packages").fetchone()[0]
    barrier = threading.Barrier(2)
    def finish():
        barrier.wait(timeout=3)
        return completion.record_completion(db, "racing-run", "test-only", 2, digest)
    def retire():
        barrier.wait(timeout=3)
        return registry.transition(db, "test-only", 2, "retired", "operator", "race")
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        a, b = pool.submit(finish), pool.submit(retire)
        result, retired = a.result(timeout=5), b.result(timeout=5)
    assert retired["revision"] == 3
    assert not result["trusted_run"] and not result["runtime_approved"]
    with sqlite3.connect(db) as c:
        receipt = c.execute("SELECT observed_revision,eligible,reason,trusted_run FROM runtime_completion_receipts").fetchone()
        assert receipt in [(2, 1, "offline_registry_match_only", 0),
                           (3, 0, "not_currently_approved", 0)]
        assert c.execute("SELECT status,revision FROM runtime_packages").fetchone() == ("retired", 3)
        assert c.execute("SELECT COUNT(*) FROM runtime_audit").fetchone()[0] == 3
    # A historical matching receipt never authorizes consumption after retirement.
    later = completion.record_completion(db, "later-run", "test-only", 2, digest)
    assert not later["registry_snapshot_matched"]
    assert later["reason"] == "not_currently_approved"
