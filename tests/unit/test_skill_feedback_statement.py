import sqlite3
import pytest
from app.skill_store import SkillStore
from app import config, skill_library_service as service


@pytest.fixture
def prepared(tmp_path):
    store=SkillStore(tmp_path/"skills.db")
    sid=store.create_candidate(scenario_type="initial_pressure_2d", state_text="test",
        normalized_params={"dx_m":0.001},code_template="pass",postconditions={},
        physics_evidence={},source_run_id="run",validator_version="v",quality_level="Q2")
    return store,sid


def write(store,sid,confirmed=True,request="a",run="run"):
    return store.record_feedback_statement(sid,run,confirmed=confirmed,
        request_id=request,actor="portal-admin",reason="test")


def test_confirmation_retry_withdrawal_and_reconfirm_keep_trust_closed(prepared):
    store,sid=prepared
    first=write(store,sid)
    assert write(store,sid)["duplicate"]
    with sqlite3.connect(store.path) as db:
        db.execute("UPDATE simulation_skills SET status='active' WHERE skill_id=?",(sid,))
    withdrawn=write(store,sid,False,"b")
    assert write(store,sid,False,"b")["event_id"] == withdrawn["event_id"]
    write(store,sid,True,"c")
    with sqlite3.connect(store.path) as db:
        assert db.execute("SELECT status,quality_level,success_count,failure_count FROM simulation_skills").fetchone() == ("questioned","Q2",1,0)
        assert db.execute("SELECT count(*) FROM skill_events WHERE event_type='feedback_statement'").fetchone()[0] == 3
    assert first["trusted_run"] is False and first["identity_authenticated"] is False


def test_conflicting_retry_unknown_run_and_withdraw_without_confirmation(prepared):
    store,sid=prepared
    with pytest.raises(ValueError,match="no_current"): write(store,sid,False)
    with pytest.raises(ValueError,match="not_associated"): write(store,sid,run="other")
    write(store,sid)
    with pytest.raises(ValueError,match="conflict"): write(store,sid,False)
    with pytest.raises(ValueError): write(store,sid,confirmed=1)


def test_audit_failure_does_not_question_active_skill(prepared):
    store,sid=prepared
    write(store,sid)
    with sqlite3.connect(store.path) as db:
        db.execute("UPDATE simulation_skills SET status='active'")
        db.execute("""CREATE TRIGGER test_failure BEFORE INSERT ON skill_events
            BEGIN SELECT RAISE(ABORT,'test'); END""")
    with pytest.raises(sqlite3.IntegrityError): write(store,sid,False,"b")
    with sqlite3.connect(store.path) as db:
        assert db.execute("SELECT status FROM simulation_skills").fetchone()[0] == "active"


def test_service_requires_existing_admin_authorization(prepared,monkeypatch):
    store,sid=prepared
    monkeypatch.setattr(service,"STORE",store)
    monkeypatch.setattr(config,"PORTAL_ADMIN_TOKEN","x"*32)
    with pytest.raises(PermissionError):
        service.feedback_statement("bad",sid,run_id="run",confirmed=True,request_id="a",reason="test")
    assert service.feedback_statement("x"*32,sid,run_id="run",confirmed=True,request_id="a",reason="test")["recorded"]

def test_feedback_keeps_historical_version_and_evidence(prepared):
    import json, hashlib
    store,sid=prepared
    store.create_candidate(scenario_type="initial_pressure_2d",state_text="test",
        normalized_params={"dx_m":.001},code_template="x=1",postconditions={},
        physics_evidence={"field":"new"},source_run_id="new-run",validator_version="v2",quality_level="Q2")
    result=write(store,sid,run="run",request="historical")
    assert result["recorded"] and write(store,sid,run="run",request="historical")["duplicate"]
    store.record_negative_feedback(sid,"new-run",actor="portal-admin",reason="wrong")
    with sqlite3.connect(store.path) as db:
        details={kind:json.loads(detail) for kind,detail in db.execute(
            "SELECT event_type,detail FROM skill_events WHERE event_type IN ('feedback_statement','negative_feedback')")}
    old=details["feedback_statement"]["source_association"]
    new=details["negative_feedback"]["source_association"]
    assert old["version"]==1 and old["code_sha256"]==hashlib.sha256(b"pass").hexdigest()
    assert new["version"]==2 and new["code_sha256"]==hashlib.sha256(b"x=1").hexdigest()
    assert not old["actual_use_authenticated"] and not new["actual_use_authenticated"]

def test_ambiguous_same_run_versions_cannot_receive_new_feedback(prepared):
    store,sid=prepared
    store.create_candidate(scenario_type="initial_pressure_2d",state_text="test",
        normalized_params={"dx_m":.001},code_template="x=1",postconditions={},
        physics_evidence={},source_run_id="run",validator_version="v",quality_level="Q2")
    before=store.get_skill(sid)
    with pytest.raises(ValueError,match="run_association_ambiguous"):
        write(store,sid)
    with pytest.raises(ValueError,match="run_association_ambiguous"):
        store.record_negative_feedback(sid,"run",actor="portal-admin",reason="wrong")
    assert store.get_skill(sid)==before

def test_observation_feedback_remains_bound_after_new_version(prepared):
    import json
    store,sid=prepared
    def add(run,code,evidence):
        store.create_candidate(scenario_type="initial_pressure_2d",state_text="test",
            normalized_params={"dx_m":.001},code_template=code,postconditions={},
            physics_evidence=evidence,source_run_id=run,validator_version="v",quality_level="Q2")
    add("observed","pass",{"observation":1})
    add("latest","x=1",{"observation":2})
    assert write(store,sid,run="observed")["recorded"]
    with sqlite3.connect(store.path) as db:
        payload=json.loads(db.execute("SELECT detail FROM skill_events WHERE event_type='feedback_statement'").fetchone()[0])
    assert payload["source_association"]["version"]==1
    assert payload["source_association"]["actual_use_authenticated"] is False

def test_legacy_observation_never_inherits_latest_version(prepared):
    import json
    store,sid=prepared
    with sqlite3.connect(store.path) as db:
        db.execute("INSERT INTO skill_events(skill_id,event_type,detail,created_at) VALUES (?,?,?,?)",
            (sid,"candidate_observed",json.dumps({"source_run_id":"legacy"}),"2026-01-01"))
    assert write(store,sid,run="legacy")["recorded"]
    with sqlite3.connect(store.path) as db:
        payload=json.loads(db.execute("SELECT detail FROM skill_events WHERE event_type='feedback_statement'").fetchone()[0])
    assert payload["source_association"]=={"kind":"legacy_unbound_source_observation","version":None,
                                         "actual_use_authenticated":False}

def test_excessive_observation_history_rejects_without_audit_or_state_change(prepared):
    import json
    store,sid=prepared
    with sqlite3.connect(store.path) as db:
        db.executemany("INSERT INTO skill_events(skill_id,event_type,detail,created_at) VALUES (?,?,?,?)",
            [(sid,"candidate_observed",json.dumps({"source_run_id":"legacy"}),"2026-01-01")]*101)
    before=store.get_skill(sid)
    with pytest.raises(ValueError,match="run_association_budget"):
        write(store,sid,run="legacy")
    assert store.get_skill(sid)==before
    with sqlite3.connect(store.path) as db:
        assert db.execute("SELECT count(*) FROM skill_events WHERE event_type='feedback_statement'").fetchone()[0]==0

def test_feedback_association_large_history_uses_index_with_unchanged_budget(prepared):
    store,sid=prepared
    with sqlite3.connect(store.path) as db:
        db.executemany("INSERT INTO skill_events(skill_id,event_type,detail,created_at) VALUES (?,?,?,?)",
            [(sid,"candidate_observed",'{"source_run_id":"unrelated"}',"2026-01-01")]*100000)
    before=store.get_skill(sid)
    with pytest.raises(ValueError,match="run_not_associated"):
        write(store,sid,run="missing")
    assert write(store,sid)["recorded"]
    with sqlite3.connect(store.path) as db:
        plan=db.execute("""EXPLAIN QUERY PLAN SELECT detail FROM skill_events
            WHERE skill_id=? AND event_type='candidate_observed' AND json_valid(detail)
            AND json_extract(CASE WHEN json_valid(detail) THEN detail ELSE '{}' END,'$.source_run_id')=?
            LIMIT 101""",(sid,"run")).fetchall()
        assert any("idx_skill_event_source" in row[3] for row in plan)
        assert not any("SCAN skill_events" in row[3] for row in plan)
    assert store.get_skill(sid)==before
    with sqlite3.connect(store.path) as db:
        assert db.execute("SELECT count(*) FROM skill_events WHERE event_type='feedback_statement'").fetchone()[0]==1

def test_recent_policy_large_history_uses_ordered_prefix(prepared):
    import json
    store,sid=prepared
    with store._db_existing_write() as db:
        db.executemany("INSERT INTO skill_events(skill_id,event_type,detail,created_at) VALUES (?,?,?,?)",
            [(sid,"candidate_observed",json.dumps({"source_run_id":"old-"+str(i)}),"2000-01-01")
             for i in range(10000)])
        for run,stamp in (("recent-a","2026-01-01"),("recent-b","2026-01-02"),("recent-c","2026-01-03")):
            db.execute("INSERT INTO skill_events(skill_id,event_type,detail,created_at) VALUES (?,?,?,?)",
                       (sid,"candidate_observed",json.dumps({"source_run_id":run}),stamp))
        # The original version uses actual current UTC, so it is newer.
        assert store._recent_source_runs_connection(db,sid)=={"run","recent-b","recent-c"}
        plan=db.execute("EXPLAIN QUERY PLAN SELECT detail,created_at FROM skill_events WHERE skill_id=? AND event_type='candidate_observed' ORDER BY created_at DESC,id DESC LIMIT 101",(sid,)).fetchall()
        assert any("idx_skill_event_recent_source" in row[3] for row in plan)
        assert not any("TEMP B-TREE" in row[3] for row in plan)

def test_recent_policy_ambiguous_truncated_history_refuses_feedback_atomically(prepared):
    import json
    store,sid=prepared
    with store._db_existing_write() as db:
        for i in range(103):
            db.execute("INSERT INTO skill_events(skill_id,event_type,detail,created_at) VALUES (?,?,?,?)",
                       (sid,"candidate_observed",json.dumps({"source_run_id":"other-"+str(i)}),"9999-01-01"))
    before=store.get_skill(sid)
    with pytest.raises(ValueError,match="recent_run_budget"):
        store.record_negative_feedback(sid,"run",actor="portal-admin",reason="failure")
    assert store.get_skill(sid)==before
    with store._read_db() as db:
        assert db.execute("SELECT count(*) FROM skill_feedback").fetchone()[0]==0
