import json
import sqlite3
import time
import pytest
from app import config,skill_deadline,skill_library_service,skill_retrieval
from app.skill_store import SkillStore
from tests.unit.test_skill_generation_tool import registered
def test_existing_write_rolls_back_if_shared_budget_expires(tmp_path):
    store=SkillStore(tmp_path/"skills.db");store.migrate()
    with pytest.raises(TimeoutError):
        with skill_deadline.limited(0.02):
            with store._db_existing_write() as db:
                db.execute("INSERT INTO skill_retrievals(run_id,query_text,candidates_json,latency_ms,created_at) VALUES ('run','{}','[]',0,'now')")
                time.sleep(0.03)
    with store._read_db() as db:
        assert db.execute("SELECT count(*) FROM skill_retrievals").fetchone()[0]==0
    assert not skill_deadline.expired()
def test_writer_lock_does_not_consume_unbounded_generation_wait(tmp_path,monkeypatch):
    store=SkillStore(tmp_path/"skills.db");store.migrate()
    monkeypatch.setattr(skill_library_service,"STORE",store)
    for flag in ("SKILL_LIBRARY_ENABLED","SKILL_RETRIEVAL_ENABLED","SKILL_INJECTION_ENABLED"):
        monkeypatch.setattr(config,flag,True)
    lock=sqlite3.connect(store.path);lock.execute("BEGIN IMMEDIATE")
    try:
        start=time.monotonic()
        result=registered()["build_simulation_generation_context"]("original","plane_wave_2d",'{"dimensions":2}')
        elapsed=time.monotonic()-start
        assert result["context"]=="original" and result["skill_usage"]["fallback"]
        assert elapsed<1.0
    finally:lock.rollback();lock.close()
    with store._read_db() as db:
        assert db.execute("SELECT count(*) FROM skill_generation_decisions").fetchone()[0]==0
def test_public_builder_does_not_create_or_migrate_missing_store(tmp_path,monkeypatch):
    store=SkillStore(tmp_path/"absent"/"skills.db")
    monkeypatch.setattr(skill_library_service,"STORE",store)
    for flag in ("SKILL_LIBRARY_ENABLED","SKILL_RETRIEVAL_ENABLED","SKILL_INJECTION_ENABLED"):
        monkeypatch.setattr(config,flag,True)
    result=registered()["build_simulation_generation_context"]("original","plane_wave_2d",'{"dimensions":2}')
    assert result["context"]=="original" and result["skill_usage"]["fallback"]
    assert not store.path.exists() and not store.path.parent.exists()
def test_expired_tool_call_cannot_handoff_even_if_inner_function_returns(monkeypatch):
    original=time.monotonic
    tick=[original()]
    monkeypatch.setattr(skill_deadline.time,"monotonic",lambda:tick[0])
    def delayed(context,**kwargs):
        tick[0]+=3
        return {"context":context+" unsafe delayed code"}
    monkeypatch.setattr(skill_retrieval,"generation_context",delayed)
    result=registered()["build_simulation_generation_context"]("original","plane_wave_2d","{}")
    assert result["context"]=="original" and result["skill_usage"]["reason"]=="generation_context_deadline"
    assert not skill_deadline.expired()
