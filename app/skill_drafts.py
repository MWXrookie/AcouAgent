"""Opt-in authoring drafts. Never executes, learns, migrates or activates skills."""
from contextlib import contextmanager
import json
import re
import sqlite3
import time
import uuid

from app import config, skill_spec, skill_authoring_schema


class DraftError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _identity(value, code):
    try:
        valid = type(value) is str and bool(value.strip()) and len(value.encode("utf-8")) <= 128
    except UnicodeError:
        valid = False
    if not valid:
        raise DraftError(code)
    return value


def _identifier(value):
    try:
        if type(value) is not str or str(uuid.UUID(value)) != value:
            raise ValueError
    except (ValueError, AttributeError):
        raise DraftError("invalid_draft_id") from None
    return value


def _revision(value):
    if type(value) is not int or not 1 <= value <= 2**63-1:
        raise DraftError("invalid_revision")


class DraftStore:
    """Use an operator-selected SkillStore whose schema18 already exists."""

    def __init__(self, store):
        self.store = store

    @staticmethod
    def _enabled():
        if getattr(config, "SKILL_AUTHORING_ENABLED", False) is not True:
            raise DraftError("authoring_disabled")

    @staticmethod
    def _schema(db):
        if not skill_authoring_schema.schema_ready(db):
            raise DraftError("migration_required")

    @contextmanager
    def _connection(self, write=False):
        self._enabled()
        try:
            manager = self.store._db_existing_write() if write else self.store._read_db()
            with manager as db:
                if write:
                    db.execute("PRAGMA busy_timeout=0")
                    for delay in (0, .05, .15, .45):
                        if delay:
                            time.sleep(delay)
                        try:
                            db.execute("BEGIN IMMEDIATE")
                            break
                        except sqlite3.OperationalError as exc:
                            code = getattr(exc, "sqlite_errorcode", 0) or 0
                            if code & 255 not in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED) or delay == .45:
                                raise
                self._schema(db)
                yield db
                if write:
                    self._enabled()
        except (sqlite3.Error, OSError, TimeoutError):
            raise DraftError("draft_store_unavailable") from None

    @staticmethod
    def _prepare(raw):
        try:
            return skill_spec.prepare_skill_spec(raw)
        except ValueError:
            raise DraftError("invalid_spec") from None

    @staticmethod
    def _head(db, draft_id, actor_id):
        row = db.execute("SELECT * FROM skill_drafts WHERE draft_id=? AND actor_id=?",
                         (draft_id, actor_id)).fetchone()
        if row is None:
            raise DraftError("not_found")
        return row

    @staticmethod
    def _view(db, head, version=None):
        version = head["current_version"] if version is None else version
        row = db.execute("SELECT * FROM skill_draft_versions WHERE draft_id=? AND version=?",
                         (head["draft_id"], version)).fetchone()
        if row is None:
            raise DraftError("not_found")
        try:
            spec = json.loads(row["spec_json"])
            if type(spec) is not dict:
                raise ValueError
        except (ValueError, TypeError, UnicodeError):
            raise DraftError("draft_store_unavailable") from None
        return dict(draft_id=head["draft_id"], version=version,
                    current_version=head["current_version"], revision=head["revision"],
                    state=head["state"], origin=head["origin"], spec=spec,
                    content_sha256=row["content_sha256"], case_fingerprint=row["case_fingerprint"],
                    code_sha256=row["code_sha256"], normalizer_version=row["normalizer_version"],
                    fingerprint_version=row["fingerprint_version"],
                    created_ms=head["created_ms"], updated_ms=head["updated_ms"])

    @staticmethod
    def _version(db, draft_id, version, prepared, now):
        db.execute("""INSERT INTO skill_draft_versions
            (draft_id,version,spec_version,spec_json,content_sha256,case_fingerprint,
             normalizer_version,fingerprint_version,code_sha256,source_run_id,created_ms)
            VALUES (?,?,?,?,?,?,?,?,?,NULL,?)""",
            (draft_id,version,prepared["spec"]["schema_version"],
             json.dumps(prepared["spec"],sort_keys=True,ensure_ascii=False,allow_nan=False),
             prepared["content_sha256"],prepared["case_fingerprint"],
             prepared["normalizer_version"],prepared["fingerprint_version"],
             prepared["code_sha256"],now))

    def create(self, raw, *, actor_id, idempotency_key, origin="manual"):
        self._enabled()
        actor_id = _identity(actor_id, "invalid_actor")
        if origin != "manual":
            raise DraftError("unsupported_origin")
        if type(idempotency_key) is not str or re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}",idempotency_key) is None:
            raise DraftError("invalid_idempotency_key")
        prepared = self._prepare(raw)
        with self._connection(True) as db:
            request = db.execute("""SELECT * FROM skill_draft_requests
                WHERE actor_id=? AND operation='create' AND idempotency_key=?""",
                (actor_id,idempotency_key)).fetchone()
            if request:
                if request["request_sha256"] != prepared["content_sha256"]:
                    raise DraftError("idempotency_conflict")
                result = self._view(db,self._head(db,request["draft_id"],actor_id),request["version"])
                result["duplicate"] = True
                return result
            now = time.time_ns() // 1000000
            draft_id = str(uuid.uuid4())
            db.execute("""INSERT INTO skill_drafts
                (draft_id,origin,actor_id,state,current_version,revision,created_ms,updated_ms)
                VALUES (?,'manual',?,'editing',1,1,?,?)""",(draft_id,actor_id,now,now))
            self._version(db,draft_id,1,prepared,now)
            db.execute("""INSERT INTO skill_draft_requests
                (actor_id,operation,idempotency_key,request_sha256,draft_id,version,created_ms)
                VALUES (?,'create',?,?,?,?,?)""",
                (actor_id,idempotency_key,prepared["content_sha256"],draft_id,1,now))
            result = self._view(db,self._head(db,draft_id,actor_id))
            result["duplicate"] = False
            return result

    def get(self, draft_id, *, actor_id, version=None):
        _identifier(draft_id); _identity(actor_id,"invalid_actor")
        if version is not None:
            _revision(version)
        with self._connection() as db:
            return self._view(db,self._head(db,draft_id,actor_id),version)

    def update(self, draft_id, raw, *, actor_id, expected_revision):
        self._enabled()
        _identifier(draft_id); _identity(actor_id,"invalid_actor"); _revision(expected_revision)
        prepared = self._prepare(raw)
        with self._connection(True) as db:
            head = self._head(db,draft_id,actor_id)
            if head["revision"] != expected_revision:
                raise DraftError("revision_conflict")
            if head["state"] != "editing":
                raise DraftError("draft_not_editable")
            current = self._view(db,head)
            if current["content_sha256"] == prepared["content_sha256"]:
                current["unchanged"] = True
                return current
            # Historical versions are immutable and content-unique.
            prior = db.execute("SELECT version FROM skill_draft_versions WHERE draft_id=? AND content_sha256=?",
                               (draft_id,prepared["content_sha256"])).fetchone()
            if prior:
                raise DraftError("historical_content_conflict")
            version, now = head["current_version"]+1, time.time_ns()//1000000
            self._version(db,draft_id,version,prepared,now)
            changed = db.execute("""UPDATE skill_drafts SET current_version=?,revision=revision+1,updated_ms=?
                WHERE draft_id=? AND actor_id=? AND revision=? AND state='editing'""",
                (version,now,draft_id,actor_id,expected_revision))
            if changed.rowcount != 1:
                raise DraftError("revision_conflict")
            result = self._view(db,self._head(db,draft_id,actor_id))
            result["unchanged"] = False
            return result

    def list_drafts(self, *, actor_id, limit=20, after_id=None):
        _identity(actor_id,"invalid_actor")
        if type(limit) is not int or not 1 <= limit <= 100:
            raise DraftError("invalid_page")
        with self._connection() as db:
            args, where = [actor_id], "actor_id=?"
            if after_id is not None:
                _identifier(after_id)
                head = self._head(db,after_id,actor_id)
                where += " AND (created_ms>? OR (created_ms=? AND draft_id>?))"
                args.extend([head["created_ms"],head["created_ms"],after_id])
            rows = db.execute("SELECT * FROM skill_drafts WHERE "+where+
                              " ORDER BY created_ms,draft_id LIMIT ?",(*args,limit+1)).fetchall()
            items = [self._view(db,row) for row in rows[:limit]]
            return dict(items=items,next_after_id=items[-1]["draft_id"] if len(rows)>limit else None)

    def archive(self, draft_id, *, actor_id, expected_revision):
        _identifier(draft_id); _identity(actor_id,"invalid_actor"); _revision(expected_revision)
        with self._connection(True) as db:
            head = self._head(db,draft_id,actor_id)
            if head["revision"] != expected_revision:
                raise DraftError("revision_conflict")
            if head["state"] == "archived":
                return self._view(db,head)
            if head["state"] != "editing":
                raise DraftError("draft_not_editable")
            changed=db.execute("""UPDATE skill_drafts SET state='archived',revision=revision+1,updated_ms=?
                WHERE draft_id=? AND actor_id=? AND revision=? AND state='editing'""",
                (time.time_ns()//1000000,draft_id,actor_id,expected_revision))
            if changed.rowcount != 1:
                raise DraftError("revision_conflict")
            return self._view(db,self._head(db,draft_id,actor_id))
