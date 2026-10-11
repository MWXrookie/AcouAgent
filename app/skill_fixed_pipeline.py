"""Internal fixed-observation -> candidate -> authenticated admission pipeline.

Only server-owned authority DB records and pinned raw evidence are accepted.
No public learning tool, simulation rerun, private key access or caller grades.
Stages fail closed; a partial storage failure may leave a Q2 candidate, never a
trusted handoff. Each trust-changing stage repeats current source authorization.
"""
import hashlib
import json
import sqlite3
import time
from app import config,skill_host_intake as intake,skill_fixed_activation as activation
from app import skill_library_service as service
from app.skill_store import skill_fingerprint,preconditions_match
from app.skill_generation_decision import _budget
from scripts.validation.host_forward_signature import canonical,unique,reject

def accumulate(*,observation_document,stdout,stderr,scenario_type="initial_pressure_2d"):
    denied=dict(intake.TRUST,recorded=False,admitted=False,reason="fixed_pipeline_disabled")
    if not config.fixed_intake_enabled():
        return denied
    try:
        if scenario_type not in {"initial_pressure_2d","plane_wave_2d"}:
            raise ValueError("fixed_pipeline_scene")
        store=service.STORE
        with store._db_existing_write() as db:
            _budget(db);db.execute("BEGIN IMMEDIATE")
            # Select historical acceptance only from an existing server receipt,
            # never from a caller-supplied timestamp or truth flag.
            if type(observation_document) is not bytes or len(observation_document)>16384:
                raise ValueError("fixed_source_budget")
            envelope=json.loads(observation_document.decode(),object_pairs_hook=unique,parse_constant=reject)
            obs=envelope["payload"]["observation"]
            historical=bool(db.execute("SELECT 1 FROM skill_host_verifications WHERE run_id=? AND nonce=?",
                                      (obs["run_id"],obs["nonce"])).fetchone())
            checked=intake.verify_source_connection(db,observation_document=observation_document,
                stdout=stdout,stderr=stderr,now=time.time_ns(),recorded_evidence=historical)
            params=checked["parameters"]
            sha=hashlib.sha256(canonical(params)).hexdigest()
            bounds=dict(dimensions=2,parameters_sha256=sha)
            row=db.execute("SELECT substr(preconditions,1,4097) preconditions FROM simulation_skills WHERE fingerprint=?",
                           (skill_fingerprint(scenario_type,params),)).fetchone()
            if row:
                if len(row["preconditions"].encode())>4096:raise ValueError("existing_bounds_budget")
                bounds=json.loads(row["preconditions"],object_pairs_hook=unique,parse_constant=reject)
                if not preconditions_match(bounds,params):raise ValueError("existing_bounds_mismatch")
        # Candidate creation is still Q2. A race/revocation between these stages
        # cannot grant Q4: intake and admission each recheck authority/lifecycle.
        sid=store.create_candidate(scenario_type=scenario_type,
            state_text="固定均匀介质高斯初压；按真实配置和原始数组复核",
            normalized_params=params,code_template=checked["code"],preconditions=bounds,
            postconditions={"numeric_prerequisites_passed":True},
            physics_evidence={"scope":intake.approved.PROFILE,
                "code_sha256":intake.pins.RECIPE_SHA256,"stdout_sha256":intake.pins.digest(stdout),
                "observation_sha256":hashlib.sha256(canonical(checked["payload"])).hexdigest(),
                "validator_sha256":intake.pins.VALIDATOR_SHA256,"trust_verified":False},
            source_run_id=checked["run"],validator_version=checked["result"]["validator_version"],
            quality_level="Q2",idempotent_source_run=True)
        with store._read_db() as db:
            version=db.execute("SELECT max(version) FROM skill_versions WHERE skill_id=?",(sid,)).fetchone()[0]
        args=dict(skill_id=sid,version=version,observation_document=observation_document,stdout=stdout,stderr=stderr)
        source=intake.record(**args)
        admitted=activation.admit(**args)
        # Return trust only from an admission that actually committed this call.
        return dict(denied,recorded=True,skill_id=sid,version=version,
            source_recorded=source["recorded"],admitted=admitted["admitted"],
            reason=admitted["reason"],**{k:admitted[k] for k in intake.TRUST},
            quality_level=admitted["quality_level"])
    except (ValueError,TypeError,KeyError,IndexError,UnicodeError,OverflowError,RecursionError,
            sqlite3.Error,OSError,ImportError):
        return dict(denied,reason="fixed_pipeline_unavailable")
