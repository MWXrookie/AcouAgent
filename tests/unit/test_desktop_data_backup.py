"""Real ZIP and SQLite WAL snapshots, without Docker or third-party dependencies."""
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sqlite3
import zipfile

import pytest

spec = importlib.util.spec_from_file_location("desktop_data_backup", Path(__file__).resolve().parents[2] / "desktop/runtime/data_backup.py")
backup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backup)


def test_snapshot_captures_committed_wal_and_assets_without_mutating_source(tmp_path):
    database = tmp_path / "history.db"
    with sqlite3.connect(database) as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("CREATE TABLE executions(value TEXT)")
        db.execute("INSERT INTO executions VALUES('kept in WAL')")
        db.commit()
        (tmp_path / "skill_assets").mkdir()
        (tmp_path / "skill_assets" / "field.npy").write_bytes(b"actual bytes\x00\xff")
        before = database.read_bytes()
        output = io.BytesIO()
        backup.export_snapshot(tmp_path, output, {"images": {"gateway": "fixed digest"}})
        assert database.read_bytes() == before
        assert db.execute("SELECT value FROM executions").fetchall() == [("kept in WAL",)]
    with zipfile.ZipFile(output) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        assert {entry["path"] for entry in manifest["files"]} == {"data/history.db", "data/skill_assets/field.npy"}
        for entry in manifest["files"]:
            raw = archive.read(entry["path"])
            assert len(raw) == entry["size"]
            assert hashlib.sha256(raw).hexdigest() == entry["sha256"]
        restored = tmp_path / "verify.sqlite"
        restored.write_bytes(archive.read("data/history.db"))
        with sqlite3.connect(restored) as db:
            assert db.execute("PRAGMA quick_check").fetchone() == ("ok",)
            assert db.execute("SELECT value FROM executions").fetchall() == [("kept in WAL",)]
    assert list((tmp_path / backup.SNAPSHOTS).iterdir()) == []


def test_snapshot_rejects_links_without_reading_external_files(tmp_path):
    outside = tmp_path.parent / (tmp_path.name + "-outside")
    outside.write_text("not exported")
    try:
        try:
            (tmp_path / "link").symlink_to(outside)
        except OSError:
            pytest.skip("OS account cannot create symbolic links")
        with pytest.raises(ValueError, match="unsupported_data_file_type"):
            backup.export_snapshot(tmp_path, io.BytesIO(), {})
        assert outside.read_text() == "not exported"
    finally:
        outside.unlink()


def _restore_fixture(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    with sqlite3.connect(source / "skill_store.db") as db:
        db.execute("CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY)")
        db.executemany("INSERT INTO schema_migrations VALUES(?)", [(i,) for i in range(1, 18)])
    with sqlite3.connect(source / "execution_history.db") as db:
        db.execute("CREATE TABLE executions(value TEXT)")
        db.execute("INSERT INTO executions VALUES('original row')")
    (source / "field.npy").write_bytes(b"original asset")
    metadata = {"installation": "test-installation", "images": {"gateway": "fixed-gateway", "executor": "fixed-executor"},
                "settings": {"port": 18081, "extensions": False, "runtimeSha256": ""}}
    output = io.BytesIO()
    backup.export_snapshot(source, output, metadata)
    destination = tmp_path / "candidate"
    destination.mkdir()
    return source, destination, output.getvalue(), metadata


def _rewrite_archive(raw, mutation):
    with zipfile.ZipFile(io.BytesIO(raw)) as original:
        files = {name: original.read(name) for name in original.namelist()}
    mutation(files)
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return io.BytesIO(output.getvalue())


def test_restore_roundtrip_uses_new_directory_and_preserves_original(tmp_path):
    source, candidate, raw, metadata = _restore_fixture(tmp_path)
    originals = {p.name: p.read_bytes() for p in source.iterdir() if p.is_file()}
    result = backup.restore_snapshot(candidate, io.BytesIO(raw), metadata)
    assert result["restored"] and result["files"] == 3
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        for name, content in originals.items():
            assert (candidate / name).read_bytes() == archive.read("data/" + name)
            assert (source / name).read_bytes() == content
    with sqlite3.connect(candidate / "execution_history.db") as db:
        assert db.execute("SELECT value FROM executions").fetchall() == [("original row",)]
    assert not (candidate / backup.SNAPSHOTS).exists()


@pytest.mark.parametrize("kind", ["installation", "runtime", "digest", "traversal", "unlisted", "schema", "sqlite"])
def test_restore_rejects_bad_archives_before_any_active_data_changes(tmp_path, kind):
    source, candidate, raw, metadata = _restore_fixture(tmp_path)
    original = (source / "skill_store.db").read_bytes()

    def mutate(files):
        manifest = json.loads(files["manifest.json"])
        if kind == "installation":
            manifest["metadata"]["installation"] = "different installation"
        elif kind == "runtime":
            manifest["metadata"]["images"]["executor"] = "different runtime"
        elif kind == "digest":
            files["data/field.npy"] = b"tampered asset!"
        elif kind == "traversal":
            files["data/../../escaped"] = b"outside"
            manifest["files"].append({"path": "data/../../escaped", "size": 7, "sha256": hashlib.sha256(b"outside").hexdigest()})
        elif kind == "unlisted":
            files["data/unlisted"] = b"unlisted"
        elif kind == "sqlite":
            files["data/execution_history.db"] = b"SQLite format 3\x00invalid database"
            entry = next(f for f in manifest["files"] if f["path"] == "data/execution_history.db")
            entry.update(size=len(files[entry["path"]]), sha256=hashlib.sha256(files[entry["path"]]).hexdigest())
        else:
            entry = next(f for f in manifest["files"] if f["path"] == "data/skill_store.db")
            entry["schemaVersions"] = list(range(1, 20))
        files["manifest.json"] = json.dumps(manifest).encode()

    with pytest.raises((ValueError, KeyError, sqlite3.Error)):
        backup.restore_snapshot(candidate, _rewrite_archive(raw, mutate), metadata)
    assert (source / "skill_store.db").read_bytes() == original
    assert not (tmp_path / "escaped").exists()


def test_restore_refuses_nonempty_destination_without_replacing_anything(tmp_path):
    _, candidate, raw, metadata = _restore_fixture(tmp_path)
    (candidate / "keep").write_bytes(b"existing")
    with pytest.raises(ValueError, match="restore_requires_empty_volume"):
        backup.restore_snapshot(candidate, io.BytesIO(raw), metadata)
    assert (candidate / "keep").read_bytes() == b"existing"


def test_restore_rejects_duplicate_members(tmp_path):
    _, candidate, raw, metadata = _restore_fixture(tmp_path)
    output = io.BytesIO(raw)
    with zipfile.ZipFile(output, "a") as archive, pytest.warns(UserWarning, match="Duplicate name"):
        archive.writestr("data/field.npy", b"duplicate")
    output.seek(0)
    with pytest.raises(ValueError, match="invalid_backup_members"):
        backup.restore_snapshot(candidate, output, metadata)


def test_restore_rejects_zip_symlink_entries(tmp_path):
    _, candidate, raw, metadata = _restore_fixture(tmp_path)
    output = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(raw)) as original, zipfile.ZipFile(output, "w") as archive:
        for info in original.infolist():
            if info.filename == "data/field.npy":
                info.external_attr = (backup.stat.S_IFLNK | 0o777) << 16
            archive.writestr(info, original.read(info.filename))
    output.seek(0)
    with pytest.raises(ValueError, match="unsupported_backup_file_type"):
        backup.restore_snapshot(candidate, output, metadata)
