import json
import sqlite3
import uuid
from concurrent.futures import ThreadPoolExecutor
import pytest
from app import config, skill_library_service as service, skill_generation_decision as decision
from app.skill_store import SkillStore, SCHEMA_VERSION
from tests.unit.test_skill_store import bounded_candidate

PARAMS='{"frequency_hz":500000,"dx_m":0.00025,"dimensions":2,"medium_type":"water"}'
CONTEXT="original private context whitespace "
ARGS=dict(scenario_type="plane_wave_2d",parameters_json=PARAMS,original_context=CONTEXT)

@pytest.fixture
def store(tmp_path,monkeypatch):
    store=SkillStore(tmp_path/"skills.db")
    monkeypatch.setattr(service,"STORE",store)
    for name in ("SKILL_LIBRARY_ENABLED","SKILL_RETRIEVAL_ENABLED","SKILL_INJECTION_ENABLED"):
        monkeypatch.setattr(config,name,True)
    monkeypatch.setattr(config,"SKILL_LEARNING_ENABLED",False)
    return store

def consume(prepared,**changes):
    args=dict(ARGS,decision_id=prepared["decision_id"],nonce=prepared["nonce"],generation_run_id=str(uuid.uuid4()))
    args.update(changes)
    return decision.consume(**args)

@pytest.mark.parametrize("value",[False,1,"true",None])
@pytest.mark.parametrize("name",["SKILL_LIBRARY_ENABLED","SKILL_RETRIEVAL_ENABLED","SKILL_INJECTION_ENABLED"])
def test_strict_disabled_never_creates(store,monkeypatch,name,value):
    monkeypatch.setattr(config,name,value)
    assert not decision.prepare(**ARGS)["prepared"]
    assert not decision.consume(**dict(ARGS,decision_id="bad",nonce="bad",generation_run_id="bad"))["consumed"]
    assert not store.path.exists()

def test_one_use_empty_fallback_no_raw_context(store):
    prepared=decision.prepare(**ARGS)
    assert prepared["prepared"]
    result=consume(prepared)
    assert result["consumed"] and result["skills"]==[] and result["context"]==CONTEXT
    assert not any(result[k] for k in decision.FALSE_TRUST)
    assert not consume(prepared)["consumed"]
    with store._read_db() as db:
        row=dict(db.execute("SELECT * FROM skill_generation_decisions").fetchone())
        assert CONTEXT not in json.dumps(row) and PARAMS not in json.dumps(row)
        assert db.execute("SELECT count(*) FROM skill_generation_consumptions").fetchone()[0]==1

@pytest.mark.parametrize("changes",[
    {"nonce":"0"*32},{"parameters_json":"{}"},{"original_context":"changed"},
    {"scenario_type":"point_source_2d"},{"generation_run_id":"not-a-uuid"}])
def test_mismatch_does_not_burn_decision(store,changes):
    prepared=decision.prepare(**ARGS)
    assert not consume(prepared,**changes)["consumed"]
    assert consume(prepared)["consumed"]

@pytest.mark.parametrize("offset",[-1,30000000000])
def test_clock_and_expiry_fail_closed(store,monkeypatch,offset):
    monkeypatch.setattr(decision.time,"time_ns",lambda:100000000000)
    prepared=decision.prepare(**ARGS)
    monkeypatch.setattr(decision.time,"time_ns",lambda:100000000000+offset)
    assert not consume(prepared)["consumed"]

def test_missing_database_no_creation(store):
    result=decision.consume(**dict(ARGS,decision_id=str(uuid.uuid4()),nonce="1"*32,generation_run_id=str(uuid.uuid4())))
    assert not result["consumed"] and not store.path.exists()

def test_concurrent_consumption_once(store):
    prepared=decision.prepare(**ARGS)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda _:consume(prepared),range(2)))
    assert sum(r["consumed"] for r in results)==1

def test_insert_failure_rolls_back_and_can_retry(store):
    prepared=decision.prepare(**ARGS)
    with sqlite3.connect(store.path) as db:
        db.execute("CREATE TRIGGER fail_consumption BEFORE INSERT ON skill_generation_consumptions BEGIN SELECT RAISE(ABORT,'injected'); END")
    assert not consume(prepared)["consumed"]
    with sqlite3.connect(store.path) as db:
        assert db.execute("SELECT count(*) FROM skill_generation_consumptions").fetchone()[0]==0
        db.execute("DROP TRIGGER fail_consumption")
    assert consume(prepared)["consumed"]

def test_metadata_change_is_denied_fallback(store):
    skill=bounded_candidate(store)
    with sqlite3.connect(store.path) as db:
        db.execute("UPDATE simulation_skills SET status='active',quality_level='Q4' WHERE skill_id=?",(skill,))
    prepared=decision.prepare(**ARGS)
    with sqlite3.connect(store.path) as db:
        db.execute("UPDATE simulation_skills SET status='questioned' WHERE skill_id=?",(skill,))
    result=consume(prepared)
    assert result["consumed"] and result["reason"]=="decision_metadata_changed"
    assert result["skills"]==[] and not result["trusted_run"]

def test_append_only_including_unique_run_replace(store):
    first=decision.prepare(**ARGS); second=decision.prepare(**ARGS)
    run=str(uuid.uuid4())
    assert consume(first,generation_run_id=run)["consumed"]
    assert not consume(second,generation_run_id=run)["consumed"]
    with sqlite3.connect(store.path) as db:
        row=db.execute("SELECT * FROM skill_generation_consumptions").fetchone()
        for sql in ("DELETE FROM skill_generation_consumptions","UPDATE skill_generation_consumptions SET fallback_reason='x'",
                    "DELETE FROM skill_generation_decisions","UPDATE skill_generation_decisions SET nonce='x'"):
            with pytest.raises(sqlite3.IntegrityError):db.execute(sql)
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("INSERT OR REPLACE INTO skill_generation_consumptions VALUES (?,?,?,?,?)",
                       (second["decision_id"],run,*row[2:]))
        assert db.execute("SELECT decision_id FROM skill_generation_consumptions").fetchone()[0]==first["decision_id"]

def test_v9_upgrade_preserves_candidate_and_audit(store):
    skill=bounded_candidate(store)
    with sqlite3.connect(store.path) as db:
        count=db.execute("SELECT count(*) FROM skill_events").fetchone()[0]
        db.execute("DROP TABLE skill_local_reference_completions")
        db.execute("DROP TABLE skill_local_reference_dispatches")
        db.execute("DELETE FROM schema_migrations WHERE version=17")
        db.execute("DROP TABLE skill_local_sources")
        db.execute("DELETE FROM schema_migrations WHERE version=16")
        db.execute("DROP TABLE skill_reference_executions")
        db.execute("DROP TABLE skill_reference_dispatches")
        db.execute("DROP TABLE skill_reference_capsules")
        db.execute("DROP TABLE skill_host_verifications")
        db.execute("DROP TABLE skill_host_dispatches")
        db.execute("DROP TABLE skill_authority_roots")
        db.execute("DROP TABLE skill_generation_consumptions")
        db.execute("DROP TABLE skill_generation_decisions")
        for name in ("idx_skill_version_source","idx_skill_event_source","idx_skill_feedback_request","idx_skill_feedback_run","idx_skill_version_recent_source","idx_skill_event_recent_source","idx_candidate_source_version","idx_candidate_source_observation"):
            db.execute("DROP INDEX IF EXISTS "+name)
        db.execute("DROP INDEX idx_skill_event_history")
        db.execute("DROP INDEX idx_skill_revalidation_epoch")
        db.execute("DELETE FROM schema_migrations WHERE version>=10")
    store.migrate()
    with store._read_db() as db:
        assert db.execute("SELECT max(version) FROM schema_migrations").fetchone()[0]==SCHEMA_VERSION
        assert db.execute("SELECT count(*) FROM skill_events").fetchone()[0]==count
        assert db.execute("SELECT skill_id FROM simulation_skills").fetchone()[0]==skill

def test_failed_v9_migration_is_atomic_and_retryable(store):
    skill=bounded_candidate(store)
    with sqlite3.connect(store.path) as db:
        db.execute("DROP TABLE skill_local_reference_completions")
        db.execute("DROP TABLE skill_local_reference_dispatches")
        db.execute("DELETE FROM schema_migrations WHERE version=17")
        db.execute("DROP TABLE skill_local_sources")
        db.execute("DELETE FROM schema_migrations WHERE version=16")
        db.execute("DROP TABLE skill_reference_executions")
        db.execute("DROP TABLE skill_reference_dispatches")
        db.execute("DROP TABLE skill_reference_capsules")
        db.execute("DROP TABLE skill_host_verifications")
        db.execute("DROP TABLE skill_host_dispatches")
        db.execute("DROP TABLE skill_authority_roots")
        db.execute("DROP TABLE skill_generation_consumptions")
        db.execute("DROP TABLE skill_generation_decisions")
        for name in ("idx_skill_version_source","idx_skill_event_source","idx_skill_feedback_request","idx_skill_feedback_run","idx_skill_version_recent_source","idx_skill_event_recent_source","idx_candidate_source_version","idx_candidate_source_observation"):
            db.execute("DROP INDEX "+name)
        db.execute("DROP INDEX idx_skill_event_history")
        db.execute("DROP INDEX idx_skill_revalidation_epoch")
        db.execute("DELETE FROM schema_migrations WHERE version>=10")
        # Index-name collision must fail; it cannot silently accept a wrong index.
        db.execute("CREATE INDEX idx_skill_event_source ON skill_events(created_at)")
    with pytest.raises(sqlite3.OperationalError):store.migrate()
    with sqlite3.connect(store.path) as db:
        assert db.execute("SELECT max(version) FROM schema_migrations").fetchone()[0]==9
        assert not db.execute("SELECT 1 FROM sqlite_master WHERE name='skill_generation_decisions'").fetchone()
        assert db.execute("SELECT skill_id FROM simulation_skills").fetchone()[0]==skill
        db.execute("DROP INDEX idx_skill_event_source")
        # Historic malformed JSON does not break the safe partial index.
        db.execute("INSERT INTO skill_events(skill_id,event_type,detail,created_at) VALUES (?,?,?,?)",
                   (skill,"candidate_observed","not json","fixture"))
    store.migrate()
    with store._read_db() as db:
        assert db.execute("SELECT max(version) FROM schema_migrations").fetchone()[0]==SCHEMA_VERSION
