"""Existing catalog and natural-language integration retain complete case identity."""
import copy
import hashlib
import json
from pathlib import Path

import pytest

from app.skill_spec import prepare_skill_spec
from app.skill_store import SkillStore, normalize_parameters


def sample():
    return json.loads((Path(__file__).resolve().parents[2] / "docs/skills-next/examples/b02-manual-skill.json").read_text(encoding="utf-8"))


def envelope(spec):
    return dict(normalizer_version="normalizer_v2", parameters=spec["parameters"], result_spec=spec["result_spec"])


def candidate(store, db, spec):
    from app.skill_store import _canonical_json
    return store.create_candidate(scenario_type=spec["scenario_type"],state_text=spec["name"],
        normalized_params=spec["parameters"],code_template=spec["code_template"],
        postconditions={"verdict":"normal"},physics_evidence={"spec":spec},
        source_run_id="stage3-source",validator_version="general-helmholtz.v1",quality_level="Q2",
        preconditions={"dimensions":2,"parameters_sha256":hashlib.sha256(_canonical_json(spec["parameters"]).encode()).hexdigest()},
        _existing_connection=db,_prepared_spec=spec)


def test_v2_candidate_uses_complete_identity_without_relaxing_legacy(tmp_path):
    spec=prepare_skill_spec(json.dumps(sample()))["spec"]
    store=SkillStore(tmp_path/"skills.sqlite")
    store.migrate_skill_authoring()
    with pytest.raises(ValueError):normalize_parameters(spec["parameters"])
    with store._db_existing_write() as db:
        db.execute("BEGIN IMMEDIATE")
        sid=candidate(store,db,spec)
        row=db.execute("SELECT fingerprint,status,quality_level FROM simulation_skills WHERE skill_id=?",(sid,)).fetchone()
        assert row["fingerprint"]==prepare_skill_spec(json.dumps(spec))["case_fingerprint"]
        assert (row["status"],row["quality_level"])==("candidate","Q2")
    detail=store.read_detail(sid)
    assert detail["available"] is True
    assert detail["trust_verified"] is False
    assert detail["item"]["selected_version"]["normalized_params"]==spec["parameters"]


def test_v2_candidate_requires_explicit_extension_and_exact_spec(tmp_path):
    spec=prepare_skill_spec(json.dumps(sample()))["spec"]
    store=SkillStore(tmp_path/"skills.sqlite");store.migrate()
    with store._db_existing_write() as db:
        db.execute("BEGIN IMMEDIATE")
        with pytest.raises(ValueError,match="general_candidate_authority_required"):candidate(store,db,spec)
    store.migrate_skill_authoring()
    with store._db_existing_write() as db:
        db.execute("BEGIN IMMEDIATE")
        sid=candidate(store,db,spec)
        db.execute("UPDATE skill_versions SET physics_evidence='{}' WHERE skill_id=?",(sid,))
    assert store.read_detail(sid)["available"] is False


def test_retrieval_v2_keeps_material_and_result_identity():
    from app import skill_retrieval as retrieval
    from app import skill_general_library as general
    spec=sample();query=envelope(spec)
    normalized=retrieval._request(spec["scenario_type"],json.dumps(query),3)
    base=general.query_fingerprint(spec["scenario_type"],normalized)
    changed=copy.deepcopy(query)
    changed["parameters"]["material_regions"][0]["sound_speed_m_s"]+=1
    assert general.query_fingerprint(spec["scenario_type"],general.normalize_query(spec["scenario_type"],json.dumps(changed)))!=base
    assert normalized["result_spec"]==spec["result_spec"]


def test_workflow_binds_query_to_actual_simulation_contract():
    from app import local_workflow as workflow
    spec=sample()
    extracted=dict(scenario_type=spec["scenario_type"],simulation_params=spec["parameters"],result_spec=spec["result_spec"])
    assert workflow._skill_query(extracted) is not None
    extracted["skill_query"]={"scenario_type":spec["scenario_type"],"parameters":copy.deepcopy(spec["parameters"])}
    assert workflow._skill_query(extracted) is not None
    extracted["skill_query"]["parameters"]["helmholtz_source_coefficient"]+=1
    assert workflow._skill_query(extracted) is None
    extracted.pop("skill_query")
    extracted["simulation_params"].pop("material_regions")
    assert workflow._skill_query(extracted) is None


def test_workflow_displays_verified_general_field_without_legacy_reanalysis(monkeypatch):
    import asyncio
    from app import local_workflow as workflow,skill_retrieval
    spec=sample()
    extracted=dict(scenario_type=spec["scenario_type"],simulation_params=spec["parameters"],result_spec=spec["result_spec"])
    async def model(profile,task,prompt,run):
        assert task != "code_generation"
        return {"content":json.dumps(extracted) if task=="parameter_extraction" else "review", "profile_id":"test","model":"test"}
    monkeypatch.setattr(workflow,"_model",model)
    usage={"reason":"general_validated_reference_delivery","skills":[],"decision_id":"decision","generation_intent_id":"intent"}
    monkeypatch.setattr(skill_retrieval,"generation_context",lambda *a,**kw:{"context":"server context","skill_usage":usage})
    analyzed={"verdict":"normal","quality_level":"Q4","max_pressure":2.0,"rms_pressure":0.1,
        "field_shape":[64,64],"summary":"independent complex-field relative error 0.01", "units":"numerical_source_normalization"}
    raw_output = '__SKILL_PROBE_START__{"npy_zlib_base64":"binary-export"}__SKILL_PROBE_END__'
    monkeypatch.setattr(workflow,"_direct_reference",lambda *a:({"exit_code":0,"timed_out":False,"stdout":raw_output,"stderr":"","run_id":"new-run"},
        {"execution_reuse_verified":True,"analysis":analyzed,"run_id":"new-run"}))
    def forbidden(*a,**kw):raise AssertionError("verified complex field must not enter legacy parser")
    monkeypatch.setattr(workflow.analysis,"analyze_and_record",forbidden)
    monkeypatch.setattr(workflow,"_tool_functions",forbidden)
    def report(result, code):
        assert 'binary-export' not in result['stdout'] and '__SKILL_PROBE_START__' not in result['stdout']
        assert 'binary-export' in raw_output  # presentation filtering preserves the source
        return 'execution report'
    monkeypatch.setattr(workflow.execution,"_build_report",report)
    async def run():return [item async for item in workflow.workflow_events("simulate", "session", "test")]
    result=asyncio.run(run())[-1]
    assert result["workflow_status"]=="succeeded"
    assert result["quality_level"]=="Q4" and result["run_id"]=="new-run"
    assert "numerical_source_normalization" in result["report"]
    assert "relative error 0.01" in result["report"]


def test_unadmitted_general_request_never_enters_legacy_generation(monkeypatch):
    import asyncio
    from app import local_workflow as workflow,skill_retrieval
    spec=sample()
    extracted=dict(scenario_type=spec["scenario_type"],simulation_params=spec["parameters"],result_spec=spec["result_spec"])
    async def model(profile,task,prompt,run):
        assert task=="parameter_extraction"
        return {"content":json.dumps(extracted),"profile_id":"test","model":"test"}
    monkeypatch.setattr(workflow,"_model",model)
    monkeypatch.setattr(skill_retrieval,"generation_context",lambda *a,**kw:{"context":"original","skill_usage":{"skills":[],"fallback":True}})
    async def run():return [item async for item in workflow.workflow_events("simulate","session","test")]
    result=asyncio.run(run())[-1]
    assert result["workflow_status"]=="error"
    assert result["error_code"]=="validated_general_reference_required"


@pytest.mark.parametrize("field",["name","description"])
def test_v2_editorial_revision_creates_version_without_changing_case(tmp_path,field):
    spec=prepare_skill_spec(json.dumps(sample()))["spec"]
    store=SkillStore(tmp_path/"skills.sqlite");store.migrate_skill_authoring()
    with store._db_existing_write() as db:
        db.execute("BEGIN IMMEDIATE")
        sid=candidate(store,db,spec)
        before=db.execute("SELECT fingerprint FROM simulation_skills WHERE skill_id=?",(sid,)).fetchone()[0]
        db.execute("UPDATE simulation_skills SET status='active',quality_level='Q4' WHERE skill_id=?",(sid,))
    changed=copy.deepcopy(spec);changed[field]+=" revised"
    with store._db_existing_write() as db:
        db.execute("BEGIN IMMEDIATE")
        assert candidate(store,db,changed)==sid
        row=db.execute("SELECT fingerprint,status,quality_level FROM simulation_skills WHERE skill_id=?",(sid,)).fetchone()
        assert row["fingerprint"]==before
        assert (row["status"],row["quality_level"])==("questioned","Q2")
        versions=db.execute("SELECT version,physics_evidence FROM skill_versions WHERE skill_id=? ORDER BY version",(sid,)).fetchall()
        assert [v["version"] for v in versions]==[1,2]
        assert json.loads(versions[0]["physics_evidence"])["spec"][field]==spec[field]
        assert json.loads(versions[1]["physics_evidence"])["spec"][field]==changed[field]
