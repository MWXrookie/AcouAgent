import sqlite3
import pytest
from app import controlled_dispatch
from app.controlled_request_adapter import make_envelope
RUN="12345678-1234-4234-8234-123456789012"
NONCE="a"*32

def test_request_context_is_atomic_append_only_and_readonly(tmp_path):
    path=tmp_path/"history.db"
    controlled_dispatch.record(path,RUN,NONCE,make_envelope(RUN,NONCE))
    result=controlled_dispatch.inspect_file(path,RUN,NONCE)
    assert result["dispatch_context_matched"] and result["request_context_matched"]
    assert not result["trusted_run"] and not result["parameters_bound"]
    with sqlite3.connect(path) as db:
        for sql in ["UPDATE controlled_request_contexts SET context_sha256='x'",
                    "DELETE FROM controlled_request_contexts",
                    "INSERT OR REPLACE INTO controlled_request_contexts SELECT * FROM controlled_request_contexts"]:
            with pytest.raises(sqlite3.IntegrityError):
                db.execute(sql)
    assert controlled_dispatch.inspect_file(path,RUN,NONCE)["request_context_matched"]

def test_second_insert_failure_rolls_back_first_dispatch_insert(tmp_path):
    path=tmp_path/"history.db"
    controlled_dispatch.record(path,RUN,NONCE,make_envelope(RUN,NONCE))
    second="12345678-1234-4234-8234-123456789013"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TRIGGER synthetic_fail BEFORE INSERT ON controlled_request_contexts BEGIN SELECT RAISE(ABORT,'synthetic'); END")
    with pytest.raises(sqlite3.IntegrityError,match="synthetic"):
        controlled_dispatch.record(path,second,"b"*32,make_envelope(second,"b"*32))
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT COUNT(*) FROM controlled_dispatches WHERE run_id=?",(second,)).fetchone()[0]==0
        assert db.execute("SELECT COUNT(*) FROM controlled_request_contexts WHERE run_id=?",(second,)).fetchone()[0]==0

def test_tampered_context_fails_closed_and_missing_db_not_created(tmp_path):
    path=tmp_path/"history.db"
    assert not controlled_dispatch.inspect_file(path,RUN,NONCE)["dispatch_context_matched"]
    assert not path.exists()
    controlled_dispatch.record(path,RUN,NONCE,make_envelope(RUN,NONCE))
    with sqlite3.connect(path) as db:
        db.execute("DROP TRIGGER request_context_no_update")
        db.execute("UPDATE controlled_request_contexts SET context_sha256='x'")
    result=controlled_dispatch.inspect_file(path,RUN,NONCE)
    assert not result["dispatch_context_matched"] and not result["request_context_matched"]

def test_invalid_envelope_never_opens_database(tmp_path):
    path=tmp_path/"history.db";e=make_envelope(RUN,NONCE);e["code"]+="bad"
    with pytest.raises(ValueError,match="envelope"):
        controlled_dispatch.record(path,RUN,NONCE,e)
    assert not path.exists()
