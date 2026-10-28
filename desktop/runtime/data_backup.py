"""Data-only desktop snapshot. Run in the offline maintenance container.

The controller must stop all writers before export. No credentials are passed
to this service. SQLite uses its backup API, including committed WAL data.
"""
import argparse
from contextlib import closing
import hashlib
import json
from pathlib import Path
from pathlib import PurePosixPath
import shutil
import sqlite3
import stat
import re
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


def restore_snapshot(root, incoming, expected):
    """Import only into a fresh, disposable volume. Never replace active data."""
    root = Path(root).resolve(strict=True)
    if any(root.iterdir()):
        raise ValueError("restore_requires_empty_volume")
    staging = root / SNAPSHOTS
    staging.mkdir()
    archive_path = staging / "incoming.zip"
    try:
        size = 0
        with archive_path.open("xb") as target:
            while chunk := incoming.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_BYTES + 1024**3:
                    raise ValueError("backup_size_limit")
                target.write(chunk)
        with zipfile.ZipFile(archive_path) as archive:
            members = archive.infolist()
            names = [entry.filename for entry in members]
            if any(stat.S_IFMT(entry.external_attr >> 16) not in (0, stat.S_IFREG) or entry.flag_bits & 1 for entry in members):
                raise ValueError("unsupported_backup_file_type")
            if len(names) != len(set(names)) or len(names) > MAX_FILES + 1:
                raise ValueError("invalid_backup_members")
            if names.count("manifest.json") != 1 or archive.getinfo("manifest.json").file_size > 32 * 1024**2:
                raise ValueError("invalid_backup_manifest")
            manifest = json.loads(archive.read("manifest.json"))
            if manifest.get("schema") != 1 or manifest.get("kind") != "acouagent-desktop-data":
                raise ValueError("unsupported_backup")
            metadata = manifest.get("metadata", {})
            if metadata.get("installation") != expected["installation"]:
                raise ValueError("backup_installation_mismatch")
            if any(metadata.get("images", {}).get(key) != expected["images"][key] for key in ("gateway", "executor")):
                raise ValueError("backup_runtime_mismatch")
            files = manifest.get("files")
            if not isinstance(files, list) or len(files) > MAX_FILES:
                raise ValueError("invalid_backup_manifest")
            listed = [item["path"] for item in files]
            if len(listed) != len(set(listed)) or set(names) != set(listed) | {"manifest.json"}:
                raise ValueError("backup_member_mismatch")
            if not {"data/skill_store.db", "data/execution_history.db"}.issubset(listed):
                raise ValueError("backup_databases_missing")
            total = 0
            for entry in files:
                name = entry["path"]
                relative = PurePosixPath(name)
                if (not isinstance(name, str) or "\\" in name or relative.is_absolute()
                    or len(relative.parts) < 2 or relative.parts[0] != "data"
                    or name != relative.as_posix() or any(part in ("", ".", "..", SNAPSHOTS) for part in relative.parts)
                    or name.endswith(("-wal", "-shm"))):
                    raise ValueError("unsafe_backup_path")
                info = archive.getinfo(name)
                mode = info.external_attr >> 16
                if info.is_dir() or (stat.S_IFMT(mode) not in (0, stat.S_IFREG)) or info.flag_bits & 1:
                    raise ValueError("unsupported_backup_file_type")
                if type(entry["size"]) is not int or entry["size"] < 0 or info.file_size != entry["size"]:
                    raise ValueError("backup_size_mismatch")
                total += entry["size"]
                if total > MAX_BYTES or not isinstance(entry["sha256"], str) or not re.fullmatch("[a-f0-9]{64}", entry["sha256"]):
                    raise ValueError("invalid_backup_manifest")
                target = root.joinpath(*relative.parts[1:])
                target.parent.mkdir(parents=True, exist_ok=True)
                digest = hashlib.sha256()
                actual = 0
                with archive.open(name) as source, target.open("xb") as copied:
                    while chunk := source.read(1024 * 1024):
                        actual += len(chunk)
                        if actual > entry["size"]:
                            raise ValueError("backup_size_mismatch")
                        digest.update(chunk)
                        copied.write(chunk)
                if actual != entry["size"] or digest.hexdigest() != entry["sha256"]:
                    raise ValueError("backup_digest_mismatch")
                with target.open("rb") as probe:
                    is_sqlite = probe.read(16) == b"SQLite format 3\x00"
                if name in ("data/skill_store.db", "data/execution_history.db", "data/error_cache.db") and not is_sqlite:
                    raise ValueError("backup_database_invalid")
                if is_sqlite:
                    with closing(sqlite3.connect(target.as_uri() + "?mode=ro&immutable=1", uri=True)) as database:
                        if database.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
                            raise ValueError("backup_integrity_failed")
                        if name == "data/skill_store.db" or "schemaVersions" in entry:
                            versions = [row[0] for row in database.execute("SELECT version FROM schema_migrations ORDER BY version")]
                            if versions != entry.get("schemaVersions", versions) or versions not in [list(range(1, 18)), list(range(1, 19))]:
                                raise ValueError("backup_schema_mismatch")
            if total != manifest.get("totalBytes"):
                raise ValueError("backup_size_mismatch")
        return {"restored": True, "files": len(files), "totalBytes": total, "settings": metadata.get("settings", {})}
    finally:
        shutil.rmtree(staging)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--metadata", required=True)
    parser.add_argument("--restore", action="store_true")
    args = parser.parse_args()
    if args.restore:
        print(json.dumps(restore_snapshot(Path("/app/data"), sys.stdin.buffer, json.loads(args.metadata))))
    else:
        export_snapshot(Path("/app/data"), sys.stdout.buffer, json.loads(args.metadata))


if __name__ == "__main__":
    main()
