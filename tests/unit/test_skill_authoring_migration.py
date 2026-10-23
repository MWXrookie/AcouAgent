from contextlib import closing
import sqlite3
from unittest.mock import patch

import pytest

from app.skill_authoring_schema import schema_ready
from app.skill_store import SkillStore
from app.skill_consistency_source import _require_current_schema
from scripts.skill_library.authoring import migrate_with_backup


def test_default_stays17_explicit18_keeps_legacy_and_is_idempotent(tmp_path):
    path = tmp_path / "skills.db"
    store = SkillStore(path)
    store.migrate()
    with closing(sqlite3.connect(path)) as db:
        original = dict(db.execute("SELECT name,sql FROM sqlite_master WHERE type='table'"))
        assert not schema_ready(db)
        assert db.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] == 17
    store.migrate_skill_authoring()
    store.migrate_skill_authoring()
    store.migrate()
    with closing(sqlite3.connect(path)) as db:
        assert schema_ready(db)
        _require_current_schema(db)
        for name, sql in original.items():
            assert db.execute("SELECT sql FROM sqlite_master WHERE name=?", (name,)).fetchone()[0] == sql
        assert not list(db.execute("PRAGMA foreign_key_check"))


def test_failed_extension_rolls_back_without_marker_or_partial_table(tmp_path):
    store = SkillStore(tmp_path / "skills.db")
    store.migrate()

    def fail_install(db):
        db.execute("CREATE TABLE skill_drafts(fixture INTEGER)")
        raise RuntimeError("injected_migration_failure")

    with patch("app.skill_authoring_schema.install", fail_install), pytest.raises(RuntimeError):
        store.migrate_skill_authoring()
    with closing(sqlite3.connect(store.path)) as db:
        assert db.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] == 17
        assert db.execute("SELECT 1 FROM sqlite_master WHERE name='skill_drafts'").fetchone() is None
    store.migrate_skill_authoring()


def test_operator_backup_is_valid_pre18_and_repeated_call_is_readonly(tmp_path):
    store = SkillStore(tmp_path / "skills.db")
    store.migrate()
    result = migrate_with_backup(store.path, tmp_path / "backups")
    assert result["changed"] is True
    with closing(sqlite3.connect(result["backup"])) as backup:
        assert backup.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        assert backup.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] == 17
    assert migrate_with_backup(store.path, tmp_path / "backups") == {"schema": 18, "changed": False}


def test_missing_source_does_not_create_a_database(tmp_path):
    missing = tmp_path / "missing.db"
    with pytest.raises(ValueError):
        migrate_with_backup(missing, tmp_path / "backups")
    assert not missing.exists()


def test_noop_replacement_trigger_is_not_a_complete_schema(tmp_path):
    store = SkillStore(tmp_path / "skills.db")
    store.migrate_skill_authoring()
    with closing(sqlite3.connect(store.path)) as db:
        db.execute("DROP TRIGGER skill_draft_versions_no_update")
        db.execute("CREATE TRIGGER skill_draft_versions_no_update BEFORE UPDATE ON skill_draft_versions BEGIN SELECT 1; END")
        db.commit()
        assert not schema_ready(db)
    with pytest.raises(ValueError):
        store.migrate()


def test_replace_cannot_delete_an_old_version_via_content_unique(tmp_path, monkeypatch):
    import json
    from pathlib import Path
    from app import config
    from app.skill_drafts import DraftStore
    monkeypatch.setattr(config, "SKILL_AUTHORING_ENABLED", True)
    store = SkillStore(tmp_path / "skills.db")
    store.migrate_skill_authoring()
    raw = (Path(__file__).resolve().parents[2] / "docs/skills-next/examples/manual-skill.json").read_text(encoding="utf-8")
    drafts = DraftStore(store)
    draft = drafts.create(raw, actor_id="test", idempotency_key="one")
    changed = json.loads(raw)
    changed["name"] = "second"
    drafts.update(draft["draft_id"], json.dumps(changed), actor_id="test", expected_revision=1)
    with closing(sqlite3.connect(store.path)) as db:
        db.execute("PRAGMA foreign_keys=ON")
        row = db.execute("SELECT * FROM skill_draft_versions WHERE draft_id=? AND version=2", (draft["draft_id"],)).fetchone()
        replacement = list(row)
        replacement[1] = 3
        placeholders = ",".join("?" for _ in replacement)
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("INSERT OR REPLACE INTO skill_draft_versions VALUES("+placeholders+")", replacement)
        assert db.execute("SELECT COUNT(*) FROM skill_draft_versions WHERE draft_id=?", (draft["draft_id"],)).fetchone()[0] == 2


def test_backup_destination_failure_never_migrates(tmp_path):
    store = SkillStore(tmp_path / "skills.db")
    store.migrate()
    blocked = tmp_path / "not-a-directory"
    blocked.write_text("occupied")
    with pytest.raises(OSError), patch.object(SkillStore, "migrate_skill_authoring", side_effect=AssertionError("must not migrate")):
        migrate_with_backup(store.path, blocked)
    with closing(sqlite3.connect(store.path)) as db:
        assert db.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] == 17
        assert db.execute("SELECT 1 FROM sqlite_master WHERE name='skill_drafts'").fetchone() is None
