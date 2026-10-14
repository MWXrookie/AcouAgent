import sqlite3
import pytest
from app import config, controlled_validation, execution, skill_accumulation, skill_library_service
from app.skill_store import SkillStore
from tests.unit.test_controlled_validation import history, fake_execute


@pytest.fixture
def store(tmp_path, monkeypatch):
    value = SkillStore(tmp_path / "skills.db")
    monkeypatch.setattr(skill_library_service, "STORE", value)
    return value


def enable(monkeypatch):
    monkeypatch.setattr(config, "SKILL_LIBRARY_ENABLED", True)
    monkeypatch.setattr(config, "SKILL_LEARNING_ENABLED", True)


def test_default_disabled_never_reads_or_creates(store, monkeypatch):
    monkeypatch.setattr(config, "SKILL_LEARNING_ENABLED", False)
    monkeypatch.setattr(skill_accumulation.binding, "inspect_run",
                        lambda *args: pytest.fail("disabled must not read"))
    assert skill_accumulation.record_controlled_candidate("run")["reason"] == "candidate_accumulation_disabled"
    assert not store.path.exists()


def test_controlled_path_candidate_only_and_repeated_run_idempotent(history, store, monkeypatch):
    enable(monkeypatch)
    monkeypatch.setattr(execution, "_execute_code", fake_execute)
    result = controlled_validation.run_controlled_plane_wave()
    first = result["candidate_accumulation"]
    assert first["recorded"] and first["reason"] == "candidate_only"
    assert skill_accumulation.record_controlled_candidate(result["run_id"]) == first
    with sqlite3.connect(store.path) as db:
        assert db.execute("SELECT status,quality_level,success_count FROM simulation_skills").fetchone() == ("candidate","Q2",1)
        assert db.execute("SELECT count(*) FROM skill_events").fetchone()[0] == 1
        assert db.execute("SELECT count(*) FROM skill_versions").fetchone()[0] == 1
    assert all(first[k] is False for k in ("trusted_run","runtime_approved","run_bound","parameters_bound"))


def test_missing_or_mutated_history_never_creates_candidate(history, store, monkeypatch):
    enable(monkeypatch)
    assert not skill_accumulation.record_controlled_candidate("missing")["recorded"]
    monkeypatch.setattr(execution, "_execute_code", fake_execute)
    monkeypatch.setattr(config, "SKILL_LEARNING_ENABLED", False)
    result=controlled_validation.run_controlled_plane_wave()
    enable(monkeypatch)
    with sqlite3.connect(history) as db:
        db.execute("UPDATE executions SET stdout='{}'")
    assert skill_accumulation.record_controlled_candidate(result["run_id"])["reason"] == "candidate_prerequisites_failed"
    assert not store.path.exists()


def test_optional_storage_failure_does_not_repeat_executor(history, store, monkeypatch):
    enable(monkeypatch)
    calls=[]
    def execute(*args, **kwargs):
        calls.append(1)
        return fake_execute(*args, **kwargs)
    monkeypatch.setattr(execution, "_execute_code", execute)
    def fail(**kwargs):
        raise sqlite3.OperationalError("test audit failure")
    monkeypatch.setattr(store, "create_candidate", fail)
    result=controlled_validation.run_controlled_plane_wave()
    assert calls == [1]
    assert result["validation"]["numeric_pass"]
    assert result["candidate_accumulation"]["reason"] == "candidate_store_unavailable"

def test_distinct_runs_accumulate_but_retired_skill_stays_retired(history, store, monkeypatch):
    enable(monkeypatch)
    monkeypatch.setattr(execution, "_execute_code", fake_execute)
    first=controlled_validation.run_controlled_plane_wave()
    sid=first["candidate_accumulation"]["skill_id"]
    with sqlite3.connect(store.path) as db:
        db.execute("UPDATE simulation_skills SET status='retired' WHERE skill_id=?", (sid,))
    second=controlled_validation.run_controlled_plane_wave()
    assert second["run_id"] != first["run_id"]
    assert second["candidate_accumulation"]["skill_id"] == sid
    assert skill_accumulation.record_controlled_candidate(second["run_id"])["recorded"]
    with sqlite3.connect(store.path) as db:
        assert db.execute("SELECT status,quality_level,success_count FROM simulation_skills").fetchone() == ("retired","Q2",2)
        assert db.execute("SELECT count(*) FROM skill_events").fetchone()[0] == 2


def test_real_audit_failure_rolls_back_candidate_write(history, store, monkeypatch):
    enable(monkeypatch)
    store.migrate()
    with sqlite3.connect(store.path) as db:
        db.execute("""CREATE TRIGGER test_audit_failure BEFORE INSERT ON skill_events
                      BEGIN SELECT RAISE(ABORT,'test-only'); END""")
    monkeypatch.setattr(execution, "_execute_code", fake_execute)
    result=controlled_validation.run_controlled_plane_wave()
    assert result["candidate_accumulation"]["reason"] == "candidate_store_unavailable"
    with sqlite3.connect(store.path) as db:
        assert db.execute("SELECT count(*) FROM simulation_skills").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM skill_versions").fetchone()[0] == 0


@pytest.mark.parametrize("library,learning", [(False,True),(True,False),("true",True),(True,1)])
def test_both_flags_must_be_server_booleans(store, monkeypatch, library, learning):
    monkeypatch.setattr(config, "SKILL_LIBRARY_ENABLED", library)
    monkeypatch.setattr(config, "SKILL_LEARNING_ENABLED", learning)
    assert not skill_accumulation.record_controlled_candidate("run")["recorded"]
    assert not store.path.exists()


def test_catch_up_recovers_recorded_runs_without_execution(history,store,monkeypatch):
    calls=[]
    def execute(*args,**kwargs):
        calls.append(1)
        return fake_execute(*args,**kwargs)
    monkeypatch.setattr(execution,"_execute_code",execute)
    monkeypatch.setattr(config,"SKILL_LEARNING_ENABLED",False)
    controlled_validation.run_controlled_plane_wave()
    controlled_validation.run_controlled_plane_wave()
    assert calls==[1,1]
    enable(monkeypatch)
    monkeypatch.setattr(execution,"_execute_code",lambda *a,**k: pytest.fail("catch-up cannot execute"))
    result=skill_accumulation.accumulate_pending_controlled_candidates()
    assert (result["examined"],result["recorded"],result["rejected"])==(2,2,0)
    assert skill_accumulation.accumulate_pending_controlled_candidates()["recorded"]==2
    with sqlite3.connect(store.path) as db:
        assert db.execute("SELECT status,quality_level,success_count FROM simulation_skills").fetchone()==("candidate","Q2",2)
        assert db.execute("SELECT count(*) FROM skill_events").fetchone()[0]==2

def test_catch_up_disabled_and_missing_history_create_nothing(store,tmp_path,monkeypatch):
    monkeypatch.setattr(skill_accumulation.dashboard,"DB_PATH",tmp_path/"absent.db")
    monkeypatch.setattr(config,"SKILL_LEARNING_ENABLED",False)
    assert skill_accumulation.accumulate_pending_controlled_candidates()["examined"]==0
    enable(monkeypatch)
    assert skill_accumulation.accumulate_pending_controlled_candidates()["reason"]=="execution_store_unavailable"
    assert not (tmp_path/"absent.db").exists()
    assert not store.path.exists()

@pytest.mark.parametrize("limit",[0,51,True,"20"])
def test_catch_up_budget_rejects_invalid_limit(monkeypatch,limit):
    enable(monkeypatch)
    with pytest.raises(ValueError):
        skill_accumulation.accumulate_pending_controlled_candidates(limit)


def test_catch_up_cursor_drains_history_instead_of_repeating_first_page(history,store,monkeypatch):
    monkeypatch.setattr(config,"SKILL_LEARNING_ENABLED",False)
    monkeypatch.setattr(execution,"_execute_code",fake_execute)
    for _ in range(3):
        controlled_validation.run_controlled_plane_wave()
    enable(monkeypatch)
    cursor=0
    for _ in range(3):
        result=skill_accumulation.accumulate_pending_controlled_candidates(1,cursor)
        assert result["recorded"]==1
        assert result["next_after_execution_id"]>cursor
        cursor=result["next_after_execution_id"]
    assert skill_accumulation.accumulate_pending_controlled_candidates(1,cursor)["examined"]==0
    with sqlite3.connect(store.path) as db:
        assert db.execute("SELECT success_count FROM simulation_skills").fetchone()[0]==3


def test_catch_up_storage_failure_keeps_cursor_for_retry(history,store,monkeypatch):
    monkeypatch.setattr(config,"SKILL_LEARNING_ENABLED",False)
    monkeypatch.setattr(execution,"_execute_code",fake_execute)
    controlled_validation.run_controlled_plane_wave()
    enable(monkeypatch)
    original=store.create_candidate
    def fail(**kwargs):
        raise sqlite3.OperationalError("test-only storage interruption")
    monkeypatch.setattr(store,"create_candidate",fail)
    failed=skill_accumulation.accumulate_pending_controlled_candidates()
    assert failed["reason"]=="candidate_store_unavailable"
    assert failed["next_after_execution_id"]==0 and failed["recorded"]==0
    monkeypatch.setattr(store,"create_candidate",original)
    monkeypatch.setattr(execution,"_execute_code",lambda *a,**k:pytest.fail("retry cannot execute"))
    recovered=skill_accumulation.accumulate_pending_controlled_candidates(
        after_execution_id=failed["next_after_execution_id"])
    assert recovered["recorded"]==1 and recovered["next_after_execution_id"]>0
