import copy
import hashlib
import json
import uuid
import pytest
from app import skill_host_intake as intake,skill_fixed_activation as activation,skill_authority as authority
from tests.unit.test_skill_host_intake import ready
from tests.unit.test_skill_authority import prepared,context,approval
from scripts.validation.host_forward_signature import canonical,DOMAIN

def questioned(ready):
    store,key,ctx,obs,args,doc=ready
    assert intake.record(**args)["recorded"] and activation.admit(**args)["admitted"]
    sid=args["skill_id"]
    store.set_archived(sid,archived=True,expected_updated_at=store.get_skill(sid)["updated_at"],actor="portal-admin",reason="review")
    store.set_archived(sid,archived=False,expected_updated_at=store.get_skill(sid)["updated_at"],actor="portal-admin",reason="review restored")
    with store._read_db() as db:latest=db.execute("SELECT max(id) FROM skill_events WHERE skill_id=?",(sid,)).fetchone()[0]
    return dict(expected_updated_at=store.get_skill(sid)["updated_at"],expected_event_id=latest,actor="portal-admin",reason="new independent evidence required")

def test_revalidation_cannot_replay_prior_source_but_allows_new_independent_run(ready,monkeypatch):
    store,key,ctx,obs,args,doc=ready
    request=questioned(ready)
    result=store.request_revalidation(args["skill_id"],**request)
    assert result["status"]=="candidate" and result["quality_level"]=="Q2" and not result["trusted_run"]
    assert not activation.admit(**args)["admitted"]
    with pytest.raises(ValueError):store.request_revalidation(args["skill_id"],**request)
    monkeypatch.setattr(authority.time,"time_ns",lambda:100_600_000_000)
    new=context();new.update(run_id=str(uuid.uuid4()),nonce=uuid.uuid4().hex)
    assert authority.record_dispatch(new,approval(key,new))["recorded"]
    code=(intake.pins.DIRECTORY/"recipes/val1_plane_wave_v2.py").read_text().strip()
    sid=store.create_candidate(scenario_type="plane_wave_2d",state_text="fresh revalidation fixture",
        normalized_params=intake.controlled_dispatch.fixed_retrieval_parameters(),code_template=code,postconditions={},
        preconditions=json.loads(store.get_skill(args["skill_id"])["preconditions"]),physics_evidence={},
        source_run_id=new["run_id"],validator_version="fixture",quality_level="Q2")
    assert sid==args["skill_id"]
    obs.update(run_id=new["run_id"],nonce=new["nonce"],observed_before_ns=100_700_000_000,
        request_start_ns=100_800_000_000,request_end_ns=101_000_000_000,observed_after_ns=101_100_000_000,
        request_sha256=hashlib.sha256(canonical(dict(code=code,timeout_seconds=30,request_nonce=new["nonce"]))).hexdigest())
    args["observation_document"]=doc()
    monkeypatch.setattr(authority.time,"time_ns",lambda:101_200_000_000)
    assert intake.record(**args)["recorded"]
    assert activation.admit(**args)["admitted"]
    assert store.get_skill(sid)["status"]=="active"

@pytest.mark.parametrize("fault",["revision","event","archive","commit"])
def test_stale_or_failed_reset_keeps_questioned_state(ready,fault):
    store,key,ctx,obs,args,doc=ready
    request=questioned(ready)
    if fault=="revision":request["expected_updated_at"]="stale"
    elif fault=="event":request["expected_event_id"]-=1
    elif fault=="archive":
        store.set_archived(args["skill_id"],archived=True,expected_updated_at=request["expected_updated_at"],actor="portal-admin",reason="archive")
    else:
        with store._db_existing_write() as db:
            db.execute("CREATE TRIGGER fault BEFORE UPDATE ON simulation_skills BEGIN SELECT RAISE(ABORT,'fault'); END")
    with pytest.raises(Exception):store.request_revalidation(args["skill_id"],**request)
    assert store.get_skill(args["skill_id"])["status"]=="questioned"
    with store._read_db() as db:
        assert db.execute("SELECT count(*) FROM skill_events WHERE event_type='revalidation_requested'").fetchone()[0]==0


def test_missing_database_reset_never_creates_it(tmp_path):
    from app.skill_store import SkillStore
    import sqlite3
    store=SkillStore(tmp_path/"missing.db")
    with pytest.raises(sqlite3.OperationalError):
        store.request_revalidation("unknown",expected_updated_at="old",expected_event_id=1,
            actor="portal-admin",reason="review")
    assert not store.path.exists()


def test_epoch_and_revision_queries_use_schema14_indexes(ready):
    store,*_=ready
    with store._read_db() as db:
        epoch=db.execute("EXPLAIN QUERY PLAN SELECT id FROM skill_events WHERE skill_id=? AND event_type='revalidation_requested' ORDER BY id DESC LIMIT 1",("missing",)).fetchall()
        revision=db.execute("EXPLAIN QUERY PLAN SELECT max(id) FROM skill_events WHERE skill_id=?",("missing",)).fetchall()
        assert "idx_skill_revalidation_epoch" in str([tuple(r) for r in epoch])
        assert "idx_skill_event_history" in str([tuple(r) for r in revision])
