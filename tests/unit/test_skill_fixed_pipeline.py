import json
import pytest
from app import config,skill_fixed_pipeline as pipeline,skill_reference_registry as registry,skill_retrieval
from app import skill_generation_decision as decisions
from host_observer.authority_signing import skill_reference_policy
from scripts.validation.host_forward_signature import canonical
from tests.unit.test_skill_host_intake import ready
from tests.unit.test_skill_authority import prepared,root
import hashlib

@pytest.mark.parametrize("manual",[False,True])
def test_raw_signed_source_to_daily_generation_then_withdrawal(ready,monkeypatch,manual):
    store,key,ctx,obs,args,doc=ready
    monkeypatch.setattr(config,"SKILL_MANUAL_INTAKE_ENABLED",manual)
    monkeypatch.setattr(config,"SKILL_LEARNING_ENABLED",not manual)
    result=pipeline.accumulate(observation_document=args["observation_document"],stdout=args["stdout"],stderr="")
    assert result["recorded"] and result["admitted"] and result["quality_level"]=="Q4"
    assert not result["actual_use_authenticated"]
    sid=result["skill_id"]
    for flag in ("SKILL_RETRIEVAL_ENABLED","SKILL_INJECTION_ENABLED"):
        monkeypatch.setattr(config,flag,True)
    params=json.dumps(pipeline.intake.controlled_dispatch.fixed_retrieval_parameters())
    ps=hashlib.sha256(canonical(json.loads(params))).hexdigest()
    proof=dict(skill_id=sid,version=result["version"],observation_document=args["observation_document"],
        stdout=args["stdout"],stderr="",approval_document=skill_reference_policy(key,skill_id=sid,version=1,
            parameters_sha256=ps,observation_sha256=hashlib.sha256(canonical(json.loads(doc())["payload"])).hexdigest(),
            revision=1,valid_from_ns=90_000_000_000,valid_until_ns=180_000_000_000,scenario_type="initial_pressure_2d"))
    assert registry.register(proof)["recorded"]
    generated=skill_retrieval.generation_context("initial pulse",scenario_type="initial_pressure_2d",parameters_json=params)
    assert not generated["skill_usage"]["fallback"] and generated["skill_usage"]["skills"][0]["skill_id"]==sid
    # A repeated callback cannot add an observation count or promote twice.
    before=store.get_skill(sid)["success_count"]
    again=pipeline.accumulate(observation_document=args["observation_document"],stdout=args["stdout"],stderr="")
    assert again["recorded"] and not again["admitted"]
    assert store.get_skill(sid)["success_count"]==before
    store.record_feedback_statement(sid,ctx["run_id"],confirmed=True,request_id="confirm",actor="portal-admin",reason="test")
    store.record_feedback_statement(sid,ctx["run_id"],confirmed=False,request_id="withdraw",actor="portal-admin",reason="test")
    after=skill_retrieval.generation_context("initial pulse",scenario_type="initial_pressure_2d",parameters_json=params)
    assert after["context"]=="initial pulse" and after["skill_usage"]["skills"]==[]

@pytest.mark.parametrize("fault",["signature","raw","revocation","off"])
def test_untrusted_or_disabled_source_is_not_added(ready,monkeypatch,fault):
    store,key,ctx,obs,args,doc=ready
    with store._read_db() as db:
        before=db.execute("SELECT count(*) FROM simulation_skills").fetchone()[0]
    if fault=="signature":
        envelope=json.loads(args["observation_document"]);envelope["signature"]="0"*128
        args["observation_document"]=canonical(envelope)
    elif fault=="raw":args["stdout"]='{}'
    elif fault=="revocation":assert pipeline.intake.skill_authority.record_root_statement(root(key,2,"revoke"))["recorded"]
    else:monkeypatch.setattr(config,"SKILL_LEARNING_ENABLED",False)
    got=pipeline.accumulate(observation_document=args["observation_document"],stdout=args["stdout"],stderr="")
    assert not got["recorded"]
    with store._read_db() as db:
        assert db.execute("SELECT count(*) FROM simulation_skills").fetchone()[0]==before


def test_initial_pressure_adapter_reaches_registered_reference_without_manual_json(ready,monkeypatch):
    from scripts.workflow import skill_parameter_adapter as adapter
    from tests.unit.test_skill_generation_tool import registered
    store,key,ctx,obs,args,doc=ready
    result=pipeline.accumulate(observation_document=args["observation_document"],stdout=args["stdout"],stderr="")
    assert result["admitted"]
    for flag in ("SKILL_RETRIEVAL_ENABLED","SKILL_INJECTION_ENABLED"):monkeypatch.setattr(config,flag,True)
    actual=pipeline.intake.controlled_dispatch.fixed_retrieval_parameters()
    inverse={v:k for k,v in adapter.ALIASES.items()}
    declared={inverse.get(k,k):v for k,v in actual.items() if k!="dimensions"}
    extracted=adapter.main(json.dumps(dict(declared,scenario_type="initial_pressure_2d")))
    assert extracted["ready"]
    proof=dict(skill_id=result["skill_id"],version=result["version"],observation_document=args["observation_document"],
        stdout=args["stdout"],stderr="",approval_document=skill_reference_policy(key,skill_id=result["skill_id"],version=1,
            parameters_sha256=hashlib.sha256(canonical(actual)).hexdigest(),
            observation_sha256=hashlib.sha256(canonical(json.loads(doc())["payload"])).hexdigest(),
            revision=1,valid_from_ns=90_000_000_000,valid_until_ns=180_000_000_000,scenario_type="initial_pressure_2d"))
    assert registry.register(proof)["recorded"]
    funcs=registered()
    generated=funcs["build_simulation_generation_context"]("user input",extracted["scenario_type"],extracted["retrieval_parameters_json"])
    assert not generated["skill_usage"]["fallback"]
    drift=dict(declared,initial_sigma_m=.004)
    other=adapter.main(json.dumps(drift),"initial_pressure_2d")
    rejected=funcs["build_simulation_generation_context"]("different width","initial_pressure_2d",other["retrieval_parameters_json"])
    assert rejected["context"]=="different width" and rejected["skill_usage"]["skills"]==[]


@pytest.mark.parametrize("manual",[False,1,"true",None])
def test_manual_intake_requires_strict_true_and_never_creates_database(tmp_path,monkeypatch,manual):
    from app.skill_store import SkillStore
    from app import skill_library_service
    store=SkillStore(tmp_path/"missing.db")
    monkeypatch.setattr(skill_library_service,"STORE",store)
    monkeypatch.setattr(config,"SKILL_LIBRARY_ENABLED",True)
    monkeypatch.setattr(config,"SKILL_LEARNING_ENABLED",False)
    monkeypatch.setattr(config,"SKILL_MANUAL_INTAKE_ENABLED",manual)
    got=pipeline.accumulate(observation_document=b"",stdout="",stderr="")
    assert got["reason"]=="fixed_pipeline_disabled"
    assert not store.path.exists()

def test_manual_intake_does_not_enable_automatic_learning(ready,monkeypatch):
    store,key,ctx,obs,args,doc=ready
    monkeypatch.setattr(config,"SKILL_LEARNING_ENABLED",False)
    monkeypatch.setattr(config,"SKILL_MANUAL_INTAKE_ENABLED",True)
    assert config.fixed_intake_enabled()
    assert config.SKILL_LEARNING_ENABLED is False
    assert not pipeline.accumulate(observation_document=b"{}",stdout="",stderr="")["recorded"]
