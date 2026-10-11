import copy,json,sqlite3,uuid
import pytest
from app import dashboard,skill_local_profile as local,skill_local_catalog as catalog
from app import skill_retrieval,skill_local_execution as runner,execution
from tests.unit.test_skill_local_profile import ready
from scripts.validation.host_forward_signature import canonical

def history():
    from scripts.validation.interface_waveform import analytic_traces,WIDE_CONFIGURATION,WIDE_PROFILE
    from tests.unit.test_executor_evidence import RUNTIME
    cfg=WIDE_CONFIGURATION;times=[(i+1)*1e-8 for i in range(2400)]
    left,right=analytic_traces(times,cfg)
    stdout=json.dumps(dict(raw_evidence=dict(profile=WIDE_PROFILE,configuration=cfg,
        units=dict(time="s",pressure="Pa"),dt_s=1e-8,time=times,pressure_left=left,pressure_right=right)))
    runtime=copy.deepcopy(RUNTIME);runtime["executor_source_sha256"]=local.EXECUTOR_SOURCE_SHA256
    code=catalog.recipe(catalog.INTERFACE);nonce=uuid.uuid4().hex
    receipt=dict(version="executor-receipt.v1",request_nonce=nonce,code_sha256=catalog.INTERFACE_CODE_SHA256,
        stdout_sha256=local.pins.digest(stdout),stderr_sha256=local.pins.digest(""),exit_code=0,timed_out=False,
        timeout_seconds=30,runtime=runtime,runtime_sha256=local.pins.digest(canonical(runtime).decode()))
    result=dict(stdout=stdout,executor_receipt=receipt)
    nonce=receipt["request_nonce"];code=catalog.recipe(catalog.INTERFACE)
    dispatch=dict(version="local-dispatch.v1",source_kind="managed_executor_transport",
        request_sha256=local.pins.digest(canonical(dict(code=code,timeout_seconds=30,request_nonce=nonce)).decode()),
        code_sha256=catalog.INTERFACE_CODE_SHA256,request_nonce=nonce,timeout_seconds=30,
        target_url="http://jwave-executor:8010/execute",trust_boundary="gateway_and_internal_executor",
        independent_host_authenticated=False,receipt_correlated=True)
    run=str(uuid.uuid4())
    dashboard.record_execution("local_profile_test",code,0,False,1,result["stdout"],"",run_id=run,
        execution_evidence=dict(receipt=receipt,local_dispatch=dispatch))
    return run

def test_distinct_reviewed_profile_can_retrieve_deliver_and_withdraw(ready):
    store,_=ready;source=history();accepted=local.record(source);assert accepted["admitted"]
    spec=catalog.specification(catalog.INTERFACE);params=canonical(spec["parameters"]).decode()
    found=skill_retrieval.search(spec["scene"],params)
    assert len(found["skills"])==1 and found["skills"][0]["scope"]==catalog.INTERFACE
    built=skill_retrieval.generation_context("original",scenario_type=spec["scene"],parameters_json=params,_existing_store=True)
    assert not built["skill_usage"]["fallback"]
    from scripts.workflow.skill_handoff_wire import main
    assert main("original",built["context"])["generation_context"]==built["context"]
    usage=built["skill_usage"]
    call=dict(decision_id=usage["decision_id"],generation_run_id=usage["generation_intent_id"],
        scenario_type=spec["scene"],parameters_json=params,original_context="original")
    assert local.read_context(**call)==built["context"]
    # Exact heterogeneous parameters cannot borrow the first-medium scalar.
    changed=dict(spec["parameters"],secondary_sound_speed_m_s=2501.)
    assert not skill_retrieval.search(spec["scene"],canonical(changed).decode())["skills"]
    assert not skill_retrieval.search(local.SCENE,params)["skills"]
    store.record_negative_feedback(accepted["skill_id"],source,actor="portal-admin",reason="incorrect result")
    assert local.read_context(**call)=="original"

def test_wrong_sign_even_with_forged_self_consistent_output_hash_is_not_admitted(ready):
    store,_=ready;source=history()
    with sqlite3.connect(dashboard.DB_PATH) as db:
        stdout,proof=db.execute("SELECT stdout,executor_evidence_json FROM executions WHERE run_id=?",(source,)).fetchone()
        raw=json.loads(stdout);raw["raw_evidence"]["pressure_right"]=[-p for p in raw["raw_evidence"]["pressure_right"]]
        stdout=json.dumps(raw);proof=json.loads(proof);proof["receipt"]["stdout_sha256"]=local.pins.digest(stdout)
        db.execute("UPDATE executions SET stdout=?,executor_evidence_json=? WHERE run_id=?",(stdout,json.dumps(proof),source))
    assert not local.record(source)["admitted"]
    with store._read_db() as db:assert db.execute("SELECT count(*) FROM skill_local_sources").fetchone()[0]==0

def test_shared_catalog_does_not_expand_legacy_narrow_profile(ready):
    store,_=ready
    with pytest.raises(ValueError):catalog.from_raw({"raw_evidence":{"profile":"val1-interface-v1"}})

def test_interface_code_and_validator_pins_are_required(monkeypatch):
    monkeypatch.setattr(catalog.pins,"digest",lambda _: "0"*64)
    with pytest.raises(ValueError):catalog.recipe(catalog.INTERFACE)
