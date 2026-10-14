"""Internal four-experiment source/consistency diagnostic; at most Q2.

Only the exact reviewed uniform periodic Gaussian initial-pressure base recipe.
Self-consistency is not analytical correctness or five-class VAL-1 acceptance.
No public route, keys, network, general geometry, derived versions or code grant.
No Q3/active/code handoff: four-experiment consistency does not satisfy the
five-class independent physical acceptance contract, even with owner signatures.
"""
import hashlib
import json
import sqlite3
import time
from app import config,skill_library_service as service,skill_consistency_source as source
from app import skill_deadline as deadline
from app.skill_generation_decision import _budget
from app.skill_store import normalize_parameters
from scripts.validation.host_forward_signature import canonical,unique,reject


def parameters():
    configuration=source.numerical.configuration_for("base")
    raw={k:v for k,v in configuration.items() if k not in
        {"boundary","alternate_units","output_pressure_time_s","output_sample_origin"}}
    raw.update(boundary_condition="periodic",dimensions=2,solver_mode="time_domain",
               medium_type="homogeneous_water")
    return normalize_parameters(raw)


def _enabled():
    return config.SKILL_LIBRARY_ENABLED is True and config.SKILL_LEARNING_ENABLED is True


def accumulate_candidate(*,batch_id,records):
    denied=dict(source.FALSE_TRUST,recorded=False,reason="consistency_admission_disabled")
    if not _enabled():return denied
    checked=source.inspect_batch(batch_id=batch_id,records=records)
    if not checked["source_prerequisites_passed"] or not checked["numeric_pass"]:
        return dict(denied,reason="consistency_source_or_numeric_prerequisites_failed")
    try:
        params=parameters();pin=source.manifest()
        evidence=dict(source.FALSE_TRUST,scope=source.PROFILE,batch_id=batch_id,
            manifest_sha256=pin["sha256"],source=checked["source"],
            physics_checks=checked["numeric"]["checks"])
        sid=service.STORE.create_candidate(scenario_type="initial_pressure_2d",
            state_text="均匀周期高斯初压；有限解析对照及四实验候选，完整物理验收未通过",
            normalized_params=params,code_template=source.numerical.recipe(*source.numerical.PROFILES["base"]),
            preconditions=dict(dimensions=2,parameters_sha256=hashlib.sha256(canonical(params)).hexdigest()),
            postconditions=dict(has_signal=True,consistency_bundle_passed=True),
            physics_evidence=evidence,source_run_id=checked["source"]["base"]["run_id"],
            validator_version="consistency-source.v1",quality_level="Q2",idempotent_source_run=True)
        return dict(denied,recorded=True,skill_id=sid,reason="bound_consistency_candidate_only")
    except (ValueError,TypeError,KeyError,UnicodeError,OverflowError,RecursionError,sqlite3.Error,OSError):
        return dict(denied,reason="consistency_candidate_unavailable")


def record_diagnostic(*,skill_id,version,batch_id,records):
    denied=dict(source.FALSE_TRUST,admitted=False,diagnostic_recorded=False,quality_level="Q2",
        reason="consistency_admission_disabled")
    if not _enabled():return denied
    try:
        if type(skill_id) is not str or not 1<=len(skill_id)<=128 or type(version) is not int or version<1:
            raise ValueError("candidate_reference")
        with deadline.limited():
            with service.STORE._db_existing_write() as db:
                _budget(db);db.execute("BEGIN IMMEDIATE");now=time.time_ns()
                checked=source.verify_connection(db,batch_id=batch_id,records=records,now=now)
                if not checked["source_prerequisites_passed"] or not checked["numeric_pass"]:
                    raise ValueError("consistency_prerequisites")
                row=db.execute("""SELECT s.status,s.archived,s.scenario_type,s.quality_level,s.preconditions,v.* FROM simulation_skills s
                    JOIN skill_versions v ON v.skill_id=s.skill_id
                    WHERE s.skill_id=? AND v.version=?""",(skill_id,version)).fetchone()
                params=parameters();pin=source.manifest()
                own=checked["source"]["base"];code=source.numerical.recipe(*source.numerical.PROFILES["base"])
                latest=db.execute("SELECT MAX(version) FROM skill_versions WHERE skill_id=?",(skill_id,)).fetchone()[0]
                if (row is None or latest!=version or row["status"]!="candidate" or row["quality_level"]!="Q2" or row["archived"]
                        or row["scenario_type"]!="initial_pressure_2d"
                        or row["validator_version"]!="consistency-source.v1" or row["code_template"]!=code
                        or row["code_hash"]!=hashlib.sha256(code.encode()).hexdigest()
                        or len(row["normalized_params"].encode())>4096
                        or normalize_parameters(json.loads(row["normalized_params"],object_pairs_hook=unique,parse_constant=reject))!=params
                        or len(row["preconditions"].encode())>4096
                        or json.loads(row["preconditions"],object_pairs_hook=unique,parse_constant=reject)!=dict(
                            dimensions=2,parameters_sha256=hashlib.sha256(canonical(params)).hexdigest())):
                    raise ValueError("exact_consistency_candidate_changed")
                if (row["reference_skill_ids"]!="[]" or db.execute(
                        "SELECT 1 FROM skill_references WHERE skill_id=? AND version=? LIMIT 1",(skill_id,version)).fetchone()):
                    raise ValueError("consistency_derivation_not_supported")
                evidence_raw=row["physics_evidence"]
                if row["source_run_id"]!=own["run_id"]:
                    observations=db.execute("""SELECT substr(detail,1,16385) detail FROM skill_events
                        WHERE skill_id=? AND event_type='candidate_observed' AND json_valid(detail)
                        AND json_extract(detail,'$.source_run_id')=? ORDER BY id DESC LIMIT 2""",
                        (skill_id,own["run_id"])).fetchall()
                    if len(observations)!=1 or len(observations[0]["detail"].encode())>16384:
                        raise ValueError("unique_consistency_observation_required")
                    observed=json.loads(observations[0]["detail"],object_pairs_hook=unique,parse_constant=reject)
                    if (type(observed.get("version")) is not int or observed["version"]!=version
                            or observed.get("code_hash")!=row["code_hash"]
                            or observed.get("evidence_binding",{}).get("validator_version")!="consistency-source.v1"):
                        raise ValueError("consistency_observation_version_or_code")
                    evidence_raw=canonical(observed["evidence_binding"]["physics_evidence"]).decode()
                if len(evidence_raw.encode())>16384:raise ValueError("candidate_evidence_budget")
                old=json.loads(evidence_raw,object_pairs_hook=unique,parse_constant=reject)
                if (type(old) is not dict or old.get("batch_id")!=batch_id
                        or old.get("manifest_sha256")!=pin["sha256"] or old.get("source")!=checked["source"]):
                    raise ValueError("candidate_bundle_changed")
                # Root/window/version/numeric/negative feedback and reset epoch
                # all stay in this decision snapshot; no earlier checked bool.
                _feedback_epoch(db,skill_id,checked)
                parameter_sha=hashlib.sha256(canonical(params)).hexdigest()
                detail=dict(source.FALSE_TRUST,batch_id=batch_id,version=version,source=checked["source"],
                    manifest_sha256=pin["sha256"],parameters_sha256=parameter_sha,
                    quality_level="Q2",scope=source.PROFILE,
                    physical_acceptance_passed=False)
                stamp=time.strftime("%Y-%m-%dT%H:%M:%SZ",time.gmtime())
                event=db.execute("INSERT INTO skill_events(skill_id,event_type,detail,created_at) VALUES (?,?,?,?)",
                    (skill_id,"consistency_profile_diagnostic",canonical(detail).decode(),stamp)).lastrowid
                # All four hashes remain in this immutable receipt. Existing
                # fixed consumers reject this profile/validator/code identity.
                db.execute("INSERT INTO skill_host_verifications VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (own["run_id"],own["nonce"],skill_id,version,source.ROOT_ID,
                     json.loads(records["base"]["observation_document"],object_pairs_hook=unique,parse_constant=reject)["payload"]["revision"],
                     pin["recipes"]["base"]["code_sha256"],parameter_sha,own["observation_sha256"],
                     own["stdout_sha256"],pin["sha256"],canonical(detail).decode(),now,event))
                deadline.check()
        return dict(denied,diagnostic_recorded=True,quality_level="Q2",scope=source.PROFILE,
            reason="source_consistency_diagnostic_not_physical_acceptance")
    except (ValueError,TypeError,KeyError,IndexError,UnicodeError,OverflowError,RecursionError,sqlite3.Error,OSError,ImportError):
        return dict(denied,reason="consistency_admission_unavailable")


def _feedback_epoch(db,skill_id,checked):
    """Current feedback and every dispatch versus latest immutable reset."""
    epoch=db.execute("""SELECT detail FROM skill_events WHERE skill_id=?
        AND event_type='revalidation_requested' ORDER BY id DESC LIMIT 1""",(skill_id,)).fetchone()
    requested=0
    if epoch:
        if len(epoch["detail"].encode())>16384:raise ValueError("epoch_budget")
        requested=json.loads(epoch["detail"],object_pairs_hook=unique,parse_constant=reject)["requested_ns"]
        if type(requested) is not int or requested<0:raise ValueError("epoch_time")
    for case,proof in checked["source"].items():
        registered=db.execute("SELECT registered_ns FROM skill_host_dispatches WHERE run_id=?",
            (proof["run_id"],)).fetchone()[0]
        if requested and registered<=requested:raise ValueError("fresh_consistency_batch_required")
        if db.execute("SELECT 1 FROM skill_feedback WHERE skill_id=? AND run_id=? LIMIT 1",
                (skill_id,proof["run_id"])).fetchone():
            raise ValueError("negative_consistency_source")
        statement=db.execute("""SELECT substr(detail,1,16385) detail FROM skill_events
            WHERE skill_id=? AND event_type='feedback_statement' AND json_valid(detail)
            AND json_extract(detail,'$.run_id')=? ORDER BY id DESC LIMIT 1""",(skill_id,proof["run_id"])).fetchone()
        if statement:
            if len(statement["detail"].encode())>16384:raise ValueError("feedback_budget")
            if json.loads(statement["detail"],object_pairs_hook=unique,parse_constant=reject).get("confirmed") is not True:
                raise ValueError("withdrawn_consistency_source")



def verify_admitted_connection(db,*,skill_id,version,batch_id,records,now):
    """Fail closed: consistency and signatures cannot authorize physical reuse."""
    raise ValueError("independent_complete_physical_acceptance_required")
