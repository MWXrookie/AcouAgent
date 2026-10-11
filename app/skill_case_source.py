"""Exact case provenance prerequisites in the authority DB; no admission/MCP.
Requires a separate owner-approved case namespace and a fresh registered dispatch.
No key loading, automatic registration, database creation, network or promotion.
"""
import hashlib
import json
import re
import sqlite3
import time
import uuid
from pathlib import Path
from app import config,skill_authority as native,skill_library_service as service,skill_deadline as deadline
from app.skill_generation_decision import _budget
from app.skill_consistency_source import _require_current_schema,_observation
from scripts.validation import case_batch_registry as registry,approved_host_root as approved,host_forward_binding as binding
from scripts.validation.host_forward_signature import canonical,unique,reject

ROOT_ID=approved.ROOT_ID+"_case_batch1"
PROFILE="case-source.v1"
DOMAIN=b"AcouAgent/case-owner/v1\x00"
ROOT_FIELDS={"purpose","root_id","profile","revision","action","valid_from_ns","valid_until_ns","manifest_sha256"}
RUN_FIELDS={"purpose","root_id","profile","revision","batch_id","case","run_id","nonce",
            "context_sha256","code_sha256","configuration_sha256","manifest_sha256"}
CONTEXT=native.CONTEXT|{"batch_id","case"}
FALSE_TRUST=dict(native.TRUST,actual_use_authenticated=False)

def enabled():
    return config.SKILL_LIBRARY_ENABLED is True and config.SKILL_CASE_OBSERVATION_ENABLED is True

def manifest():
    root=Path(__file__).resolve().parents[1]
    validators={name:hashlib.sha256((root/name).read_bytes()).hexdigest() for name in (
        "app/skill_case_source.py","app/skill_case_candidate.py","app/skill_store.py","app/config.py","app/skill_authority.py","app/skill_consistency_source.py",
        "scripts/validation/case_batch_registry.py","scripts/validation/case_batch1.py",
        "scripts/validation/plane_wave_diagnostic.py","scripts/validation/cylindrical_evidence.py",
        "scripts/validation/interface_evidence.py","scripts/validation/attenuation_evidence.py",
        "scripts/validation/host_forward_binding.py","host_observer/forwarder.py",
        "host_observer/case_runner.py","host_observer/case_signing.py")}
    body=dict(profile=PROFILE,recipes={case:registry.pins(case) for case in registry.CASES},validators=validators)
    return dict(body,sha256=hashlib.sha256(canonical(body)).hexdigest())

def _statement(raw,fields):
    if type(raw) is not str or len(raw.encode())>8192:raise ValueError("statement_budget")
    doc=json.loads(raw,object_pairs_hook=unique,parse_constant=reject)
    if type(doc) is not dict or set(doc)!={"payload","signature"}:raise ValueError("statement_fields")
    body,sig=doc["payload"],doc["signature"]
    if (type(body) is not dict or set(body)!=fields or body["root_id"]!=ROOT_ID
            or body["profile"]!=PROFILE or type(body["revision"]) is not int
            or not 1<=body["revision"]<=2147483647 or body["manifest_sha256"]!=manifest()["sha256"]
            or type(sig) is not str or not re.fullmatch("[0-9a-f]{128}",sig)):
        raise ValueError("explicit_case_scope_required")
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    from cryptography.exceptions import InvalidSignature
    try:Ed25519PublicKey.from_public_bytes(approved.approved_public_key()).verify(bytes.fromhex(sig),DOMAIN+canonical(body))
    except InvalidSignature:raise ValueError("case_owner_signature") from None
    return body

def _window(body,now):
    first,last=body["valid_from_ns"],body["valid_until_ns"]
    if (body["purpose"]!="case_root_lifecycle" or body["action"] not in {"approve","revoke"}
            or type(first) is not int or type(last) is not int
            or not 0<=first<=now<last<=2**63-1 or last-first>86400_000_000_000):
        raise ValueError("case_root_window")

def _root(db,revision,now):
    row=db.execute("SELECT * FROM skill_authority_roots WHERE root_id=? ORDER BY revision DESC LIMIT 1",(ROOT_ID,)).fetchone()
    if row is None or row["revision"]!=revision:raise ValueError("case_root_revision")
    body=_statement(row["statement_json"],ROOT_FIELDS);_window(body,now)
    if body["action"]!="approve" or any(body[k]!=row[k] for k in ("revision","action","valid_from_ns","valid_until_ns")):
        raise ValueError("case_root_revoked")
    return body

def _context(ctx,now):
    if type(ctx) is not dict or set(ctx)!=CONTEXT:raise ValueError("case_context_fields")
    if (type(ctx["batch_id"]) is not str or str(uuid.UUID(ctx["batch_id"]))!=ctx["batch_id"]
            or type(ctx["case"]) is not str or ctx["case"] not in registry.CASES):
        raise ValueError("closed_case_batch")
    native._context({k:ctx[k] for k in native.CONTEXT},now,_root_id=ROOT_ID)
    return hashlib.sha256(canonical(ctx)).hexdigest()

def _run(body,ctx):
    if (body["purpose"]!="approve_case_run"
            or any(body[k]!=ctx[k] for k in ("revision","batch_id","case","run_id","nonce"))
            or body["context_sha256"]!=hashlib.sha256(canonical(ctx)).hexdigest()
            or any(body[k]!=v for k,v in registry.pins(ctx["case"]).items())):
        raise ValueError("case_run_scope")

ERRORS=(ValueError,TypeError,KeyError,IndexError,UnicodeError,OverflowError,RecursionError,sqlite3.Error,OSError,ImportError)

def record_policy(document):
    denied=dict(FALSE_TRUST,recorded=False,reason="case_source_disabled")
    if not enabled():return denied
    try:
        with deadline.limited():
            body=_statement(document,ROOT_FIELDS)
            with service.STORE._db_existing_write() as db:
                _budget(db);db.execute("BEGIN IMMEDIATE");_require_current_schema(db);_window(body,time.time_ns())
                prior=db.execute("SELECT revision,action FROM skill_authority_roots WHERE root_id=? ORDER BY revision DESC LIMIT 1",(ROOT_ID,)).fetchone()
                if body["revision"]!=(prior["revision"]+1 if prior else 1) or prior and prior["action"]=="revoke":
                    raise ValueError("terminal_case_root")
                db.execute("INSERT INTO skill_authority_roots VALUES (?,?,?,?,?,?)",
                    (ROOT_ID,body["revision"],body["action"],body["valid_from_ns"],body["valid_until_ns"],document))
        return dict(denied,recorded=True,reason="explicit_case_policy_recorded")
    except ERRORS:return dict(denied,reason="case_policy_unavailable")

def record_dispatch(context,approval_document):
    denied=dict(FALSE_TRUST,recorded=False,reason="case_source_disabled")
    if not enabled():return denied
    try:
        with deadline.limited():
            body=_statement(approval_document,RUN_FIELDS)
            with service.STORE._db_existing_write() as db:
                _budget(db);db.execute("BEGIN IMMEDIATE");_require_current_schema(db);now=time.time_ns()
                digest=_context(context,now);root=_root(db,context["revision"],now);_run(body,context)
                if context["dispatch_not_after_ns"]>root["valid_until_ns"]:raise ValueError("case_root_window")
                # Database partial UNIQUE(root,batch,case) is the final duplicate guard.
                db.execute("INSERT INTO skill_host_dispatches VALUES (?,?,?,?,?,?,?,?)",
                    (context["run_id"],context["nonce"],ROOT_ID,context["revision"],canonical(context).decode(),digest,approval_document,now))
        return dict(denied,recorded=True,reason="independent_case_dispatch_recorded")
    except ERRORS:return dict(denied,reason="case_dispatch_unavailable")

def verify_connection(db,*,case,record,now):
    if not db.in_transaction:raise ValueError("authority_transaction_required")
    _require_current_schema(db)
    if type(now) is not int or not 0<=now<=2**63-1:raise ValueError("server_time_required")
    cfg,code=registry.specification(case)
    if type(record) is not dict or set(record)!={"observation_document","stdout","stderr"}:raise ValueError("case_record_fields")
    stdout=record["stdout"]
    if type(stdout) is not str or len(stdout.encode())>500000 or record["stderr"]!="":raise ValueError("case_raw_budget")
    payload=_observation(record["observation_document"])
    if type(payload) is not dict or set(payload)!={"root_id","revision","observation"}:raise ValueError("case_payload")
    o=payload["observation"]
    if type(o) is not dict:raise ValueError("case_observation")
    row=db.execute("SELECT * FROM skill_host_dispatches WHERE run_id=? AND nonce=?",(o.get("run_id"),o.get("nonce"))).fetchone()
    if row is None or row["root_id"]!=ROOT_ID:raise ValueError("case_dispatch_missing")
    ctx=json.loads(row["context_json"],object_pairs_hook=unique,parse_constant=reject)
    digest=_context(ctx,row["registered_ns"]);root=_root(db,row["revision"],now)
    _run(_statement(row["approval_json"],RUN_FIELDS),ctx)
    if (ctx["case"]!=case or ctx["revision"]!=row["revision"] or digest!=row["context_sha256"]
            or not row["registered_ns"]<=now or ctx["dispatch_not_after_ns"]>root["valid_until_ns"]):
        raise ValueError("case_dispatch_changed")
    expected={k:ctx[k] for k in native.CONTEXT};expected.update(now_ns=now,registered_ns=row["registered_ns"])
    if not binding._inspect_binding(payload,expected,PROFILE+"."+case)["context_matched"]:raise ValueError("case_observation_mismatch")
    pins=registry.pins(case)
    request=canonical(dict(code=code,timeout_seconds=30,request_nonce=ctx["nonce"]))
    if (o["request_sha256"]!=hashlib.sha256(request).hexdigest()
            or any(o[k]!=v for k,v in pins.items())
            or o["stdout_sha256"]!=hashlib.sha256(stdout.encode()).hexdigest()
            or o["stderr_sha256"]!=hashlib.sha256(b"").hexdigest()):
        raise ValueError("case_request_or_output_changed")
    numeric=registry.assess(case,dict(code_sha256=o["code_sha256"],stdout=stdout,stderr="",
        exit_code=o["exit_code"],timed_out=o["timed_out"]))
    return dict(FALSE_TRUST,source_prerequisites_passed=True,numeric_pass=numeric["numeric_pass"],
        quality_level=numeric["quality_level"],revision=ctx["revision"],case=case,batch_id=ctx["batch_id"],run_id=ctx["run_id"],
        nonce=ctx["nonce"],manifest_sha256=manifest()["sha256"],numeric=numeric,
        reason="case_source_diagnostic_only_no_admission")

def inspect_case(*,case,record):
    denied=dict(FALSE_TRUST,source_prerequisites_passed=False,numeric_pass=False,quality_level="Q0",reason="case_source_disabled")
    if not enabled():return denied
    try:
        with deadline.limited():
            with service.STORE._db_existing_write() as db:
                _budget(db);db.execute("BEGIN IMMEDIATE")
                return verify_connection(db,case=case,record=record,now=time.time_ns())
    except ERRORS:return dict(denied,reason="case_source_unavailable")
