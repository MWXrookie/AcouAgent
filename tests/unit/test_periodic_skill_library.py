import copy,json,math,uuid
import pytest
from scripts.validation import periodic_eigenmodes as g
from app import skill_local_catalog as catalog,skill_local_profile as local,skill_retrieval,skill_usage,dashboard
from scripts.validation.host_forward_signature import canonical
from tests.unit.test_skill_local_profile import ready

def evidence(scope):
 cfg=g.configuration(scope);dt=cfg["cfl"]*.0005/cfg["sound_speed_m_s"];ts=[(i+1)*dt for i in range(math.ceil(cfg["t_end_s"]/dt))]
 return {"raw_evidence":dict(profile=scope,configuration=cfg,units={"time":"s","pressure":"Pa"},time=ts,
 traces=[[g.pressure(cfg,*xy,t) for t in ts] for xy in cfg["sensor_indices"]],
 snapshots=[[g.pressure(cfg,x,y,ts[k]) for x in range(64) for y in range(64)] for k in cfg["snapshot_indices"]])}
def history(scope):
 code=catalog.recipe(scope);stdout=canonical(evidence(scope)).decode();nonce=uuid.uuid4().hex;run=str(uuid.uuid4())
 request_sha=local.pins.digest(canonical(dict(code=code,timeout_seconds=30,request_nonce=nonce)).decode())
 dispatch=dict(version="local-dispatch.v1",source_kind="managed_executor_transport",request_sha256=request_sha,
 code_sha256=local.pins.digest(code),request_nonce=nonce,timeout_seconds=30,target_url="http://jwave-executor:8010/execute",
 trust_boundary="gateway_and_internal_executor",independent_host_authenticated=False,receipt_correlated=False)
 receipt=dict(version="gateway-transport-receipt.v1",request_sha256=request_sha,request_nonce=nonce,
 stdout_sha256=local.pins.digest(stdout),stderr_sha256=local.pins.digest(""),exit_code=0,timed_out=False,
 executor_receipt_absent=True,authentication_configured=True,independent_host_authenticated=False)
 dashboard.record_execution("local_profile_test",code,0,False,1,stdout,"",run_id=run,execution_evidence=dict(local_dispatch=dispatch,local_transport_receipt=receipt))
 return run

def current_history(scope):
 code=catalog.recipe(scope);stdout=canonical(evidence(scope)).decode();nonce=uuid.uuid4().hex;run=str(uuid.uuid4())
 request_sha=local.pins.digest(canonical(dict(code=code,timeout_seconds=30,request_nonce=nonce)).decode())
 dispatch=dict(version="local-dispatch.v1",source_kind="managed_executor_transport",request_sha256=request_sha,
 code_sha256=local.pins.digest(code),request_nonce=nonce,timeout_seconds=30,target_url="http://jwave-executor:8010/execute",
 trust_boundary="gateway_and_internal_executor",independent_host_authenticated=False,receipt_correlated=True)
 from app import executor_evidence
 from tests.unit.test_executor_evidence import RUNTIME
 runtime=copy.deepcopy(RUNTIME)
 executor_receipt=dict(version="executor-receipt.v1",request_nonce=nonce,code_sha256=local.pins.digest(code),
 stdout_sha256=local.pins.digest(stdout),stderr_sha256=local.pins.digest(""),exit_code=0,timed_out=False,
 timeout_seconds=30,runtime=runtime,runtime_sha256=executor_evidence.sha256(json.dumps(runtime,sort_keys=True,separators=(",",":"))))
 compatibility=dict(version="gateway-transport-receipt.v1",request_sha256=request_sha,request_nonce=nonce,
 stdout_sha256=local.pins.digest(stdout),stderr_sha256=local.pins.digest(""),exit_code=0,timed_out=False,
 executor_receipt_absent=False,authentication_configured=True,independent_host_authenticated=False)
 dashboard.record_execution("local_profile_test",code,0,False,1,stdout,"",run_id=run,
  execution_evidence=dict(local_dispatch=dispatch,local_transport_receipt=compatibility,receipt=executor_receipt))
 return run

def test_current_executor_receipt_takes_precedence_over_compatibility_record(ready):
 from app.skill_execution_history import checked_daily_history
 scope=g.SCOPES[0];run=current_history(scope)
 capsule,code,numeric=checked_daily_history(run)
 assert capsule["stdout"] and code==catalog.recipe(scope) and numeric["numeric_pass"]
@pytest.mark.parametrize("scope",g.SCOPES)
def test_fifteen_distinct_exact_analytic_profiles(scope):
 raw=evidence(scope);assert g.validate(raw)["numeric_pass"]
 assert catalog.from_raw(raw)["scope"]==scope
 assert local.pins.digest(catalog.recipe(scope))==catalog.specification(scope)["code_sha256"]
@pytest.mark.parametrize("fault",["wrong_amplitude","wrong_phase","missing_field","wrong_unit","time_shift","changed_boundary","bool_sample"])
def test_gate_rejects_successful_but_wrong_physics(fault):
 raw=evidence(g.SCOPES[0]);r=raw["raw_evidence"]
 if fault=="wrong_amplitude":r["traces"][0]=[v*1.1 for v in r["traces"][0]]
 elif fault=="wrong_phase":r["traces"][0]=[-v for v in r["traces"][0]]
 elif fault=="missing_field":r["snapshots"][0].pop()
 elif fault=="wrong_unit":r["units"]["pressure"]="kPa"
 elif fault=="time_shift":r["time"]=[t-r["time"][0] for t in r["time"]]
 elif fault=="changed_boundary":r["configuration"]["boundary_condition"]="open"
 else:r["snapshots"][0][0]=True
 assert not g.validate(raw)["numeric_pass"]
def test_intake_reference_feedback_then_withdrawal(ready):
 store,_=ready;scope=g.SCOPES[0];run=history(scope);accepted=local.record(run);assert accepted["admitted"]
 spec=catalog.specification(scope);params=canonical(spec["parameters"]).decode()
 found=skill_retrieval.search(spec["scene"],params);assert len(found["skills"])==1
 built=skill_retrieval.generation_context("original",scenario_type=spec["scene"],parameters_json=params,_existing_store=True)
 usage=built["skill_usage"];args=dict(decision_id=usage["decision_id"],generation_run_id=usage["generation_intent_id"],scenario_type=spec["scene"],parameters_json=params,original_context="original")
 from scripts.workflow.skill_handoff_wire import main
 assert main("original",built["context"])["generation_context"]==built["context"]
 notice=skill_usage.reference_notice(dict(node_id="local-basic-main-reader",inputs=args,outputs=dict(text=built["context"])))
 assert notice["stage"]=="reference_delivered" and not notice["model_adoption_authenticated"]
 other=history(scope)
 assert skill_usage.execution_notice(other)["stage"]=="recipe_execution_verified"
 assert skill_usage.execution_notice(run) is None
 store.record_negative_feedback(accepted["skill_id"],run,actor="portal-admin",reason="incorrect result")
 assert skill_usage.reference_notice(dict(node_id="local-basic-main-reader",inputs=args,outputs=dict(text=built["context"]))) is None
 assert skill_usage.execution_notice(other) is None
 assert not skill_retrieval.search(spec["scene"],params)["skills"]
def test_self_report_does_not_generate_feedback():
 assert skill_usage.reference_notice(dict(node_id="llm",inputs={},outputs=dict(text="used skill"))) is None
 assert skill_usage.execution_node_notice(dict(node_type="llm",outputs=dict(json=[dict(run_id="fake",execution_reuse_verified=True)]))) is None

@pytest.mark.parametrize("scope",g.SCOPES)
def test_extraction_adapter_preserves_explicit_periodic_configuration(scope):
 from scripts.workflow.skill_parameter_adapter import main
 spec=catalog.specification(scope);params=dict(spec["parameters"],scenario_type=spec["scene"])
 result=main(canonical(params).decode(),original_query=canonical(spec["parameters"]).decode());assert result["ready"]
 assert local._json(result["retrieval_parameters_json"])==spec["parameters"]
 params["boundary_condition"]="open";assert not main(canonical(params).decode())["ready"]
