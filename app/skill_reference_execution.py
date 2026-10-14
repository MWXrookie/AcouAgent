"""Internal signed execution association, no execution or user identity claim.

A generation handoff must be bound atomically to a newly approved independent
dispatch BEFORE the actual observation. Matching code after an unrelated run
does not establish reuse. Production wiring and real observer still pending.
"""
import hashlib
import json
import sqlite3
import time
from app import skill_generation_decision as decisions,skill_reference_consumer as consumer
from app import skill_host_intake as intake,skill_library_service as service
from scripts.validation.host_forward_signature import canonical,unique,reject

FIELDS={"decision_id","generation_run_id","scenario_type","parameters_json","original_context"}
PREFIX='\n\nValidated fixed-profile reference (parameters must remain unchanged):\n'

def bind_connection(db,context,reference,now):
    """Private hook inside authoritative signed-dispatch BEGIN IMMEDIATE."""
    if not decisions.enabled() or type(reference) is not dict or set(reference)!=FIELDS:
        raise ValueError("reference_dispatch_scope")
    decisions._budget(db)
    verified=consumer.read_committed_context(**reference,_db=db)
    original=reference["original_context"]
    if verified==original:raise ValueError("reference_dispatch_not_authorized")
    ref=json.loads(verified[len(original+PREFIX):],object_pairs_hook=unique,parse_constant=reject)
    if context["run_id"]==ref["source_run_id"]:raise ValueError("source_run_is_not_new_reference_execution")
    params,_,_=decisions._request(reference["scenario_type"],reference["parameters_json"],original)
    db.execute("INSERT INTO skill_reference_dispatches VALUES (?,?,?,?,?,?,?,?,?)",
        (context["run_id"],reference["generation_run_id"],reference["decision_id"],ref["skill_id"],ref["version"],
         reference["scenario_type"],canonical(params).decode(),original,now))

def observe(*,observation_document,stdout,stderr):
    denied=dict(recorded=False,execution_reuse_verified=False,actual_use_authenticated=False,
                reason="reference_execution_disabled")
    if not decisions.enabled():return denied
    try:
        store=service.STORE
        with store._db_existing_write() as db:
            decisions._budget(db);db.execute("BEGIN IMMEDIATE");now=time.time_ns()
            checked=intake.verify_source_connection(db,observation_document=observation_document,
                stdout=stdout,stderr=stderr,now=now)
            row=db.execute("SELECT * FROM skill_reference_dispatches WHERE run_id=?",(checked["run"],)).fetchone()
            if row is None or not row["registered_ns"]<=checked["payload"]["observation"]["observed_before_ns"]<=now:
                raise ValueError("reference_execution_not_prebound")
            reference={k:row[k] for k in FIELDS}
            # Immutable pre-dispatch time bounds the generation intent; current
            # root/policy/lifecycle/physics still use the current clock.
            verified=consumer.read_committed_context(**reference,_db=db,_decision_at=row["registered_ns"])
            if verified==row["original_context"]:raise ValueError("reference_execution_authority_changed")
            ref=json.loads(verified[len(row["original_context"]+PREFIX):],object_pairs_hook=unique,parse_constant=reject)
            if (ref["skill_id"]!=row["skill_id"] or ref["version"]!=row["version"]
                    or json.loads(row["parameters_json"])!=checked["parameters"]
                    or ref["code"]!=checked["code"]):
                raise ValueError("reference_execution_code_or_parameters_changed")
            obs_sha=hashlib.sha256(canonical(checked["payload"])).hexdigest()
            output_sha=intake.pins.digest(stdout)
            prior=db.execute("SELECT * FROM skill_reference_executions WHERE run_id=?",(checked["run"],)).fetchone()
            if prior:
                if prior["observation_sha256"]!=obs_sha or prior["stdout_sha256"]!=output_sha:
                    raise ValueError("reference_execution_conflict")
                return dict(denied,reason="reference_execution_already_recorded",duplicate=True)
            db.execute("INSERT INTO skill_reference_executions VALUES (?,?,?,?)",
                (checked["run"],obs_sha,output_sha,now))
            audit=dict(generation_run_id=row["generation_run_id"],decision_id=row["decision_id"],
                execution_run_id=checked["run"],version=row["version"],code_sha256=intake.pins.RECIPE_SHA256,
                observation_sha256=obs_sha,stdout_sha256=output_sha,
                execution_reuse_verified=True,actual_use_authenticated=False)
            db.execute("INSERT INTO skill_events(skill_id,event_type,detail,created_at) VALUES (?,?,?,?)",
                (row["skill_id"],"reference_execution_observed",canonical(audit).decode(),str(now)))
        return dict(recorded=True,execution_reuse_verified=True,actual_use_authenticated=False,
                    reason="signed_reference_execution_observed",run_id=checked["run"])
    except (ValueError,TypeError,KeyError,IndexError,UnicodeError,OverflowError,RecursionError,
            sqlite3.Error,OSError,ImportError):
        return dict(denied,reason="reference_execution_unavailable")
