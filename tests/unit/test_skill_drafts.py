"""Draft isolation, idempotency and racing edits against disposable databases."""
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from threading import Barrier

import pytest

from app import config
from app.skill_drafts import DraftStore, DraftError
from app.skill_store import SkillStore


def raw(name="original"):
    path = Path(__file__).resolve().parents[2] / "docs/skills-next/examples/manual-skill.json"
    spec = json.loads(path.read_text(encoding="utf-8"))
    spec["name"] = name
    return json.dumps(spec, ensure_ascii=False)


@pytest.fixture
def drafts(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SKILL_AUTHORING_ENABLED", True)
    store = SkillStore(tmp_path / "skills.db")
    store.migrate()
    store.migrate_skill_authoring()
    return DraftStore(store), store.path


def create(store, key="one", body=None, actor="admin-a"):
    return store.create(raw() if body is None else body, actor_id=actor, idempotency_key=key)


def test_disabled_feature_never_creates_a_database(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SKILL_AUTHORING_ENABLED", False)
    path = tmp_path / "never-created.db"
    store = DraftStore(SkillStore(path))
    with pytest.raises(DraftError):
        create(store)
    assert not path.exists()


def test_base17_is_not_silently_migrated_by_authoring(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SKILL_AUTHORING_ENABLED", True)
    underlying = SkillStore(tmp_path / "legacy.db")
    underlying.migrate()
    with pytest.raises(DraftError):
        create(DraftStore(underlying))
    with closing(sqlite3.connect(underlying.path)) as db:
        assert db.execute("SELECT max(version) FROM schema_migrations").fetchone()[0] == 17
        assert not db.execute("SELECT name FROM sqlite_master WHERE name='skill_drafts'").fetchall()


def test_repeat_create_is_idempotent_and_conflicting_content_rejected(drafts):
    store, _ = drafts
    first, repeat = create(store), create(store)
    assert repeat["draft_id"] == first["draft_id"]
    assert repeat["version"] == first["version"] == 1
    assert repeat["duplicate"] is True
    with pytest.raises(DraftError):
        create(store, body=raw("different content"))
    assert len(store.list_drafts(actor_id="admin-a")["items"]) == 1


def test_actor_namespace_prevents_cross_actor_read_and_idempotency_collision(drafts):
    store, _ = drafts
    first = create(store)
    second = create(store, actor="admin-b")
    assert first["draft_id"] != second["draft_id"]
    with pytest.raises(DraftError):
        store.get(first["draft_id"], actor_id="admin-b")


def test_update_has_cas_and_preserves_immutable_versions(drafts):
    store, _ = drafts
    first = create(store)
    unchanged = store.update(first["draft_id"], raw(), actor_id="admin-a", expected_revision=first["revision"])
    assert unchanged["unchanged"] is True
    assert unchanged["version"] == first["version"]
    assert unchanged["revision"] == first["revision"]
    edited = store.update(first["draft_id"], raw("edited"), actor_id="admin-a", expected_revision=first["revision"])
    assert edited["version"] == 2
    assert edited["revision"] > first["revision"]
    old = store.get(first["draft_id"], actor_id="admin-a", version=1)
    assert old["spec"]["name"] == "original"
    with pytest.raises(DraftError) as error:
        store.update(first["draft_id"], raw("stale"), actor_id="admin-a", expected_revision=first["revision"])
    assert error.value.code == "revision_conflict"
    replay = create(store)
    assert replay["version"] == 1
    assert replay["content_sha256"] == first["content_sha256"]


def test_parallel_duplicate_create_yields_one_business_resource(drafts):
    store, path = drafts
    barrier = Barrier(2)
    def submit(_):
        barrier.wait()
        return create(store)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(submit, range(2)))
    assert len({result["draft_id"] for result in results}) == 1
    with closing(sqlite3.connect(path)) as db:
        assert db.execute("SELECT count(*) FROM skill_drafts").fetchone()[0] == 1
        assert db.execute("SELECT count(*) FROM skill_draft_versions").fetchone()[0] == 1


def test_reintroducing_historical_content_cannot_silently_roll_back_head(drafts):
    store, _ = drafts
    first = create(store)
    second = store.update(first["draft_id"], raw("second"), actor_id="admin-a", expected_revision=first["revision"])
    with pytest.raises(DraftError) as error:
        store.update(first["draft_id"], raw(), actor_id="admin-a", expected_revision=second["revision"])
    assert error.value.code == "historical_content_conflict"
    head = store.get(first["draft_id"], actor_id="admin-a")
    assert head["version"] == second["version"]
    assert head["revision"] == second["revision"]
    assert head["spec"]["name"] == "second"


def test_parallel_updates_have_one_cas_winner(drafts):
    store, path = drafts
    first = create(store)
    barrier = Barrier(2)
    def update(name):
        barrier.wait()
        try:
            return store.update(first["draft_id"], raw(name), actor_id="admin-a", expected_revision=first["revision"])
        except DraftError as error:
            return error.code
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(update, ("edit-a", "edit-b")))
    assert sum(isinstance(result, dict) for result in results) == 1
    assert results.count("revision_conflict") == 1
    with closing(sqlite3.connect(path)) as db:
        assert db.execute("SELECT count(*) FROM skill_draft_versions").fetchone()[0] == 2


def test_drafts_never_enter_candidate_active_or_trusted_tables(drafts):
    store, path = drafts
    first = create(store)
    store.update(first["draft_id"], raw("edited"), actor_id="admin-a", expected_revision=first["revision"])
    assert store.get(first["draft_id"], actor_id="admin-a")["state"] == "editing"
    with closing(sqlite3.connect(path)) as db:
        assert db.execute("SELECT count(*) FROM simulation_skills").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM skill_versions").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM skill_events").fetchone()[0] == 0


def test_client_origin_and_quality_cannot_mint_trust(drafts):
    store, _ = drafts
    with pytest.raises(DraftError):
        store.create(raw(), actor_id="admin-a", idempotency_key="auto", origin="automatic")
    body = json.loads(raw())
    body["quality_level"] = "Q4"
    with pytest.raises(ValueError):
        create(store, key="fake", body=json.dumps(body))
