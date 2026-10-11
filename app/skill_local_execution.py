"""Explicit local reference execution through the managed executor.

No model adoption or user identity claim. Pre-dispatch intent is append-only;
a transport failure is terminal for that intent and never triggers a replay.
No public caller code, output, grade, nonce, database or signing inputs.
"""
import json
import sqlite3
import time
import uuid
import httpx
from app import config,dashboard,execution,skill_library_service as service
from app import skill_local_profile as local,skill_generation_decision as decisions
from scripts.validation.host_forward_signature import canonical

FIELDS={"decision_id","generation_run_id","scenario_type","parameters_json","original_context"}
PREFIX="\n\nValidated local fixed-profile reference (parameters must remain unchanged):\n"
ERRORS=local.ERRORS+(httpx.HTTPError,)

def enabled():
    return decisions.enabled() and config.fixed_intake_enabled()

def _reference(db,reference,at=None):
    if type(reference) is not dict or set(reference)!=FIELDS:
        raise ValueError("local_execution_reference_fields")
    for name in ("decision_id","generation_run_id"):
        if type(reference[name]) is not str:raise ValueError("local_reference_id")
        uuid.UUID(reference[name])
    context=local.read_connection(db,**reference,_decision_at=at)
    if context==reference["original_context"]:raise ValueError("local_reference_unavailable")
    ref=local._json(context[len(reference["original_context"]+PREFIX):])
    if ref["code"]!=local._recipe(ref["scope"]):raise ValueError("local_reference_code")
    return ref

def _bind(reference):
    if not enabled():raise ValueError("local_execution_disabled")
    store=service.STORE
    run=str(uuid.uuid4());nonce=uuid.uuid4().hex
    with store._db_existing_write() as db:
        decisions._budget(db);db.execute("BEGIN IMMEDIATE");now=time.time_ns()
        ref=_reference(db,reference)
        if run==ref["source_run_id"]:raise ValueError("local_execution_must_be_new")
        if config.EXECUTOR_URL!="http://jwave-executor:8010":
            raise ValueError("local_execution_target")
        request=dict(code=ref["code"],timeout_seconds=30,request_nonce=nonce)
        encoded=canonical(reference).decode()
        if len(encoded.encode())>70000:raise ValueError("local_reference_budget")
        # Capture time after lock and verification; TTL is rechecked before commit.
        registered=time.time_ns()
        _reference(db,reference,registered)
        db.execute("INSERT INTO skill_local_reference_dispatches VALUES (?,?,?,?,?,?,?,?,?,?)",
            (run,reference["generation_run_id"],reference["decision_id"],ref["skill_id"],ref["version"],
             encoded,local.pins.digest(encoded),nonce,local.pins.digest(canonical(request).decode()),registered))
        if not enabled():raise ValueError("local_execution_disabled_before_commit")
        _reference(db,reference)
    return run,nonce,ref["code"]

def _finish(run,*,failed=False):
    store=service.STORE
    capsule=None
    if not failed:capsule=local._history(run)
    with store._db_existing_write() as db:
        decisions._budget(db);db.execute("BEGIN IMMEDIATE")
        row=db.execute("SELECT * FROM skill_local_reference_dispatches WHERE run_id=?",(run,)).fetchone()
        if row is None:raise ValueError("local_execution_not_prebound")
        # Completion is one-way; a failed request can never turn into verified.
        prior=db.execute("SELECT outcome FROM skill_local_reference_completions WHERE run_id=?",(run,)).fetchone()
        if prior:raise ValueError("local_execution_already_completed")
        now=time.time_ns()
        if now<row["registered_ns"]:raise ValueError("local_execution_clock")
        verified=False;capsule_sha=stdout_sha=""
        if not failed:
            if not enabled():raise ValueError("local_execution_disabled")
            encoded=row["reference_json"]
            if len(encoded.encode())>70000 or local.pins.digest(encoded)!=row["reference_sha256"]:
                raise ValueError("local_execution_reference_integrity")
            reference=local._json(encoded)
            ref=_reference(db,reference,row["registered_ns"])
            if (ref["skill_id"]!=row["skill_id"] or ref["version"]!=row["version"]
                    or local._capsule_checked(capsule)[0]!=ref["code"]
                    or capsule["dispatch"]["request_nonce"]!=row["request_nonce"]
                    or capsule["dispatch"]["request_sha256"]!=row["request_sha256"]):
                raise ValueError("local_execution_binding_changed")
            capsule_sha=local.pins.digest(canonical(capsule).decode())
            stdout_sha=local.pins.digest(capsule["stdout"])
            verified=True
        db.execute("INSERT INTO skill_local_reference_completions VALUES (?,?,?,?,?)",
            (run,"verified" if verified else "failed",capsule_sha,stdout_sha,now))
        audit=dict(execution_run_id=run,generation_run_id=row["generation_run_id"],
            decision_id=row["decision_id"],version=row["version"],capsule_sha256=capsule_sha,
            stdout_sha256=stdout_sha,execution_reuse_verified=verified,
            actual_use_authenticated=False,model_adoption_authenticated=False,
            independent_host_authenticated=False,source_scope=local.catalog.from_raw(local._json(capsule["stdout"]))["scope"] if verified else "unverified_local_reference")
        db.execute("INSERT INTO skill_events(skill_id,event_type,detail,created_at) VALUES (?,?,?,?)",
            (row["skill_id"],"local_reference_execution_verified" if verified else "local_reference_execution_failed",
             canonical(audit).decode(),str(now)))
        if verified and not enabled():raise ValueError("local_execution_disabled_before_commit")
    return dict(recorded=True,execution_reuse_verified=verified,
        actual_use_authenticated=False,model_adoption_authenticated=False,
        reason="local_reference_execution_verified" if verified else "local_reference_execution_failed",
        run_id=run)

def execute_reference(*,decision_id,generation_run_id,scenario_type,parameters_json,original_context):
    """Internal operator entry: execute exactly one freshly reverified fixed reference."""
    denied=dict(recorded=False,execution_reuse_verified=False,actual_use_authenticated=False,
        model_adoption_authenticated=False,reason="local_reference_execution_unavailable")
    run=None
    try:
        reference=dict(decision_id=decision_id,generation_run_id=generation_run_id,
            scenario_type=scenario_type,parameters_json=parameters_json,original_context=original_context)
        run,nonce,code=_bind(reference)
        # Private fresh nonce already persisted before HTTP. No lock held during execution.
        result=execution._execute_code(code,30,request_nonce=nonce)
        dashboard.record_execution("local_reference_execute",code,result.get("exit_code"),
            result.get("timed_out",False),result.get("duration_ms"),result.get("stdout",""),
            result.get("stderr",""),run_id=run,execution_evidence=result.get("execution_evidence"))
        return _finish(run)
    except ERRORS:
        if run is not None:
            try:return _finish(run,failed=True)
            except ERRORS:return dict(denied,run_id=run,reason="local_execution_completion_unavailable")
        return denied
