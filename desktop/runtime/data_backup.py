"""Data-only desktop snapshot. Run in the offline maintenance container.

The controller must stop all writers before export. No credentials are passed
to this service. SQLite uses its backup API, including committed WAL data.
"""
import argparse
from contextlib import closing
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import stat
import sys
import uuid
import zipfile

SNAPSHOTS = ".desktop-snapshots"
MAX_FILES = 100000
MAX_BYTES = 8 * 1024**3


def export_snapshot(root, output, metadata):
    root = Path(root).resolve(strict=True)
    snapshots = root / SNAPSHOTS
    if snapshots.is_symlink():
        raise ValueError("snapshot_directory_is_symlink")
    snapshots.mkdir(exist_ok=True)
    staging = snapshots / uuid.uuid4().hex
    staging.mkdir()
    entries = []
    total = 0
    try:
        paths = []
        for candidate in root.rglob("*"):
            relative = candidate.relative_to(root)
            if relative.parts[0] == SNAPSHOTS:
                continue
            mode = candidate.lstat().st_mode
            if stat.S_ISLNK(mode) or not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
                raise ValueError("unsupported_data_file_type")
            if stat.S_ISREG(mode):
                paths.append((candidate, relative))
        if len(paths) > MAX_FILES:
            raise ValueError("backup_file_limit")
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
            for source, relative in sorted(paths):
                # Companion files are represented by their consistent DB snapshot.
                if source.name.endswith(("-wal", "-shm")) and source.with_name(source.name[:-4]).is_file():
                    continue
                snapshot = source
                with source.open("rb") as probe:
                    sqlite = probe.read(16) == b"SQLite format 3\x00"
                versions = None
                if sqlite:
                    snapshot = staging / (uuid.uuid4().hex + ".sqlite")
                    with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True, timeout=5)) as database:
                        with closing(sqlite3.connect(snapshot)) as copied:
                            database.backup(copied)
                            if copied.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
                                raise ValueError("backup_integrity_failed")
                            if copied.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migrations'").fetchone():
                                versions = [row[0] for row in copied.execute("SELECT version FROM schema_migrations ORDER BY version")]
                size = snapshot.stat().st_size
                total += size
                if total > MAX_BYTES:
                    raise ValueError("backup_size_limit")
                digest = hashlib.sha256()
                name = "data/" + relative.as_posix()
                with snapshot.open("rb") as stream, archive.open(name, "w", force_zip64=True) as target:
                    while chunk := stream.read(1024 * 1024):
                        digest.update(chunk)
                        target.write(chunk)
                entry = {"path": name, "size": size, "sha256": digest.hexdigest()}
                if versions is not None:
                    entry["schemaVersions"] = versions
                entries.append(entry)
                if sqlite:
                    snapshot.unlink()
            archive.writestr("manifest.json", json.dumps({"schema": 1, "kind": "acouagent-desktop-data",
                "metadata": metadata, "files": entries, "totalBytes": total}, ensure_ascii=False))
    finally:
        # Exactly the random directory created above; never user data or the root.
        shutil.rmtree(staging)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--metadata", required=True)
    args = parser.parse_args()
    export_snapshot(Path("/app/data"), sys.stdout.buffer, json.loads(args.metadata))


if __name__ == "__main__":
    main()
