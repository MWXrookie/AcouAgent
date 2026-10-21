import json
import sqlite3
import pytest
from app import candidate_intake, config, dashboard, execution, skill_library_service, tools, analysis
from app.skill_store import SkillStore
from tests.unit.test_controlled_validation import history

PARAMS = dict(sound_speed=1500, density=1000, source_frequency=100000,
              domain_N=[32,32], domain_dx=0.00025, cfl=0.3, pml_size=4,
              source_index=[8,8], sensor_index=[20,20],
              solver_mode="time_domain", medium_type="water")
CODE = "print('ordinary simulation')"

class Registry:
    def __init__(self): self.functions={}
    def tool(self, function):
        self.functions[function.__name__]=function
        return function

def output(value=1.0):
    return analysis._FIELD_START+json.dumps(dict(data=[[value]*32 for _ in range(32)],
                                                shape=[32,32]))+analysis._FIELD_END

@pytest.fixture
def env(history, tmp_path, monkeypatch):
    monkeypatch.setattr(config,"SKILL_LIBRARY_ENABLED",True)
    monkeypatch.setattr(config,"SKILL_LEARNING_ENABLED",True)
    store=SkillStore(tmp_path/"skills.db")
    monkeypatch.setattr(skill_library_service,"STORE",store)
    calls=[]
    def execute(code, timeout):
        calls.append(code)
        return dict(stdout=output(),stderr="",exit_code=0,timed_out=False,duration_ms=1)
    monkeypatch.setattr(execution,"_execute_code",execute)
    monkeypatch.setattr(execution,"_normalize_result_artifacts",lambda result: None)
    registry=Registry();tools.register(registry)
    return history,store,calls,registry.functions

def run(env, **kwargs):
    return env[3]["run_jwave_code"](CODE,scenario_type="point_source_2d",
                                   params_json=json.dumps(PARAMS),**kwargs)

def test_recorded_final_output_creates_candidate_once(env):
    result=run(env)
    first=result["candidate_accumulation"]
    assert first["recorded"] and first["reason"]=="candidate_only"
    assert candidate_intake.complete(result["run_id"])==first
    assert len(env[2])==1
    with sqlite3.connect(env[1].path) as db:
        assert db.execute("SELECT status,quality_level,success_count FROM simulation_skills").fetchone()==("candidate","Q2",1)
        assert db.execute("SELECT count(*) FROM skill_events").fetchone()[0]==1
    assert all(first[key] is False for key in candidate_intake.FALSE_TRUST)

@pytest.mark.parametrize("flag",[False,1,"true",None])
def test_disabled_does_not_access_skill_store(env,monkeypatch,flag):
    monkeypatch.setattr(config,"SKILL_LEARNING_ENABLED",flag)
    result=run(env)
    assert "candidate_accumulation" not in result
    assert len(env[2])==1 and not env[1].path.exists()

@pytest.mark.parametrize("fault",["zero","failure","timeout","boolean","nan","duplicate"])
def test_bad_recorded_output_cannot_accumulate(env,monkeypatch,fault):
    def execute(code,timeout):
        value=output(0) if fault=="zero" else output(True) if fault=="boolean" else output(float("nan")) if fault=="nan" else output()
        if fault=="duplicate":
            value=value.replace('"shape":', '"shape":[32,32],"shape":')
        return dict(stdout=value,stderr="",exit_code=1 if fault=="failure" else 0,
                    timed_out=fault=="timeout",duration_ms=1)
    monkeypatch.setattr(execution,"_execute_code",execute)
    result=run(env)
    assert not result["candidate_accumulation"]["recorded"]
    assert not env[1].path.exists()

@pytest.mark.parametrize("change",[dict(source_index=[0,0]),dict(domain_dx=True),
                                  dict(domain_N=[True,32]),dict(source_frequency=0),
                                  dict(runtime_approved=True)])
def test_invalid_or_trust_claiming_parameters_never_accumulate(env,change):
    params=dict(PARAMS,**change)
    result=env[3]["run_jwave_code"](CODE,scenario_type="point_source_2d",params_json=json.dumps(params))
    assert not result.get("candidate_accumulation",{}).get("recorded")
    assert not env[1].path.exists()

def test_executor_cannot_smuggle_reserved_context(env,monkeypatch):
    forged=candidate_intake.prepare(CODE,"point_source_2d",json.dumps(PARAMS))
    monkeypatch.setattr(execution,"_execute_code",lambda *args:dict(stdout=output(),stderr="",exit_code=0,
        timed_out=False,execution_evidence={"candidate_intake":forged}))
    result=env[3]["run_jwave_code"](CODE)
    assert candidate_intake.complete(result["run_id"])["reason"]=="missing_intake_context"
    assert not env[1].path.exists()

def test_ambiguous_run_reference_rejected(env):
    run(env,run_id="reused")
    second=run(env,run_id="reused")
    assert second["candidate_accumulation"]["reason"]=="missing_or_ambiguous_execution"
    with sqlite3.connect(env[1].path) as db:
        assert db.execute("SELECT success_count FROM simulation_skills").fetchone()[0]==1

def test_storage_failure_does_not_repeat_executor(env,monkeypatch):
    def fail(**kwargs):raise sqlite3.OperationalError("test-only")
    monkeypatch.setattr(env[1],"create_candidate",fail)
    result=run(env)
    assert result["exit_code"]==0 and len(env[2])==1
    assert result["candidate_accumulation"]["reason"]=="candidate_store_unavailable"

def test_retry_captures_actual_final_code(env,monkeypatch):
    monkeypatch.setenv("MODEL_BASE_URL","https://models.example.test/v1")
    monkeypatch.setenv("MODEL_NAME","test-model")
    monkeypatch.setenv("MODEL_API_KEY","test-only")
    monkeypatch.setattr(tools.llm,"_llm_fix_code",lambda *args,**kwargs:"print('corrected')")
    monkeypatch.setattr(tools.cache_store,"_update_cache",lambda *args,**kwargs:None)
    count=[]
    def execute(code,timeout):
        count.append(code)
        return dict(stdout=output(),stderr="test error" if len(count)==1 else "",
                    exit_code=1 if len(count)==1 else 0,timed_out=False,duration_ms=1)
    monkeypatch.setattr(execution,"_execute_code",execute)
    result=env[3]["run_jwave_code_with_retry"](CODE,max_retries=1,scenario_type="point_source_2d",
                                              params_json=json.dumps(PARAMS))
    assert result["candidate_accumulation"]["recorded"]
    assert count==[CODE,"print('corrected')"]
    with sqlite3.connect(env[0]) as db:
        assert db.execute("SELECT code FROM executions").fetchone()[0]=="print('corrected')"


def test_declared_boundary_does_not_generalize_to_other_parameters(env):
    from app.skill_store import preconditions_match, normalize_parameters
    result=run(env)
    with sqlite3.connect(env[1].path) as db:
        bounds=json.loads(db.execute("SELECT preconditions FROM simulation_skills").fetchone()[0])
    canonical=normalize_parameters({("density_kg_m3" if k=="density" else k):v for k,v in PARAMS.items()})
    canonical["dimensions"]=len(PARAMS["domain_N"])
    assert preconditions_match(bounds,canonical)
    assert not preconditions_match(bounds,dict(canonical,sound_speed_m_s=1400))
    assert not preconditions_match(bounds,dict(canonical,grid_shape=[64,64]))
    assert not preconditions_match(bounds,dict(canonical,source_index=[9,9]))

def test_recovery_resumes_after_write_failure_without_executor_replay(env,monkeypatch):
    original=env[1].create_candidate
    def fail(**kwargs):raise sqlite3.OperationalError("temporary test fault")
    monkeypatch.setattr(env[1],"create_candidate",fail)
    result=run(env)
    outcome=candidate_intake.recover_pending()
    assert outcome["reason"]=="candidate_store_unavailable"
    assert outcome["next_after_execution_id"]==0
    monkeypatch.setattr(env[1],"create_candidate",original)
    restored=candidate_intake.recover_pending()
    assert restored["recorded"]==1 and restored["next_after_execution_id"]>0
    assert candidate_intake.recover_pending(after_execution_id=restored["next_after_execution_id"])["examined"]==0
    assert len(env[2])==1

def test_generic_case_feedback_uses_persisted_run_association(env):
    result=run(env)
    sid=result["candidate_accumulation"]["skill_id"]
    assert env[1].record_negative_feedback(sid,result["run_id"],actor="internal-system",
                                           reason="case failed manual review")["recorded"]
    with pytest.raises(ValueError,match="run_not_associated"):
        env[1].record_negative_feedback(sid,"unrelated-run",actor="internal-system",reason="invalid")
    assert env[1].record_negative_feedback(sid,result["run_id"],actor="internal-system",
                                           reason="same observation")["duplicate"]
    with sqlite3.connect(env[1].path) as db:
        assert db.execute("SELECT status,quality_level,failure_count FROM simulation_skills").fetchone()==("candidate","Q2",1)

def test_output_window_explicitly_too_short_not_accumulated(env,monkeypatch):
    metadata=dict(version=1,time_unit="s",medium="uniform",sound_speed_m_s=1500,
                  source_frequency_hz=100000,source_position_m=[0,0],
                  sensor_position_m=[0.005,0],source_start_s=0,source_kind="continuous_sinusoid")
    start,end=analysis._SENSOR_MARKERS[0]
    stdout=output()+start+json.dumps(dict(time=[0,1e-7],pressure=[0,1],timing_metadata=metadata))+end
    monkeypatch.setattr(execution,"_execute_code",lambda *args:dict(stdout=stdout,stderr="",exit_code=0,
                                                                 timed_out=False))
    result=run(env)
    assert result["candidate_accumulation"]["reason"]=="candidate_window_insufficient"
    assert not env[1].path.exists()


def test_recovery_skips_permanent_bad_output_and_continues(env,monkeypatch):
    monkeypatch.setattr(execution,"_execute_code",lambda *args:dict(stdout=output(float("nan")),
                                                                 stderr="",exit_code=0,timed_out=False))
    run(env)
    monkeypatch.setattr(execution,"_execute_code",lambda *args:dict(stdout=output(),
                                                                 stderr="",exit_code=0,timed_out=False))
    run(env)
    recovered=candidate_intake.recover_pending()
    assert recovered["reason"]=="candidate_scan_complete"
    assert recovered["rejected"]==1 and recovered["recorded"]==1

def test_bool_exit_code_cannot_become_integer_evidence(env,monkeypatch):
    monkeypatch.setattr(execution,"_execute_code",lambda *args:dict(stdout=output(),stderr="",
                                                                 exit_code=False,timed_out=False))
    result=run(env)
    assert not result["candidate_accumulation"]["recorded"]
    assert not env[1].path.exists()


def test_audit_insert_failure_rolls_back_optional_intake_without_execution_replay(env):
    env[1].migrate()
    with sqlite3.connect(env[1].path) as db:
        db.execute("CREATE TRIGGER test_candidate_audit_failure BEFORE INSERT ON skill_events BEGIN SELECT RAISE(ABORT,'test-only'); END")
    result=run(env)
    assert result["exit_code"]==0 and len(env[2])==1
    assert result["candidate_accumulation"]["reason"]=="candidate_store_unavailable"
    with sqlite3.connect(env[1].path) as db:
        assert db.execute("SELECT count(*) FROM simulation_skills").fetchone()[0]==0
        assert db.execute("SELECT count(*) FROM skill_versions").fetchone()[0]==0
        db.execute("DROP TRIGGER test_candidate_audit_failure")
    assert candidate_intake.recover_pending()["recorded"]==1
    assert len(env[2])==1


def test_gaussian_initial_pressure_candidate_does_not_invent_frequency(env):
    params=dict(PARAMS)
    params.pop("source_frequency")
    params.update(source_kind="gaussian_initial_pressure",initial_pressure_pa=1,
                  initial_sigma_m=.001,initial_velocity="zero",t_end=8e-6)
    result=env[3]["run_jwave_code"](CODE,scenario_type="initial_pressure_2d",params_json=json.dumps(params))
    assert result["candidate_accumulation"]["recorded"]
    with sqlite3.connect(env[1].path) as db:
        stored=json.loads(db.execute("SELECT normalized_params FROM skill_versions").fetchone()[0])
    assert "frequency_hz" not in stored and stored["initial_sigma_m"]==.001

@pytest.mark.parametrize("change",[dict(initial_sigma_m=.0001),dict(initial_velocity="unknown"),
                                  dict(initial_pressure_pa=True),dict(source_frequency=100000),
                                  dict(t_end=None),dict(t_end=0)])
def test_unresolved_or_conflicting_initial_pressure_profile_rejected(env,change):
    params=dict(PARAMS);params.pop("source_frequency")
    params.update(source_kind="gaussian_initial_pressure",initial_pressure_pa=1,
                  initial_sigma_m=.001,initial_velocity="zero",t_end=8e-6)
    params.update(change)
    result=env[3]["run_jwave_code"](CODE,scenario_type="initial_pressure_2d",params_json=json.dumps(params))
    assert not result.get("candidate_accumulation",{}).get("recorded")
    assert not env[1].path.exists()


def test_missing_initial_pressure_time_does_not_accumulate(env):
    params=dict(PARAMS)
    params.pop("source_frequency")
    params.update(source_kind="gaussian_initial_pressure",initial_pressure_pa=1,
                  initial_sigma_m=.001,initial_velocity="zero")
    assert "t_end" not in params
    result=env[3]["run_jwave_code"](
        CODE,scenario_type="initial_pressure_2d",params_json=json.dumps(params))
    assert result["candidate_accumulation"]["reason"]=="candidate_quality_insufficient"
    assert not env[1].path.exists()


@pytest.mark.parametrize("declared,stride,data_size,recorded",[
    ([32,32],1,16,False),([16,16],2,16,True),([32,32],2,16,True),
    ([1000000,1000000],1,32,False),([16,16],True,16,False)])
def test_field_geometry_cannot_be_forged_and_coherent_striding_is_supported(
        env,monkeypatch,declared,stride,data_size,recorded):
    payload=dict(data=[[1.0]*data_size for _ in range(data_size)],shape=declared,downsample=stride)
    stdout=analysis._FIELD_START+json.dumps(payload)+analysis._FIELD_END
    monkeypatch.setattr(execution,"_execute_code",lambda *args:dict(stdout=stdout,stderr="",exit_code=0,
                                                                 timed_out=False))
    result=run(env)
    assert result["candidate_accumulation"]["recorded"] is recorded
    if not recorded:
        assert result["candidate_accumulation"]["reason"]=="candidate_output_geometry_unavailable"
        assert not env[1].path.exists()


@pytest.mark.parametrize("suffix",["duplicate","unclosed","wide_oversize"])
def test_ambiguous_or_byte_oversize_output_is_not_partially_adopted(env,monkeypatch,suffix):
    raw=output()
    raw+=output(0) if suffix=="duplicate" else analysis._FIELD_START if suffix=="unclosed" else "声"*140000
    monkeypatch.setattr(execution,"_execute_code",lambda *args:dict(stdout=raw,stderr="",exit_code=0,timed_out=False))
    result=run(env)
    assert not result["candidate_accumulation"]["recorded"] and not env[1].path.exists()
    assert result["candidate_accumulation"]["reason"]==(
        "candidate_output_budget" if suffix=="wide_oversize" else "candidate_field_payload_ambiguous")
    recovered=candidate_intake.recover_pending()
    assert recovered["reason"]=="candidate_scan_complete" and recovered["rejected"]==1
    assert recovered["next_after_execution_id"]>0
    with sqlite3.connect(env[0]) as db:
        assert db.execute("SELECT stdout FROM executions").fetchone()[0]==raw


def test_legacy_missing_run_index_scan_is_bounded(env):
    # 400k compact synthetic legacy rows, not a production history. One matching
    # result at the end would otherwise force the whole missing-index scan.
    with sqlite3.connect(env[0]) as db:
        db.execute("DROP INDEX idx_executions_run_id")
        db.execute("""WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i+1 FROM n WHERE i<400000)
            INSERT INTO executions(timestamp,tool_name,code,exit_code,timed_out,duration_ms,
                stdout,stderr,attempt_count,run_id)
            SELECT 't','test','pass',0,0,1,'','',1,'legacy-'||i FROM n""")
    outcome=candidate_intake.complete("absent-after-large-history")
    assert not outcome["recorded"] and outcome["reason"]=="execution_store_unavailable"
    recovered=candidate_intake.recover_pending(limit=1)
    assert recovered["reason"]=="execution_store_unavailable" and recovered["next_after_execution_id"]==0
    assert not env[1].path.exists()


def test_blob_context_is_refused_without_breaking_optional_learning(env):
    result=run(env)
    with sqlite3.connect(env[0]) as db:
        db.execute("UPDATE executions SET executor_evidence_json=? WHERE run_id=?",
            (sqlite3.Binary(b"not JSON text"),result["run_id"]))
    outcome=candidate_intake.complete(result["run_id"])
    assert not outcome["recorded"] and outcome["reason"]=="intake_context_mismatch"
    assert len(env[2])==1
    with sqlite3.connect(env[1].path) as db:
        assert db.execute("SELECT success_count FROM simulation_skills").fetchone()[0]==1
        assert db.execute("SELECT COUNT(*) FROM skill_events").fetchone()[0]==1
