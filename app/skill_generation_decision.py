"""Internal generation fallback decisions, not authenticated positive reuse.

No public MCP/HTTP registration. Authoritative decision and one-use audit share
the skill SQLite DB. Current API always returns unchanged context and zero code.
It records controller intent only, not actual model/executor or user identity.
"""
import hashlib
import json
import re
import sqlite3
import time
import uuid
from app import config, skill_library_service, skill_retrieval

FALSE_TRUST = dict(trusted_run=False,runtime_approved=False,run_bound=False,
                   parameters_bound=False,actual_use_authenticated=False)

def enabled():
    return (config.SKILL_LIBRARY_ENABLED is True
            and config.SKILL_RETRIEVAL_ENABLED is True
            and config.SKILL_INJECTION_ENABLED is True)

def _request(scenario_type,parameters_json,context):
    if type(context) is not str or len(context.encode("utf-8"))>65536:
        raise ValueError("context_budget")
    parameters=skill_retrieval._request(scenario_type,parameters_json,3)
    raw=json.dumps(parameters,sort_keys=True,separators=(",",":"),ensure_ascii=False,allow_nan=False)
    return parameters,hashlib.sha256(raw.encode()).hexdigest(),hashlib.sha256(context.encode()).hexdigest()

def _base(context,reason):
    return dict(FALSE_TRUST,context=context,skills=[],fallback=True,reason=reason)

def _budget(db):
    steps=[0]
    def progress():
        steps[0]+=1000
        from app.skill_deadline import expired
        return int(steps[0]>1000000 or expired())
    db.set_progress_handler(progress,1000)

def prepare(*,scenario_type,parameters_json,original_context,_existing_store=False):
    result=_base(original_context,"generation_decision_disabled")
    if not enabled():
        return dict(result,prepared=False)
    try:
        params,param_sha,context_sha=_request(scenario_type,parameters_json,original_context)
        store=skill_library_service.STORE
        if _existing_store is not True:
            store.migrate()
        decision_id=str(uuid.uuid4());nonce=uuid.uuid4().hex
        with (store._db_existing_write() if _existing_store is True else store._db()) as db:
            _budget(db)
            db.execute("BEGIN IMMEDIATE")
            snapshot=skill_retrieval._shortlist(store,db,scenario_type,params,3)
            reason="provenance_authentication_unavailable" if snapshot else "no_applicable_skill"
            now=time.time_ns()
            db.execute("INSERT INTO skill_generation_decisions VALUES (?,?,?,?,?,?,?,?,?)",
                (decision_id,nonce,scenario_type,param_sha,context_sha,now,now+30000000000,
                 json.dumps(snapshot,sort_keys=True,separators=(",",":")),reason))
        return dict(_base(original_context,reason),prepared=True,decision_id=decision_id,nonce=nonce)
    except (ValueError,TypeError,OverflowError,UnicodeError,RecursionError,sqlite3.Error,OSError):
        return dict(_base(original_context,"generation_decision_unavailable"),prepared=False)

def consume(*,decision_id,nonce,generation_run_id,scenario_type,parameters_json,original_context):
    result=_base(original_context,"generation_decision_disabled")
    if not enabled():
        return dict(result,consumed=False)
    try:
        if (type(decision_id) is not str or str(uuid.UUID(decision_id))!=decision_id
                or type(nonce) is not str or not re.fullmatch("[0-9a-f]{32}",nonce)
                or type(generation_run_id) is not str
                or str(uuid.UUID(generation_run_id))!=generation_run_id):
            raise ValueError("invalid_decision_identity")
        params,param_sha,context_sha=_request(scenario_type,parameters_json,original_context)
        store=skill_library_service.STORE
        # No migration or missing-database creation during consumption.
        with store._db_existing_write() as db:
            _budget(db)
            db.execute("BEGIN IMMEDIATE")
            row=db.execute("SELECT * FROM skill_generation_decisions WHERE decision_id=?",(decision_id,)).fetchone()
            if row is None:
                return dict(_base(original_context,"decision_missing"),consumed=False)
            if (row["nonce"]!=nonce or row["scenario_type"]!=scenario_type
                    or row["parameters_sha256"]!=param_sha or row["context_sha256"]!=context_sha):
                return dict(_base(original_context,"decision_context_mismatch"),consumed=False)
            now=time.time_ns()
            if not row["created_ns"]<=now<row["expires_ns"]:
                return dict(_base(original_context,"decision_expired_or_clock_invalid"),consumed=False)
            if db.execute("SELECT 1 FROM skill_generation_consumptions WHERE decision_id=? OR generation_run_id=?",
                          (decision_id,generation_run_id)).fetchone():
                return dict(_base(original_context,"decision_already_consumed"),consumed=False)
            current=skill_retrieval._shortlist(store,db,scenario_type,params,3)
            current_json=json.dumps(current,sort_keys=True,separators=(",",":"))
            reason=("decision_metadata_changed" if current_json!=row["metadata_snapshot_json"] else
                    "provenance_authentication_unavailable" if current else "no_applicable_skill")
            db.execute("INSERT INTO skill_generation_consumptions VALUES (?,?,?,?,?)",
                       (decision_id,generation_run_id,now,current_json,reason))
        # consumed means one fallback intent audit, never permission to inject.
        return dict(_base(original_context,reason),consumed=True,audit_kind="generation_fallback_intent")
    except (ValueError,TypeError,OverflowError,UnicodeError,RecursionError,sqlite3.Error,OSError):
        return dict(_base(original_context,"generation_decision_unavailable"),consumed=False)
