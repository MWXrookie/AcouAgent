from app import config, dashboard
from app import skill_local_profile as local
from scripts.skill_library import local_basic


def test_bootstrap_runs_every_reviewed_profile_once(monkeypatch):
    calls=[]
    monkeypatch.setattr(config,"SKILL_BOOTSTRAP_ALL_FIXED",True)
    monkeypatch.setattr(config,"SKILL_LIBRARY_ENABLED",True)
    monkeypatch.setattr(config,"SKILL_RETRIEVAL_ENABLED",True)
    monkeypatch.setattr(config,"SKILL_MANUAL_INTAKE_ENABLED",True)
    monkeypatch.setattr(dashboard,"init_db",lambda: calls.append("history"))
    monkeypatch.setattr(local_basic,"initialize",lambda:{"ready":True,"created":True})
    monkeypatch.setattr(local.catalog,"SCOPES",("one","two","three"))
    monkeypatch.setattr(local_basic,"seed",lambda profile:(calls.append(profile) or {
        "ready":True,"executed":True,"reason":"accepted",
        "admission":{"skill_id":"skill-"+profile,"quality_level":"Q4"}}))
    result=local_basic.bootstrap()
    assert result["ready"] and result["profile_count"]==3 and result["executed_count"]==3
    assert calls==["history","one","two","three"]
    assert all(len(item["attempts"])==1 for item in result["profiles"])
    assert all(item["quality_level"]=="Q4" for item in result["profiles"])


def test_bootstrap_disabled_is_clean_noop(monkeypatch):
    monkeypatch.setattr(config,"SKILL_BOOTSTRAP_ALL_FIXED",False)
    monkeypatch.setattr(local_basic,"initialize",lambda:(_ for _ in ()).throw(AssertionError("must not initialize")))
    result=local_basic.bootstrap()
    assert result=={"ready":True,"skipped":True,"profile_count":0,
                    "reason":"fixed_profile_bootstrap_disabled"}


def test_bootstrap_stops_on_first_failed_profile(monkeypatch):
    monkeypatch.setattr(config,"SKILL_BOOTSTRAP_ALL_FIXED",True)
    monkeypatch.setattr(config,"SKILL_LIBRARY_ENABLED",True)
    monkeypatch.setattr(config,"SKILL_RETRIEVAL_ENABLED",True)
    monkeypatch.setattr(config,"SKILL_MANUAL_INTAKE_ENABLED",True)
    monkeypatch.setattr(dashboard,"init_db",lambda:None)
    monkeypatch.setattr(local_basic,"initialize",lambda:{"ready":True})
    monkeypatch.setattr(local.catalog,"SCOPES",("one","two","three"))
    calls=[]
    def fake_seed(profile):
        calls.append(profile)
        return {"ready":profile!="two","executed":True,"reason":"failed" if profile=="two" else "ok"}
    monkeypatch.setattr(local_basic,"seed",fake_seed)
    result=local_basic.bootstrap()
    assert not result["ready"] and result["failed_profile"]=="two"
    assert calls==["one","two","two","two"]
    assert len(result["profiles"][-1]["attempts"])==3


def test_bootstrap_retries_transient_executor_failure(monkeypatch):
    monkeypatch.setattr(config,"SKILL_BOOTSTRAP_ALL_FIXED",True)
    monkeypatch.setattr(config,"SKILL_LIBRARY_ENABLED",True)
    monkeypatch.setattr(config,"SKILL_RETRIEVAL_ENABLED",True)
    monkeypatch.setattr(config,"SKILL_MANUAL_INTAKE_ENABLED",True)
    monkeypatch.setattr(dashboard,"init_db",lambda:None)
    monkeypatch.setattr(local_basic,"initialize",lambda:{"ready":True})
    monkeypatch.setattr(local.catalog,"SCOPES",("one",))
    outcomes=iter((
        {"ready":False,"executed":True,"reason":"local_execution_failed"},
        {"ready":True,"executed":True,"reason":"local_fixed_profile_accepted",
         "admission":{"skill_id":"skill-one","quality_level":"Q4"}},
    ))
    monkeypatch.setattr(local_basic,"seed",lambda profile:next(outcomes))
    result=local_basic.bootstrap()
    assert result["ready"] and len(result["profiles"][0]["attempts"])==2


def test_status_all_exit_code_fails_when_catalog_is_incomplete(monkeypatch):
    monkeypatch.setattr(local_basic,"status_all",lambda:{"ready":False,"ready_count":16})
    monkeypatch.setattr("sys.argv",["local_basic","status-all"])
    assert local_basic.main()==1
