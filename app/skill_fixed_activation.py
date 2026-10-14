"""Internal exact-profile Q4 admission; never accepts caller quality flags.

Production still defaults off and cannot admit without separately signed root,
run approval, immutable source receipt and recomputed pinned analytic traces.
This limited profile does not establish general Q3 or five-class acceptance.
"""
import hashlib
import sqlite3
import time
from app import config,skill_library_service as service,skill_host_intake as intake
from app.skill_generation_decision import _budget
from app.skill_store import normalize_parameters
from scripts.validation.host_forward_signature import canonical

def admit(*,skill_id,version,observation_document,stdout,stderr):
    denied=dict(intake.TRUST,admitted=False,quality_level="Q2",reason="fixed_admission_disabled")
    if not config.fixed_intake_enabled():
        return denied
    try:
        store=service.STORE
        with store._db_existing_write() as db:
            _budget(db);db.execute("BEGIN IMMEDIATE");now=time.time_ns()
            checked=intake.verify_connection(store,db,skill_id=skill_id,version=version,
                observation_document=observation_document,stdout=stdout,stderr=stderr,now=now,
                recorded_evidence=True,require_independent_root=True)
            _budget(db)
            receipt=db.execute("SELECT * FROM skill_host_verifications WHERE skill_id=? AND version=? AND run_id=? AND nonce=?",
                (skill_id,version,checked["run"],checked["nonce"])).fetchone()
            digest=hashlib.sha256(canonical(checked["payload"])).hexdigest()
            parameter_sha=hashlib.sha256(canonical(checked["parameters"])).hexdigest()
            if (receipt is None or receipt["observation_sha256"]!=digest
                    or receipt["stdout_sha256"]!=intake.pins.digest(stdout)
                    or receipt["code_sha256"]!=intake.pins.RECIPE_SHA256
                    or receipt["validator_sha256"]!=intake.pins.VALIDATOR_SHA256
                    or receipt["parameters_sha256"]!=parameter_sha):
                raise ValueError("fixed_source_receipt")
            source=db.execute("SELECT substr(normalized_params,1,4097) parameters FROM skill_versions WHERE skill_id=? AND version=?",
                              (skill_id,version)).fetchone()
            if source is None or len(source["parameters"].encode())>4096:
                raise ValueError("stored_source_parameter_budget")
            declared=normalize_parameters(intake.json.loads(source["parameters"],
                object_pairs_hook=intake.signatures.unique,parse_constant=intake.signatures.reject))
            if declared!=checked["parameters"]:raise ValueError("stored_source_parameters_mismatch")
            # Only a candidate can be newly admitted. Questioned, archived and
            # withdrawn records cannot be restored by replaying old evidence.
            row=db.execute("SELECT status,quality_level,archived,scenario_type FROM simulation_skills WHERE skill_id=?",
                           (skill_id,)).fetchone()
            if row is None or row["status"]!="candidate" or row["archived"] or row["scenario_type"] not in {"plane_wave_2d","initial_pressure_2d"}:
                raise ValueError("fixed_admission_lifecycle")
            negative=db.execute("SELECT 1 FROM skill_feedback WHERE skill_id=? AND run_id=? LIMIT 1",
                                (skill_id,checked["run"])).fetchone()
            statements=db.execute("""SELECT substr(detail,1,16385) detail FROM skill_events
                WHERE skill_id=? AND event_type='feedback_statement'
                AND json_valid(detail) AND json_extract(detail,'$.run_id')=?
                ORDER BY id DESC LIMIT 1""",(skill_id,checked["run"])).fetchone()
            if negative or (statements and (
                    len(statements["detail"].encode())>16384
                    or intake.json.loads(statements["detail"],object_pairs_hook=intake.signatures.unique,
                                         parse_constant=intake.signatures.reject).get("confirmed") is not True)):
                raise ValueError("feedback_requires_new_evidence")
            detail=dict(version=version,source_run_id=checked["run"],code_sha256=intake.pins.RECIPE_SHA256,
                parameters_sha256=parameter_sha,observation_sha256=digest,
                validator_sha256=intake.pins.VALIDATOR_SHA256,root_id=receipt["root_id"],revision=receipt["revision"],
                quality_level="Q4",scope=intake.approved.PROFILE,actual_use_authenticated=False,
                trusted_run=True,runtime_approved=True,run_bound=True,parameters_bound=True)
            bounds=dict(dimensions=2,parameters_sha256=parameter_sha)
            stamp=time.strftime("%Y-%m-%dT%H:%M:%SZ",time.gmtime())
            _budget(db)
            db.execute("INSERT INTO skill_events(skill_id,event_type,detail,created_at) VALUES (?,?,?,?)",
                       (skill_id,"fixed_profile_admitted",canonical(detail).decode(),stamp))
            db.execute("UPDATE simulation_skills SET status='active',quality_level='Q4',preconditions=?,updated_at=? WHERE skill_id=?",
                       (canonical(bounds).decode(),stamp,skill_id))
            # Recheck authority at the last decision point; a valid receipt
            # cannot keep an expired root alive while admission is processing.
            _budget(db)
            intake.skill_authority.inspect_connection(
                db,checked["run"],checked["nonce"],time.time_ns(),recorded_evidence=True)
            if not config.fixed_intake_enabled():
                raise ValueError("fixed_admission_disabled_before_commit")

        return dict(denied,admitted=True,quality_level="Q4",reason="fixed_profile_admitted",
                    trusted_run=True,runtime_approved=True,run_bound=True,parameters_bound=True)
    except (ValueError,TypeError,KeyError,IndexError,UnicodeError,OverflowError,RecursionError,
            sqlite3.Error,OSError,ImportError):
        return dict(denied,reason="fixed_admission_unavailable")
