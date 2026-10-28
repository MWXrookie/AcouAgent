"""Logical hashes for stopped, test-owned desktop data. Prints no row contents.

For stopped data, run with /qa mounted read-only and copy WAL companions.
Online mode uses SQLite's backup API. Both paths hash logical database rows,
including saved database backups; page counters are not scientific data.
"""
from collections import Counter
from contextlib import closing
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import tempfile


def digest(value):
    return hashlib.sha256(value).hexdigest()


def fingerprint(root, online=False):
    root = Path(root).resolve()
    result = {"databases": {}, "files": {}}
    required_types = ("skill_store.db", "execution_history.db", "error_cache.db")
    databases = []
    files = []
    for file in sorted(root.rglob("*")):
        relative = file.relative_to(root).as_posix()
        if relative.split("/")[0] == ".desktop-snapshots":
            continue
        if file.is_symlink():
            raise ValueError("unexpected_data_link")
        if file.is_file():
            with file.open("rb") as stream:
                sqlite = stream.read(16) == b"SQLite format 3\x00"
            if relative in required_types and not sqlite:
                raise ValueError("database_integrity_failed")
            (databases if sqlite else files).append(relative)
    with tempfile.TemporaryDirectory(prefix="desktop-fingerprint-") as directory:
        for name in databases:
            original = root / name
            copied = Path(directory) / name
            copied.parent.mkdir(parents=True, exist_ok=True)
            if online:
                with closing(sqlite3.connect(original.as_uri() + "?mode=ro", uri=True)) as source:
                    with closing(sqlite3.connect(copied)) as destination:
                        source.backup(destination)
            else:
                for suffix in ("", "-wal", "-shm"):
                    source = root / (name + suffix)
                    if source.is_file():
                        shutil.copyfile(source, Path(directory) / (name + suffix))
            with closing(sqlite3.connect(copied)) as database:
                if database.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
                    raise ValueError("database_integrity_failed")
                tables = {}
                for table, sql in database.execute("SELECT name,sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall():
                    quoted = '"' + table.replace('"', '""') + '"'
                    cursor = database.execute("SELECT * FROM " + quoted)
                    rows = Counter()
                    for row in cursor:
                        clean = [{"blobSha256": digest(value), "bytes": len(value)} if isinstance(value, bytes) else value for value in row]
                        rows[digest(json.dumps(clean, ensure_ascii=False, separators=(",", ":")).encode())] += 1
                    tables[table] = {"columns": [item[0] for item in cursor.description], "definitionSha256": digest((sql or "").encode()), "rows": dict(sorted(rows.items()))}
                result["databases"][name] = tables
            for suffix in ("", "-wal", "-shm"):
                Path(str(copied) + suffix).unlink(missing_ok=True)
        excluded = {name + suffix for name in databases for suffix in ("-wal", "-shm")}
        for relative in files:
            if relative not in excluded:
                result["files"][relative] = digest((root / relative).read_bytes())
    return result


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default="/qa")
    parser.add_argument("--online", action="store_true")
    args = parser.parse_args()
    print(json.dumps(fingerprint(Path(args.root), online=args.online), sort_keys=True, separators=(",", ":")))
