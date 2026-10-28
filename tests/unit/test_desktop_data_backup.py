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
