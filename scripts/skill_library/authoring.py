"""Operator-only schema18 migration and status. No HTTP or skill activation."""
import argparse
from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import time
import uuid

from app.skill_authoring_schema import schema_ready
from app.skill_store import SkillStore


def migrate_with_backup(database, backup_dir):
    database = Path(database).resolve()
    if not database.is_file():
        raise ValueError("existing_skill_database_required")
    backup_dir = Path(backup_dir).resolve()
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup = backup_dir / ("skill-store-pre-authoring-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                           + "-" + uuid.uuid4().hex + ".sqlite")
    started = time.monotonic()

    def progress(_status, _remaining, _total):
        if time.monotonic() - started > 15:
            raise TimeoutError("backup_timeout")

    with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=5)) as source:
        versions = [r[0] for r in source.execute("SELECT version FROM schema_migrations ORDER BY version")]
        if schema_ready(source):
            return {"schema": 18, "changed": False}
        if versions != list(range(1, 18)) or source.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise ValueError("healthy_schema17_required")
        with closing(sqlite3.connect(backup)) as destination:
            source.backup(destination, pages=256, progress=progress, sleep=.05)
            if destination.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise ValueError("backup_integrity_failed")
    SkillStore(database).migrate_skill_authoring()
    return {"schema": 18, "changed": True, "backup": str(backup), "skills_activated": 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("status", "migrate"))
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--backup-dir", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "migrate":
            if args.backup_dir is None:
                parser.error("migrate requires --backup-dir")
            result = migrate_with_backup(args.database, args.backup_dir)
        else:
            with closing(sqlite3.connect(args.database.resolve().as_uri() + "?mode=ro", uri=True)) as db:
                result = {"authoring_schema_ready": schema_ready(db),
                          "schema": db.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]}
        print(json.dumps(result))
        return 0
    except (ValueError, OSError, sqlite3.Error, TimeoutError):
        print(json.dumps({"error": "authoring_migration_or_status_failed"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
