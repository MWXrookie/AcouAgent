"""Gateway pump lifecycle is bounded and never silently prepares storage."""
from pathlib import Path
import threading
import time
from types import SimpleNamespace

import pytest

from app import config, skill_auto_worker as worker_module
from app.skill_store import SkillStore


@pytest.fixture
def enabled(monkeypatch):
    for key in ("SKILL_LEARNING_ENABLED", "SKILL_AUTHORING_ENABLED", "SKILL_VALIDATION_ENABLED", "SKILL_GENERAL_ADMISSION_ENABLED"):
        monkeypatch.setattr(config, key, True)
    monkeypatch.setattr(worker_module, "_WORKER", None)


def test_disabled_start_never_reads_or_creates_storage(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SKILL_LEARNING_ENABLED", False)
    def forbidden():
        raise AssertionError("disabled worker must not resolve a library")
    monkeypatch.setattr(worker_module, "get_library", forbidden)
    assert worker_module.start_worker() is None
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("schema", ["missing", "legacy"])
def test_unready_start_never_migrates_or_installs_profiles(enabled, tmp_path, monkeypatch, schema):
    path = tmp_path / "skills.db"
    store = SkillStore(path)
    if schema == "legacy":
        store.migrate()
        before = path.read_bytes()
    monkeypatch.setattr(worker_module, "get_library", lambda: SimpleNamespace(store=store))
    assert worker_module.start_worker() is None
    if schema == "missing":
        assert not path.exists()
    else:
        assert path.read_bytes() == before


def test_invalid_library_error_is_sanitized(enabled, monkeypatch, caplog):
    def invalid():
        raise ValueError("private-key and local-path must never be logged")
    monkeypatch.setattr(worker_module, "get_library", invalid)
    assert worker_module.start_worker() is None
    assert "automatic_learning_worker_not_ready" in caplog.text
    assert "private-key" not in caplog.text and "local-path" not in caplog.text


def test_ready_start_is_singleton_and_does_not_write_database(enabled, tmp_path, monkeypatch):
    store = SkillStore(tmp_path / "skills.db"); store.migrate_skill_authoring()
    before = store.path.read_bytes()
    library = SimpleNamespace(store=store)
    monkeypatch.setattr(worker_module, "get_library", lambda: library)
    worker = worker_module.start_worker()
    try:
        assert worker is not None and worker.is_alive()
        assert worker_module.start_worker() is worker
        assert store.path.read_bytes() == before
    finally:
        worker.stop()


def test_tick_pump_stops_and_never_changes_configuration(enabled):
    ticked = threading.Event(); calls = []
    class Engine:
        def __init__(self, library):
            self.library = library
        def tick(self, owner):
            calls.append(owner); ticked.set()
    library = object()
    worker = worker_module.AutoLearningWorker(library, interval=.01, engine_factory=Engine)
    assert worker.start() is worker
    assert ticked.wait(1)
    assert worker.stop()
    count = len(calls)
    assert not worker.is_alive()
    assert len(calls) == count and all(owner == worker.owner for owner in calls)
    assert config.SKILL_LEARNING_ENABLED is True


def test_tick_errors_are_sanitized_and_next_tick_still_runs(enabled, caplog):
    continued = threading.Event(); calls = []
    class Engine:
        def __init__(self, library):
            pass
        def tick(self, owner):
            calls.append(owner)
            if len(calls) == 1:
                raise RuntimeError("sensitive-token SQL raw response")
            continued.set()
    worker = worker_module.AutoLearningWorker(object(), interval=.01, engine_factory=Engine).start()
    try:
        assert continued.wait(1)
    finally:
        worker.stop()
    assert "automatic_learning_tick_failed" in caplog.text
    assert "sensitive-token" not in caplog.text and "SQL raw response" not in caplog.text


def test_shutdown_is_bounded_and_does_not_kill_an_inflight_task(enabled):
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    class Engine:
        def __init__(self, library):
            pass
        def tick(self, owner):
            entered.set(); release.wait(5); finished.set()
    worker = worker_module.AutoLearningWorker(object(), interval=.01, engine_factory=Engine).start()
    assert entered.wait(1)
    started = time.monotonic()
    try:
        assert worker.stop() is False
        assert time.monotonic() - started < 1.8
        assert not finished.is_set()
    finally:
        release.set()
        worker.stop()
    assert finished.is_set()


def test_disabled_during_lifespan_stops_scheduling_new_ticks(enabled, monkeypatch):
    calls = []; first = threading.Event()
    class Engine:
        def __init__(self, library):
            pass
        def tick(self, owner):
            calls.append(owner)
            monkeypatch.setattr(config, "SKILL_LEARNING_ENABLED", False)
            first.set()
    worker = worker_module.AutoLearningWorker(object(), interval=.01, engine_factory=Engine).start()
    try:
        assert first.wait(1)
        time.sleep(.04)
        assert len(calls) == 1
    finally:
        worker.stop()
