import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
import pytest
from scripts.validation import host_consumption_prototype as ledger
from tests.unit.test_host_public_key_prototype import fixture


def prepared(tmp_path):
    path=tmp_path/"test.db";ledger.initialize_test_ledger(path)
    public,context,document=fixture()
    ledger.append_test_root(path,"test-root",1,public)
    return path,public,context,document


def test_one_time_consumption_and_revocation(tmp_path):
    path,public,context,document=prepared(tmp_path)
    def consume():return ledger.consume_test_signature(path,"test-root",1,document,expected=context)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda _:consume(),range(2)))
    assert sum(r["consumed"] for r in results)==1
    assert all(r["authenticated"] is False for r in results)
    ledger.append_test_root(path,"test-root",2,public,revoked=True)
    assert consume()["reason"]=="root_unavailable_or_stale"
    with pytest.raises(ValueError):ledger.append_test_root(path,"test-root",3,public)
    with sqlite3.connect(path) as db:
        for table in ("roots","consumption"):
            with pytest.raises(sqlite3.IntegrityError):db.execute("DELETE FROM "+table)


def test_wrong_signature_and_missing_db_do_not_consume(tmp_path):
    path,public,context,document=prepared(tmp_path)
    assert not ledger.consume_test_signature(path,"test-root",1,b"{}",expected=context)["consumed"]
    missing=tmp_path/"missing.db"
    assert not ledger.consume_test_signature(missing,"test-root",1,document,expected=context)["consumed"]
    assert not missing.exists()
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT count(*) FROM consumption").fetchone()[0]==0


def test_insert_failure_rolls_back_and_allows_retry(tmp_path):
    path,public,context,document=prepared(tmp_path)
    with sqlite3.connect(path) as db:
        db.execute("""CREATE TRIGGER test_fail BEFORE INSERT ON consumption
                      BEGIN SELECT RAISE(ABORT,'test'); END""")
    assert not ledger.consume_test_signature(path,"test-root",1,document,expected=context)["consumed"]
    with sqlite3.connect(path) as db:db.execute("DROP TRIGGER test_fail")
    assert ledger.consume_test_signature(path,"test-root",1,document,expected=context)["consumed"]


def test_ledger_connections_close_and_failures_roll_back(tmp_path):
    path,public,context,document=prepared(tmp_path)
    with ledger._db(path) as db:
        db.execute("SELECT 1")
    with pytest.raises(sqlite3.ProgrammingError):db.execute("SELECT 1")
    with pytest.raises(RuntimeError):
        with ledger._db(path) as failed:
            failed.execute("INSERT INTO consumption VALUES('rollback','nonce','hash','test-root',1)")
            raise RuntimeError("test")
    with pytest.raises(sqlite3.ProgrammingError):failed.execute("SELECT 1")
    with sqlite3.connect(path) as check:
        assert check.execute("SELECT COUNT(*) FROM consumption").fetchone()[0]==0


def test_test_ledger_schema_collision_rolls_back_whole_initialization(tmp_path):
    path=tmp_path/"collision.db"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE consumption(marker TEXT)")
    with pytest.raises(sqlite3.OperationalError):ledger.initialize_test_ledger(path)
    with sqlite3.connect(path) as db:
        assert not db.execute("SELECT 1 FROM sqlite_master WHERE name='roots'").fetchone()
        assert db.execute("PRAGMA table_info(consumption)").fetchone()[1]=="marker"
