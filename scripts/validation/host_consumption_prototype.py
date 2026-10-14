"""Explicitly initialized temporary SQLite ledger. No production root/API."""
from contextlib import contextmanager,closing
import hashlib
import re
import sqlite3
from pathlib import Path
from scripts.validation import host_public_key_prototype as signature


def initialize_test_ledger(path):
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("BEGIN IMMEDIATE")
        db.execute("CREATE TABLE roots(id TEXT,revision INTEGER,key BLOB,state TEXT,PRIMARY KEY(id,revision))")
        db.execute("CREATE TABLE consumption(run_id TEXT,nonce TEXT,context_hash TEXT,root_id TEXT,revision INTEGER,PRIMARY KEY(run_id,nonce))")
        for table in ("roots","consumption"):
            for operation in ("UPDATE","DELETE"):
                db.execute(f"""CREATE TRIGGER {table}_no_{operation.lower()} BEFORE {operation} ON {table}
                    BEGIN SELECT RAISE(ABORT,'append-only'); END""")
            keys="id=NEW.id AND revision=NEW.revision" if table=="roots" else "run_id=NEW.run_id AND nonce=NEW.nonce"
            db.execute(f"""CREATE TRIGGER {table}_no_replace BEFORE INSERT ON {table}
                WHEN EXISTS(SELECT 1 FROM {table} WHERE {keys})
                BEGIN SELECT RAISE(ABORT,'append-only'); END""")


@contextmanager
def _db(path):
    # Consumption/root mutation never creates a missing ledger.
    db=sqlite3.connect(Path(path).resolve().as_uri()+"?mode=rw",uri=True,timeout=2)
    try:
        with db:
            yield db
    finally:
        db.close()


def append_test_root(path, root_id, revision, public_key, *, revoked=False):
    if (not isinstance(root_id,str) or not 1<=len(root_id)<=128
            or type(revision) is not int or revision<1
            or not isinstance(public_key,bytes) or len(public_key)!=32 or type(revoked) is not bool):
        raise ValueError("invalid_test_root")
    with _db(path) as db:
        db.execute("BEGIN IMMEDIATE")
        latest=db.execute("SELECT revision,state FROM roots WHERE id=? ORDER BY revision DESC LIMIT 1",(root_id,)).fetchone()
        if revision != (latest[0]+1 if latest else 1) or (latest and latest[1]=="revoked"):
            raise ValueError("stale_or_revoked_test_root")
        db.execute("INSERT INTO roots VALUES(?,?,?,?)",(root_id,revision,public_key,"revoked" if revoked else "test-active"))


def consume_test_signature(path, root_id, revision, document, *, expected):
    denied={"consumed":False,"authenticated":False,"trusted_run":False,
            "runtime_approved":False,"run_bound":False,"parameters_bound":False}
    try:
        if (not isinstance(expected,dict) or not isinstance(expected.get("run_id"),str)
                or not 1<=len(expected["run_id"])<=128
                or not isinstance(expected.get("nonce"),str)
                or not re.fullmatch("[0-9a-f]{32}",expected["nonce"])
                or type(revision) is not int):
            return dict(denied,reason="invalid_context")
        with _db(path) as db:
            db.execute("BEGIN IMMEDIATE")
            root=db.execute("SELECT revision,key,state FROM roots WHERE id=? ORDER BY revision DESC LIMIT 1",(root_id,)).fetchone()
            if not root or root[0]!=revision or root[2]!="test-active":
                return dict(denied,reason="root_unavailable_or_stale")
            checked=signature.inspect_test_signature(document,root[1],expected=expected)
            if not checked["signature_matched"]:
                return dict(denied,reason=checked["reason"])
            digest=hashlib.sha256(signature.canonical(expected)).hexdigest()
            prior=db.execute("SELECT context_hash FROM consumption WHERE run_id=? AND nonce=?",
                             (expected["run_id"],expected["nonce"])).fetchone()
            if prior:
                return dict(denied,reason="already_consumed" if prior[0]==digest else "consumption_conflict")
            db.execute("INSERT INTO consumption VALUES(?,?,?,?,?)",
                       (expected["run_id"],expected["nonce"],digest,root_id,revision))
            return dict(denied,consumed=True,reason="test_consumption_only")
    except (sqlite3.Error,OSError,ValueError,TypeError):
        return dict(denied,reason="ledger_unavailable")
