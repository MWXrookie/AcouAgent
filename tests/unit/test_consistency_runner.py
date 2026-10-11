import json
import pytest
from app import config,skill_consistency_source as source
from host_observer import consistency_runner as runner
from tests.unit.test_skill_consistency_source import prepared,records,NOW
from tests.unit.test_host_forwarder import endpoint,TOKEN


def real_case_responder(prepared,endpoint,monkeypatch,fault=None):
    store,key,batch,contexts,signed=prepared
    import itertools
    clock=itertools.count(NOW+5)
    monkeypatch.setattr(source.time,"time_ns",lambda:next(clock))
    target,server,calls=endpoint;forward=runner.forward_consistency_with_approved_key
    def observed(*args,case,run_id,**kwargs):
        # Assert the database dispatch exists BEFORE actual loopback HTTP.
        with store._read_db() as db:
            row=db.execute("SELECT context_json FROM skill_host_dispatches WHERE run_id=?",(run_id,)).fetchone()
            assert row and json.loads(row[0])["case"]==case
        stdout=signed[case]["stdout"];status=0
        if fault=="numeric" and case=="time":
            raw=json.loads(stdout);raw["pressure"]=[[0.0]*64 for _ in range(64)];stdout=json.dumps(raw)
        if fault=="execution" and case=="grid":status=1
        server.raw=json.dumps(dict(stdout=stdout,stderr="",exit_code=status,timed_out=False)).encode()
        return forward(*args,case=case,run_id=run_id,**kwargs)
    monkeypatch.setattr(runner,"forward_consistency_with_approved_key",observed)
    return store,key,target,calls


def test_actual_loopback_four_runs_recorded_before_http_and_only_q2(prepared,endpoint,monkeypatch):
    store,key,target,calls=real_case_responder(prepared,endpoint,monkeypatch)
    result=runner.observe_consistency_source(target,key=key,shared_token=TOKEN,revision=1)
    assert result["recorded"] and result["diagnostic_recorded"] and result["completed_cases"]==4
    assert result["stage"]=="complete" and result["failed_case"] is None
    assert len(calls)==4 and not result["admitted"] and result["quality_level"]=="Q2"
    assert all(result[k] is False for k in source.FALSE_TRUST)
    assert TOKEN not in json.dumps(result) and "stdout" not in result
    assert store.get_skill(result["skill_id"])["status"]=="candidate"
    assert len(set(result["runs"].values()))==4
    with store._read_db() as db:
        assert db.execute("SELECT COUNT(*) FROM skill_host_dispatches").fetchone()[0]==8
        assert db.execute("SELECT COUNT(*) FROM skill_host_verifications").fetchone()[0]==1


@pytest.mark.parametrize("fault",["execution","numeric"])
def test_failed_case_never_retries_or_drops_sample_to_pass(prepared,endpoint,monkeypatch,fault):
    store,key,target,calls=real_case_responder(prepared,endpoint,monkeypatch,fault)
    result=runner.observe_consistency_source(target,key=key,shared_token=TOKEN,revision=1)
    assert not result["recorded"] and not result["diagnostic_recorded"]
    assert result["stage"]==("execution" if fault=="execution" else "candidate")
    assert result["failed_case"]==("grid" if fault=="execution" else None)
    assert len(calls)==(2 if fault=="execution" else 4)
    with store._read_db() as db:
        assert db.execute("SELECT COUNT(*) FROM simulation_skills").fetchone()[0]==0
        assert db.execute("SELECT COUNT(*) FROM skill_host_verifications").fetchone()[0]==0
        assert db.execute("SELECT COUNT(*) FROM skill_host_dispatches").fetchone()[0]==4+len(calls)


@pytest.mark.parametrize("fault",["revision","storage","old_schema"])
def test_unapproved_or_unrecordable_case_never_sends_http(prepared,endpoint,monkeypatch,fault):
    store,key,target,calls=real_case_responder(prepared,endpoint,monkeypatch)
    if fault=="storage":
        with store._db_existing_write() as db:
            db.execute("CREATE TRIGGER no_dispatch BEFORE INSERT ON skill_host_dispatches BEGIN SELECT RAISE(ABORT,'test'); END")
    if fault=="old_schema":
        with store._db_existing_write() as db:
            db.execute("DROP INDEX idx_skill_dispatch_batch_case")
            db.execute("DROP TABLE skill_local_reference_completions")
            db.execute("DROP TABLE skill_local_reference_dispatches")
            db.execute("DELETE FROM schema_migrations WHERE version=17")
            db.execute("DROP TABLE skill_local_sources")
            db.execute("DELETE FROM schema_migrations WHERE version>=15")
    result=runner.observe_consistency_source(target,key=key,shared_token=TOKEN,revision=2 if fault=="revision" else 1)
    assert not result["recorded"] and calls==[]
    assert result["stage"]==("dispatch" if fault=="storage" else "authority")
    assert result["failed_case"]==("base" if fault=="storage" else None)
    if fault=="old_schema":
        with store._read_db() as db:
            assert db.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]==14


@pytest.mark.parametrize("disabled",[False,1,"true",None])
def test_nonboolean_enable_never_reads_database_or_network(prepared,endpoint,monkeypatch,disabled):
    store,key,target,calls=real_case_responder(prepared,endpoint,monkeypatch)
    monkeypatch.setattr(config,"SKILL_LIBRARY_ENABLED",disabled)
    monkeypatch.setattr(store,"_read_db",lambda:pytest.fail("disabled DB access"))
    result=runner.observe_consistency_source(target,key=key,shared_token=TOKEN,revision=1)
    assert result["reason"]=="consistency_runner_disabled" and calls==[]
    assert result["stage"]=="disabled" and result["failed_case"] is None
