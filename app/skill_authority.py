"""Native signed owner policy and dispatch authorization, default closed.

No private keys, signing API, model, network or execution here. A public-root
approval alone cannot create a runtime approval. All runtime trust flags remain
false: this module validates owner authorization, not actual host observation.
"""
import hashlib
import ipaddress
import json
import re
import sqlite3
import time
import uuid
from app import config, skill_library_service
from scripts.validation import approved_host_root as approved
from scripts.validation import host_forward_binding as binding
from scripts.validation import executor_host_observation as docker
from scripts.validation.host_forward_signature import canonical,unique,reject
from scripts.validation import plane_wave_run_binding as pins
from scripts.validation.plane_wave_evidence import EXPECTED_CONFIGURATION

DOMAIN=b"AcouAgent/owner-authority/v1\0"
TRUST=dict(trusted_run=False,runtime_approved=False,run_bound=False,parameters_bound=False)
ROOT_FIELDS={"purpose","root_id","revision","action","profile","valid_from_ns","valid_until_ns"}
RUN_FIELDS={"purpose","root_id","revision","profile","run_id","nonce","context_sha256","code_sha256","configuration_sha256"}
CONTEXT=binding.CONTEXT-{"now_ns","registered_ns"}

def fixed_scope():
    return dict(code_sha256=pins.RECIPE_SHA256,
                configuration_sha256=hashlib.sha256(canonical(EXPECTED_CONFIGURATION)).hexdigest())

def _run_scope(body):
    if any(body[k]!=v for k,v in fixed_scope().items()):raise ValueError("fixed_profile_scope_changed")

def _document(raw,fields):
    if type(raw) is not str or len(raw.encode("utf-8"))>8192:raise ValueError("statement_budget")
    envelope=json.loads(raw,object_pairs_hook=unique,parse_constant=reject)
    if type(envelope) is not dict or set(envelope)!={"payload","signature"}:raise ValueError("statement_fields")
    payload=envelope["payload"];sig=envelope["signature"]
    if type(payload) is not dict or set(payload)!=fields:raise ValueError("payload_fields")
    if type(sig) is not str or not re.fullmatch("[0-9a-f]{128}",sig):raise ValueError("signature_format")
    if (payload["root_id"]!=approved.ROOT_ID or payload["profile"]!=approved.PROFILE
            or type(payload["revision"]) is not int or not 1<=payload["revision"]<=2147483647):
        raise ValueError("approved_scope_mismatch")
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    from cryptography.exceptions import InvalidSignature
    try:
        Ed25519PublicKey.from_public_bytes(approved.approved_public_key()).verify(bytes.fromhex(sig),DOMAIN+canonical(payload))
    except InvalidSignature:raise ValueError("invalid_owner_signature") from None
    return payload

def _root_policy(body,now):
    first,last=body["valid_from_ns"],body["valid_until_ns"]
    if (body["purpose"]!="root_lifecycle" or body["action"] not in {"approve","revoke"}
            or type(first) is not int or type(last) is not int
            or not 0<=first<=now<last<=2**63-1 or last-first>86400_000_000_000):
        raise ValueError("invalid_owner_window")

def _root_connection(db,revision,now):
    row=db.execute("SELECT * FROM skill_authority_roots WHERE root_id=? ORDER BY revision DESC LIMIT 1",
                   (approved.ROOT_ID,)).fetchone()
    if row is None or row["revision"]!=revision:raise ValueError("root_revision_unavailable")
    body=_document(row["statement_json"],ROOT_FIELDS)
    _root_policy(body,now)
    if (body["action"]!="approve" or body["purpose"]!="root_lifecycle"
            or not body["valid_from_ns"]<=now<body["valid_until_ns"]
            or any(row[k]!=body[k] for k in ("action","revision","valid_from_ns","valid_until_ns"))):
        raise ValueError("root_revoked_or_expired")
    return body

def record_root_statement(document):
    denied=dict(TRUST,recorded=False)
    if config.SKILL_LIBRARY_ENABLED is not True:return dict(denied,reason="authority_disabled")
    try:
        body=_document(document,ROOT_FIELDS)
        first,last=body["valid_from_ns"],body["valid_until_ns"]
        with skill_library_service.STORE._db_existing_write() as db:
            db.execute("BEGIN IMMEDIATE")
            _root_policy(body,time.time_ns())
            prior=db.execute("SELECT revision,action FROM skill_authority_roots WHERE root_id=? ORDER BY revision DESC LIMIT 1",
                             (approved.ROOT_ID,)).fetchone()
            if (body["revision"]!=(prior["revision"]+1 if prior else 1)
                    or prior and prior["action"]=="revoke"):
                raise ValueError("root_revision_or_terminal_revocation")
            db.execute("INSERT INTO skill_authority_roots VALUES (?,?,?,?,?,?)",
                       (body["root_id"],body["revision"],body["action"],first,last,document))
        return dict(denied,recorded=True,reason="signed_owner_policy_recorded")
    except (ValueError,TypeError,UnicodeError,OverflowError,RecursionError,sqlite3.Error,OSError,ImportError):
        return dict(denied,reason="authority_unavailable")

def _context(context,now,*,_root_id=None):
    if type(context) is not dict or set(context)!=CONTEXT:raise ValueError("dispatch_context_fields")
    expected_root=approved.ROOT_ID if _root_id is None else _root_id
    if type(expected_root) is not str or not re.fullmatch("[A-Za-z0-9_-]{1,64}",expected_root):
        raise ValueError("dispatch_root_namespace")
    if (context["root_id"]!=expected_root or type(context["revision"]) is not int
            or type(context["run_id"]) is not str or str(uuid.UUID(context["run_id"]))!=context["run_id"]
            or type(context["nonce"]) is not str or not re.fullmatch("[0-9a-f]{32}",context["nonce"])
            or type(context["target_port"]) is not int or not 1<=context["target_port"]<=65535):
        raise ValueError("dispatch_identity")
    if type(context["target_ip"]) is not str:raise ValueError("dispatch_route_type")
    ip=ipaddress.IPv4Address(context["target_ip"])
    if not (ip.is_private or ip.is_loopback) or ip.is_unspecified or ip.is_multicast or ip.is_reserved:
        raise ValueError("dispatch_route")
    identity={k:context[k] for k in ("container_id","image_id","started_at")}
    identity.update(running=True,restart_count=0)
    if not docker._valid(identity):raise ValueError("dispatch_container")
    first,last=context["dispatch_not_before_ns"],context["dispatch_not_after_ns"]
    if (type(first) is not int or type(last) is not int or not 0<=first<=now<=last<=2**63-1
            or last-first>90_000_000_000):raise ValueError("dispatch_window")
    raw=canonical(context)
    if len(raw)>2048:raise ValueError("dispatch_budget")
    return hashlib.sha256(raw).hexdigest()

def record_dispatch(context,approval_document,*,generation_reference=None):
    denied=dict(TRUST,recorded=False)
    if config.SKILL_LIBRARY_ENABLED is not True:return dict(denied,reason="authority_disabled")
    try:
        body=_document(approval_document,RUN_FIELDS)
        _run_scope(body)
        with skill_library_service.STORE._db_existing_write() as db:
            db.execute("BEGIN IMMEDIATE");now=time.time_ns()
            digest=_context(context,now)
            root=_root_connection(db,context["revision"],now)
            if (body["purpose"]!="approve_run" or body["revision"]!=context["revision"]
                    or body["run_id"]!=context["run_id"] or body["nonce"]!=context["nonce"]
                    or body["context_sha256"]!=digest
                    or context["dispatch_not_after_ns"]>root["valid_until_ns"]):
                raise ValueError("owner_run_approval_mismatch")
            db.execute("INSERT INTO skill_host_dispatches VALUES (?,?,?,?,?,?,?,?)",
                       (context["run_id"],context["nonce"],context["root_id"],context["revision"],
                        canonical(context).decode(),digest,approval_document,now))
            if generation_reference is not None:
                from app.skill_reference_execution import bind_connection
                bind_connection(db,context,generation_reference,now)
        return dict(denied,recorded=True,reason="signed_dispatch_approval_recorded")
    except (ValueError,TypeError,KeyError,UnicodeError,OverflowError,RecursionError,sqlite3.Error,OSError,ImportError):
        return dict(denied,reason="authority_unavailable")

def inspect_connection(db,run_id,nonce,now, *, recorded_evidence=False):
    """Caller must hold its authoritative BEGIN IMMEDIATE decision transaction."""
    row=db.execute("SELECT * FROM skill_host_dispatches WHERE run_id=? AND nonce=?",(run_id,nonce)).fetchone()
    if row is None:raise ValueError("authorized_dispatch_missing")
    context=json.loads(row["context_json"],object_pairs_hook=unique,parse_constant=reject)
    # Historical source evidence is checked at its immutable acceptance time;
    # current root revocation/expiry is still checked at consumption time.
    evidence_now=now
    if recorded_evidence:
        receipt=db.execute("SELECT registered_ns FROM skill_host_verifications WHERE run_id=? AND nonce=?",
                           (run_id,nonce)).fetchone()
        if receipt is None or type(receipt["registered_ns"]) is not int or not row["registered_ns"]<=receipt["registered_ns"]<=now:
            raise ValueError("recorded_evidence_time")
        evidence_now=receipt["registered_ns"]
    digest=_context(context,row["registered_ns"] if recorded_evidence else now)
    root=_root_connection(db,row["revision"],now)
    body=_document(row["approval_json"],RUN_FIELDS)
    _run_scope(body)
    if (body["purpose"]!="approve_run" or body["run_id"]!=run_id or body["nonce"]!=nonce
            or body["revision"]!=row["revision"] or digest!=row["context_sha256"]
            or body["context_sha256"]!=digest or row["root_id"]!=context["root_id"]
            or row["revision"]!=context["revision"]
            or not context["dispatch_not_before_ns"]<=row["registered_ns"]<=now
            or context["dispatch_not_after_ns"]>root["valid_until_ns"]):
        raise ValueError("authorized_dispatch_conflict")
    return dict(context,now_ns=evidence_now,registered_ns=row["registered_ns"])
