import json
import sqlite3
import pytest
from app import config, skill_retrieval as retrieval, skill_library_service as service
from app.skill_store import SkillStore
from tests.unit.test_skill_store import bounded_candidate

PARAMS='{"frequency_hz":500000,"dx_m":0.00025,"dimensions":2,"medium_type":"water"}'


@pytest.fixture
def store(tmp_path,monkeypatch):
    store=SkillStore(tmp_path/"skills.db")
    monkeypatch.setattr(service,"STORE",store)
    monkeypatch.setattr(config,"SKILL_LIBRARY_ENABLED",True)
    monkeypatch.setattr(config,"SKILL_RETRIEVAL_ENABLED",False)
    monkeypatch.setattr(config,"SKILL_INJECTION_ENABLED",False)
    return store


def test_disabled_search_and_adapter_never_touch_database(store):
    result=retrieval.search("plane_wave_2d",PARAMS)
    assert result["skills"]==[] and result["reason"]=="retrieval_disabled"
    original="original generation context\nwith exact whitespace "
    result=retrieval.generation_context(original,scenario_type="plane_wave_2d",parameters_json=PARAMS)
    assert result["context"]==original and result["skill_usage"]["fallback"]
    assert not store.path.exists()


@pytest.mark.parametrize("scenario,params,limit",[
    ("unknown",PARAMS,3),("plane_wave_2d",PARAMS,0),("plane_wave_2d",PARAMS,4),
    ("plane_wave_2d",PARAMS,True),("plane_wave_2d",'{"trusted_run":true}',3),
    ("plane_wave_2d",'{"frequency_hz":1,"frequency_hz":2}',3),
    ("plane_wave_2d",'{"frequency_hz":NaN}',3),("plane_wave_2d","x"*4097,3),
    ("plane_wave_2d","["*1500,3)])
def test_invalid_public_request_does_not_create_database(store,scenario,params,limit):
    with pytest.raises(ValueError):
        retrieval.search(scenario,params,limit)
    assert not store.path.exists()


def test_synthetic_active_label_never_authorizes_code_or_changes_db(store,monkeypatch):
    skill=bounded_candidate(store)
    with sqlite3.connect(store.path) as db:
        db.execute("UPDATE simulation_skills SET status='active',quality_level='Q4' WHERE skill_id=?",(skill,))
        scenario=db.execute("SELECT scenario_type FROM simulation_skills").fetchone()[0]
    monkeypatch.setattr(config,"SKILL_RETRIEVAL_ENABLED",True)
    monkeypatch.setattr(config,"SKILL_INJECTION_ENABLED",True)
    before=store.path.read_bytes()
    result=retrieval.search(scenario,PARAMS)
    assert result["skills"]==[] and result["fallback"]
    assert result["reason"]=="provenance_authentication_unavailable"
    assert not any(result[k] for k in ("trusted_run","runtime_approved","parameters_bound","run_bound"))
    # Public search is read-only; internal generation adds only a fallback intent.
    assert store.path.read_bytes()==before
    context=retrieval.generation_context("unchanged",scenario_type=scenario,parameters_json=PARAMS)
    assert context["context"]=="unchanged" and context["skill_usage"]["audit_recorded"]
    with store._read_db() as db:
        assert db.execute("SELECT count(*) FROM skill_generation_consumptions").fetchone()[0]==1
    assert context["skill_usage"]["skills"]==[]


def test_missing_database_and_bad_injection_request_fall_back(store,monkeypatch):
    monkeypatch.setattr(config,"SKILL_RETRIEVAL_ENABLED",True)
    monkeypatch.setattr(config,"SKILL_INJECTION_ENABLED",True)
    assert retrieval.search("plane_wave_2d",PARAMS)["reason"]=="skill_store_unavailable"
    result=retrieval.generation_context("original",scenario_type="bad",parameters_json="{}")
    assert result["context"]=="original" and result["skill_usage"]["fallback"]
    assert not store.path.exists()


@pytest.mark.parametrize("status,grade,archived",[
    ("candidate","Q2",0),("questioned","Q4",0),("retired","Q4",0),
    ("active","Q2",0),("active","Q4",1)])
def test_non_consumable_states_are_filtered(store,monkeypatch,status,grade,archived):
    skill=bounded_candidate(store)
    with sqlite3.connect(store.path) as db:
        db.execute("UPDATE simulation_skills SET status=?,quality_level=?,archived=? WHERE skill_id=?",
                   (status,grade,archived,skill))
    monkeypatch.setattr(config,"SKILL_RETRIEVAL_ENABLED",True)
    result=retrieval.search("plane_wave_2d",PARAMS)
    assert result["reason"]=="no_applicable_skill" and result["skills"]==[]


def test_registered_mcp_search_has_no_path_or_trust_inputs(store):
    import inspect
    from app import tools
    class Registry:
        def __init__(self):self.tools={}
        def tool(self,fn):
            self.tools[fn.__name__]=fn
            return fn
    registry=Registry();tools.register(registry)
    search=registry.tools["search_simulation_skill"]
    assert set(inspect.signature(search).parameters)=={"scenario_type","parameters_json","limit"}
    assert search("plane_wave_2d",PARAMS)["reason"]=="retrieval_disabled"
    assert not store.path.exists()


def test_internal_fallback_audit_requires_server_context_and_is_append_only(store,tmp_path,monkeypatch):
    monkeypatch.setattr(config,"SKILL_RETRIEVAL_ENABLED",True)
    from app import dashboard, controlled_dispatch
    history=tmp_path/"history.db"
    monkeypatch.setattr(dashboard,"DB_PATH",str(history))
    args=dict(run_id="audit-run",request_nonce="a"*32,scenario_type="plane_wave_2d",
              parameters_json=json.dumps(controlled_dispatch.fixed_retrieval_parameters()),
              reason="provenance_authentication_unavailable")
    assert not retrieval.record_fallback_usage(**args)["audit_recorded"]
    assert not store.path.exists()
    controlled_dispatch.record(history,"audit-run","a"*32)
    result=retrieval.record_fallback_usage(**args)
    assert result["audit_recorded"] and not result["trusted_run"]
    assert retrieval.record_fallback_usage(**args)["duplicate"]
    wrong={**args,"request_nonce":"b"*32}
    assert not retrieval.record_fallback_usage(**wrong)["audit_recorded"]
    with sqlite3.connect(store.path) as db:
        row=db.execute("SELECT query_text,candidates_json FROM skill_retrievals").fetchone()
        assert "500000" not in row[0] and row[1]=="[]"
        assert json.loads(row[0])["audit_kind"]=="fallback_diagnostic"
        assert json.loads(row[0])["actual_use_authenticated"] is False
        for sql in ("UPDATE skill_retrievals SET candidates_json='[1]'",
                    "DELETE FROM skill_retrievals",
                    "INSERT OR REPLACE INTO skill_retrievals SELECT * FROM skill_retrievals"):
            with pytest.raises(sqlite3.IntegrityError,match="append-only"):
                db.execute(sql)
        assert db.execute("SELECT COUNT(*) FROM skill_retrievals").fetchone()[0]==1


def test_exact_case_is_not_lost_behind_fifty_recent_cases(store):
    from app.skill_store import normalize_parameters
    params={"frequency_hz":500000,"dx_m":.00025,"domain_N":[128,128]}
    exact=bounded_candidate(store)
    for i in range(51):
        bounded_candidate(store,normalized_params={**params,"frequency_hz":500001+i},
                          source_run_id="recent-"+str(i))
    with sqlite3.connect(store.path) as db:
        # Fixture labels are not production activation.
        db.execute("UPDATE simulation_skills SET status='active',quality_level='Q4',updated_at='2026-10-09'")
        db.execute("UPDATE simulation_skills SET updated_at='2020-01-01' WHERE skill_id=?",(exact,))
    with store._read_db() as db:
        selected=retrieval._shortlist(store,db,"plane_wave_2d",normalize_parameters(params),3)
    assert selected[0]=={"skill_id":exact,"version":1}
    assert len(selected)==3
    assert all(set(row)=={"skill_id","version"} for row in selected)

def test_shortlist_excludes_withdrawn_parent_in_same_snapshot(store,monkeypatch):
    parent=bounded_candidate(store)
    child=bounded_candidate(store,normalized_params={"frequency_hz":510000,"dx_m":.00025,"domain_N":[128,128]},
        source_run_id="derived-run",reference_skill_ids=[parent])
    with sqlite3.connect(store.path) as db:
        db.execute("UPDATE simulation_skills SET status='active',quality_level='Q4'")
        db.execute("UPDATE simulation_skills SET status='questioned' WHERE skill_id=?",(parent,))
    monkeypatch.setattr(config,"SKILL_RETRIEVAL_ENABLED",True)
    result=retrieval.search("plane_wave_2d",PARAMS)
    assert result["skills"]==[] and result["reason"]=="no_applicable_skill"


@pytest.mark.parametrize("flag",[1,"true","false",None])
def test_non_boolean_feature_flags_cannot_enable_reads_or_injection(store,monkeypatch,flag):
    monkeypatch.setattr(config,"SKILL_RETRIEVAL_ENABLED",flag)
    monkeypatch.setattr(config,"SKILL_INJECTION_ENABLED",flag)
    monkeypatch.setattr(store,"_read_db",lambda:pytest.fail("non-boolean flag cannot read"))
    assert retrieval.search("plane_wave_2d",PARAMS)["reason"]=="retrieval_disabled"
    assert retrieval.generation_context("original",scenario_type="plane_wave_2d",
        parameters_json=PARAMS)["context"]=="original"
    assert not store.path.exists()


@pytest.mark.parametrize("flag",[False,1,"true",None])
def test_library_master_switch_prevents_retrieval_and_audit_even_with_child_flags(store,monkeypatch,flag):
    monkeypatch.setattr(config,"SKILL_LIBRARY_ENABLED",flag)
    monkeypatch.setattr(config,"SKILL_RETRIEVAL_ENABLED",True)
    monkeypatch.setattr(config,"SKILL_INJECTION_ENABLED",True)
    monkeypatch.setattr(store,"_read_db",lambda:pytest.fail("master switch must prevent reads"))
    assert retrieval.search("plane_wave_2d",PARAMS)["reason"]=="retrieval_disabled"
    assert retrieval.generation_context("original",scenario_type="plane_wave_2d",parameters_json=PARAMS)["context"]=="original"
    assert retrieval.record_fallback_usage(run_id="run",request_nonce="a"*32,scenario_type="plane_wave_2d",
        parameters_json=PARAMS,reason="no_applicable_skill")["reason"]=="retrieval_disabled"
    assert not store.path.exists()

@pytest.mark.parametrize("change",[
    {"scenario_type":"point_source_2d"},
    {"parameters_json":PARAMS},
    {"parameters_json":"{}"},
    {"reason":"retrieval_timeout"}])
def test_fallback_audit_rejects_unrelated_query_without_creating_skill_db(store,tmp_path,monkeypatch,change):
    from app import dashboard,controlled_dispatch
    history=tmp_path/"history.db"
    monkeypatch.setattr(dashboard,"DB_PATH",str(history))
    monkeypatch.setattr(config,"SKILL_RETRIEVAL_ENABLED",True)
    controlled_dispatch.record(history,"bound-run","a"*32)
    args=dict(run_id="bound-run",request_nonce="a"*32,scenario_type="plane_wave_2d",
        parameters_json=json.dumps(controlled_dispatch.fixed_retrieval_parameters()),
        reason="provenance_authentication_unavailable")
    args.update(change)
    assert not retrieval.record_fallback_usage(**args)["audit_recorded"]
    assert not store.path.exists()

def test_fixed_initial_pressure_audit_never_fabricates_frequency():
    from app import controlled_dispatch
    params=controlled_dispatch.fixed_retrieval_parameters()
    assert "source_frequency" not in params and "frequency_hz" not in params
    assert params["grid_shape"]==[352,320]
    assert params["dx_m"]==[.00025,.00025]


def test_legacy_fallback_diagnostic_retry_does_not_duplicate_audit(store):
    import hashlib
    from app.skill_store import _canonical_json
    params_sha=hashlib.sha256(b"synthetic-parameters").hexdigest()
    store.migrate()
    legacy=_canonical_json(dict(scenario_type="plane_wave_2d",parameters_sha256=params_sha,
        request_nonce="a"*32,reason="provenance_authentication_unavailable"))
    with sqlite3.connect(store.path) as db:
        db.execute("INSERT INTO skill_retrievals(run_id,query_text,candidates_json,latency_ms,created_at) VALUES (?,?,?,NULL,?)",
            ("old-run",legacy,"[]","2026-01-01"))
    result=store.record_fallback_retrieval("old-run","a"*32,"plane_wave_2d",params_sha,
                                         "provenance_authentication_unavailable")
    assert result["duplicate"] and result["actual_use_authenticated"] is False
    with sqlite3.connect(store.path) as db:
        assert db.execute("SELECT count(*) FROM skill_retrievals").fetchone()[0]==1
        assert db.execute("SELECT query_text FROM skill_retrievals").fetchone()[0]==legacy

def test_generation_audit_failure_keeps_original_and_does_not_replay(store,monkeypatch):
    from app import skill_generation_decision as decisions
    monkeypatch.setattr(config,"SKILL_RETRIEVAL_ENABLED",True)
    monkeypatch.setattr(config,"SKILL_INJECTION_ENABLED",True)
    calls=[]
    original=decisions.consume
    def failed(**kwargs):
        calls.append(kwargs)
        return dict(decisions._base(kwargs["original_context"],"generation_decision_unavailable"),consumed=False)
    monkeypatch.setattr(decisions,"consume",failed)
    result=retrieval.generation_context("original",scenario_type="plane_wave_2d",parameters_json=PARAMS)
    assert result["context"]=="original" and not result["skill_usage"]["audit_recorded"]
    assert result["skill_usage"]["skills"]==[] and len(calls)==1
    with store._read_db() as db:
        assert db.execute("SELECT count(*) FROM skill_generation_consumptions").fetchone()[0]==0

def test_older_nonexact_frequency_range_survives_recent_inapplicable_cases(store):
    target=bounded_candidate(store,normalized_params={"frequency_hz":500000,"domain_N":[128,128]},
                             preconditions={"dimensions":2,"frequency_hz":[490000,510000]})
    with store._db_existing_write() as db:
        db.execute("UPDATE simulation_skills SET status='active',quality_level='Q4',updated_at='2000' WHERE skill_id=?",(target,))
    for i in range(60):
        sid=bounded_candidate(store,normalized_params={"frequency_hz":1000000+i,"domain_N":[128,128]},
            source_run_id="distractor-"+str(i),preconditions={"dimensions":2,"frequency_hz":[1000000+i,1000000+i]})
        with store._db_existing_write() as db:
            db.execute("UPDATE simulation_skills SET status='active',quality_level='Q4' WHERE skill_id=?",(sid,))
    with store._read_db() as db:
        matches=retrieval._shortlist(store,db,"plane_wave_2d",{"frequency_hz":505000,"grid_shape":[128,128]},3)
    assert matches==[{"skill_id":target,"version":1}]

def test_corrupt_duplicate_boundary_never_preselects(store):
    sid=bounded_candidate(store)
    with store._db_existing_write() as db:
        db.execute("UPDATE simulation_skills SET status='active',quality_level='Q4',preconditions=? WHERE skill_id=?",
                   ('{"dimensions":3,"dimensions":2,"frequency_hz":[400000,600000]}',sid))
    with store._read_db() as db:
        assert retrieval._shortlist(store,db,"plane_wave_2d",{"frequency_hz":500000,"dimensions":2},3)==[]

@pytest.mark.parametrize("parameters",[
    {"dimensions":True},{"dimensions":1},{"dimensions":3,"grid_shape":[128,128]},
    {"grid_shape":[128,128,128]}])
def test_declared_geometry_conflict_is_rejected_before_database(store,parameters):
    with pytest.raises(ValueError):
        retrieval.search("plane_wave_2d",json.dumps(parameters))
    assert not store.path.exists()

def test_vector_dx_prefilter_keeps_each_axis_within_bounds(store):
    sid=bounded_candidate(store,preconditions={"dimensions":2,"dx_m":[0.0002,0.0003]})
    with store._db_existing_write() as db:
        db.execute("UPDATE simulation_skills SET status='active',quality_level='Q4' WHERE skill_id=?",(sid,))
    with store._read_db() as db:
        assert retrieval._shortlist(store,db,"plane_wave_2d",{"grid_shape":[128,128],"dx_m":[0.0002,0.0003]},3)
        assert not retrieval._shortlist(store,db,"plane_wave_2d",{"grid_shape":[128,128],"dx_m":[0.0002,0.0004]},3)
