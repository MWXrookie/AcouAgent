"""Same-DB four-experiment provenance prerequisites, no skill admission.

A NEW explicit owner-signed namespace/profile policy is mandatory. The approved
fixed VAL-1 public root does not implicitly authorize this scope. Existing
append-only root/dispatch tables are reused with a distinct namespace; fixed
consumers reject these records. No keys/network/execution/DB migration here.
This verifies a batch; it neither records one-use skill consumption nor grants
Q3/Q4/active/reference code. Those require version/lifecycle/admission consumers.
"""
import hashlib
import json
import re
import sqlite3
import time
import uuid
from pathlib import Path
from app import config,skill_authority as native,skill_library_service as service
from app import controlled_consistency as numerical,skill_deadline as deadline
from app.skill_generation_decision import _budget
from scripts.validation import approved_host_root as approved,host_forward_binding as binding
from scripts.validation.host_forward_signature import canonical,unique,reject
from scripts.validation.host_forward_signature import DOMAIN as OBSERVATION_DOMAIN

ROOT_ID=approved.ROOT_ID+"_consistency"
PROFILE="consistency-sourcefree-v2"
DOMAIN=b"AcouAgent/consistency-owner/v1\x00"
ROOT_FIELDS={"purpose","root_id","profile","revision","action","valid_from_ns","valid_until_ns","manifest_sha256"}
RUN_FIELDS={"purpose","root_id","profile","revision","batch_id","case","run_id","nonce",
            "context_sha256","code_sha256","configuration_sha256","manifest_sha256"}
CONTEXT=native.CONTEXT|{"batch_id","case"}
FALSE_TRUST=dict(native.TRUST,actual_use_authenticated=False)


def manifest():
    recipes={}
    for case,args in numerical.PROFILES.items():
        recipes[case]=dict(code_sha256=hashlib.sha256(numerical.recipe(*args).encode()).hexdigest(),
            configuration_sha256=hashlib.sha256(canonical(numerical.configuration_for(case))).hexdigest())
    root=Path(__file__).resolve().parents[1]
    validators={name:hashlib.sha256((root/name).read_bytes()).hexdigest() for name in
        ("app/controlled_consistency.py","app/physics_consistency.py","app/physics_planar_initial.py","scripts/validation/consistency_experiment.py",
         "app/skill_consistency_source.py","app/skill_consistency_admission.py","app/skill_consistency_reference.py","app/skill_authority.py","scripts/validation/host_forward_binding.py")}
    body=dict(profile=PROFILE,recipes=recipes,validators=validators)
    return dict(body,sha256=hashlib.sha256(canonical(body)).hexdigest())


def _statement(raw,fields):
    if type(raw) is not str or len(raw.encode())>8192:raise ValueError("owner_statement_budget")
    envelope=json.loads(raw,object_pairs_hook=unique,parse_constant=reject)
    if type(envelope) is not dict or set(envelope)!={"payload","signature"}:
        raise ValueError("owner_statement_fields")
    body=envelope["payload"];signature=envelope["signature"]
    if (type(body) is not dict or set(body)!=fields or body["root_id"]!=ROOT_ID
            or body["profile"]!=PROFILE or type(body["revision"]) is not int
            or not 1<=body["revision"]<=2147483647
            or body["manifest_sha256"]!=manifest()["sha256"]
            or type(signature) is not str or not re.fullmatch("[0-9a-f]{128}",signature)):
        raise ValueError("explicit_consistency_scope_required")
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    from cryptography.exceptions import InvalidSignature
    try:
        Ed25519PublicKey.from_public_bytes(approved.approved_public_key()).verify(
            bytes.fromhex(signature),DOMAIN+canonical(body))
    except InvalidSignature:raise ValueError("invalid_consistency_owner_signature") from None
    return body


def _window(body,now):
    first,last=body["valid_from_ns"],body["valid_until_ns"]
    if (body["purpose"]!="consistency_root_lifecycle" or body["action"] not in {"approve","revoke"}
            or type(first) is not int or type(last) is not int
            or not 0<=first<=now<last<=2**63-1 or last-first>86400_000_000_000):
        raise ValueError("explicit_consistency_window")


def _root(db,revision,now):
    row=db.execute("SELECT * FROM skill_authority_roots WHERE root_id=? ORDER BY revision DESC LIMIT 1",
                   (ROOT_ID,)).fetchone()
    if row is None or row["revision"]!=revision:raise ValueError("consistency_root_revision")
    body=_statement(row["statement_json"],ROOT_FIELDS);_window(body,now)
    if body["action"]!="approve" or any(body[k]!=row[k] for k in
            ("revision","action","valid_from_ns","valid_until_ns")):
        raise ValueError("consistency_root_revoked")
    return body


def _require_current_schema(db):
    from app.skill_store import SCHEMA_VERSION
    from app.skill_authoring_schema import schema_ready
    versions=[row[0] for row in db.execute("SELECT version FROM schema_migrations ORDER BY version")]
    if versions!=list(range(1,SCHEMA_VERSION+1)) and not schema_ready(db):
        raise ValueError("explicit_schema_migration_required")


def record_policy(document):
    denied=dict(FALSE_TRUST,recorded=False,reason="consistency_source_disabled")
    if config.SKILL_LIBRARY_ENABLED is not True:return denied
    try:
        with deadline.limited():
            body=_statement(document,ROOT_FIELDS)
            with service.STORE._db_existing_write() as db:
                _budget(db);db.execute("BEGIN IMMEDIATE");_require_current_schema(db);_window(body,time.time_ns())
                prior=db.execute("SELECT revision,action FROM skill_authority_roots WHERE root_id=? ORDER BY revision DESC LIMIT 1",
                    (ROOT_ID,)).fetchone()
                if body["revision"]!=(prior["revision"]+1 if prior else 1) or prior and prior["action"]=="revoke":
                    raise ValueError("terminal_consistency_root")
                db.execute("INSERT INTO skill_authority_roots VALUES (?,?,?,?,?,?)",
                    (ROOT_ID,body["revision"],body["action"],body["valid_from_ns"],body["valid_until_ns"],document))
        return dict(denied,recorded=True,reason="explicit_consistency_policy_recorded")
    except (ValueError,TypeError,KeyError,UnicodeError,OverflowError,RecursionError,sqlite3.Error,OSError,ImportError):
        return dict(denied,reason="consistency_policy_unavailable")


def _context(context,now):
    if type(context) is not dict or set(context)!=CONTEXT:raise ValueError("consistency_context_fields")
    if (type(context["batch_id"]) is not str or str(uuid.UUID(context["batch_id"]))!=context["batch_id"]
            or type(context["case"]) is not str or context["case"] not in numerical.PROFILES):
        raise ValueError("consistency_batch_case")
    native._context({k:v for k,v in context.items() if k in native.CONTEXT},now,_root_id=ROOT_ID)
    return hashlib.sha256(canonical(context)).hexdigest()


def _run(body,context):
    expected=manifest()
    if (body["purpose"]!="approve_consistency_run"
            or any(body[k]!=context[k] for k in ("revision","run_id","nonce","batch_id","case"))
            or body["context_sha256"]!=hashlib.sha256(canonical(context)).hexdigest()
            or any(body[k]!=v for k,v in expected["recipes"][context["case"]].items())):
        raise ValueError("consistency_run_approval_scope")


def record_dispatch(context,approval_document):
    denied=dict(FALSE_TRUST,recorded=False,reason="consistency_source_disabled")
    if config.SKILL_LIBRARY_ENABLED is not True or config.SKILL_LEARNING_ENABLED is not True:return denied
    try:
        with deadline.limited():
            body=_statement(approval_document,RUN_FIELDS)
            with service.STORE._db_existing_write() as db:
                _budget(db);db.execute("BEGIN IMMEDIATE");_require_current_schema(db);now=time.time_ns()
                digest=_context(context,now);root=_root(db,context["revision"],now);_run(body,context)
                if context["dispatch_not_after_ns"]>root["valid_until_ns"]:raise ValueError("root_window")
                # Per-case batch uniqueness is read under the same writer lock.
                prior=db.execute("""SELECT 1 FROM skill_host_dispatches
                    WHERE root_id=? AND json_valid(context_json)
                    AND json_type(context_json,'$.batch_id')='text'
                    AND json_type(context_json,'$.case')='text'
                    AND json_extract(CASE WHEN json_valid(context_json) THEN context_json ELSE '{}' END,'$.batch_id')=?
                    AND json_extract(CASE WHEN json_valid(context_json) THEN context_json ELSE '{}' END,'$.case')=? LIMIT 1""",(ROOT_ID,context["batch_id"],context["case"])).fetchone()
                if prior:raise ValueError("consistency_case_already_dispatched")
                db.execute("INSERT INTO skill_host_dispatches VALUES (?,?,?,?,?,?,?,?)",
                    (context["run_id"],context["nonce"],ROOT_ID,context["revision"],
                     canonical(context).decode(),digest,approval_document,now))
        return dict(denied,recorded=True,reason="independent_consistency_dispatch_recorded")
    except (ValueError,TypeError,KeyError,UnicodeError,OverflowError,RecursionError,sqlite3.Error,OSError,ImportError):
        return dict(denied,reason="consistency_dispatch_unavailable")


def _observation(raw):
    if type(raw) is not str or len(raw.encode())>8192:raise ValueError("observation_budget")
    doc=json.loads(raw,object_pairs_hook=unique,parse_constant=reject)
    if type(doc) is not dict or set(doc)!={"payload","signature"}:raise ValueError("observation_fields")
    signature=doc["signature"]
    if type(signature) is not str or not re.fullmatch("[0-9a-f]{128}",signature):raise ValueError("observation_signature")
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    from cryptography.exceptions import InvalidSignature
    try:
        Ed25519PublicKey.from_public_bytes(approved.approved_public_key()).verify(
            bytes.fromhex(signature),OBSERVATION_DOMAIN+canonical(doc["payload"]))
    except InvalidSignature:raise ValueError("observation_signature") from None
    return doc["payload"]


def verify_connection(db,*,batch_id,records,now):
    """Hold one authoritative DB transaction. No write/one-use grant here."""
    if (type(batch_id) is not str or str(uuid.UUID(batch_id))!=batch_id
            or type(records) is not dict or set(records)!=set(numerical.PROFILES)):
        raise ValueError("complete_consistency_batch_required")
    if not db.in_transaction:raise ValueError("authority_transaction_required")
    _require_current_schema(db)
    if type(now) is not int or now<0:raise ValueError("server_time_required")
    raw={};source={};identities=[];windows=[];runs=set();revision=None
    pins=manifest()
    for case,args in numerical.PROFILES.items():
        deadline.check()
        record=records[case]
        if type(record) is not dict or set(record)!={"observation_document","stdout","stderr"}:
            raise ValueError("raw_signed_case_fields")
        if type(record["stdout"]) is not str or len(record["stdout"].encode())>500000 or record["stderr"]!="":
            raise ValueError("raw_signed_output_budget")
        payload=_observation(record["observation_document"])
        if type(payload) is not dict or set(payload)!={"root_id","revision","observation"}:
            raise ValueError("observed_case_payload")
        o=payload["observation"]
        if type(o) is not dict:raise ValueError("observed_case_payload")
        row=db.execute("SELECT * FROM skill_host_dispatches WHERE run_id=? AND nonce=?",
            (o.get("run_id"),o.get("nonce"))).fetchone()
        if row is None or row["root_id"]!=ROOT_ID:raise ValueError("independent_consistency_dispatch_missing")
        ctx=json.loads(row["context_json"],object_pairs_hook=unique,parse_constant=reject)
        digest=_context(ctx,row["registered_ns"]);_root(db,row["revision"],now)
        body=_statement(row["approval_json"],RUN_FIELDS);_run(body,ctx)
        if (digest!=row["context_sha256"] or ctx["batch_id"]!=batch_id or ctx["case"]!=case
                or ctx["revision"]!=row["revision"] or not row["registered_ns"]<=now
                or ctx["dispatch_not_after_ns"]>_root(db,row["revision"],now)["valid_until_ns"]):
            raise ValueError("consistency_dispatch_changed")
        if revision is None:revision=row["revision"]
        if revision!=row["revision"] or o["run_id"] in runs:raise ValueError("mixed_consistency_authority")
        runs.add(o["run_id"])
        expected={k:v for k,v in ctx.items() if k in native.CONTEXT}
        expected.update(now_ns=now,registered_ns=row["registered_ns"])
        checked=binding.inspect_consistency_binding(payload,expected,case)
        if not checked["context_matched"]:raise ValueError("independent_consistency_observation_mismatch")
        recipe=numerical.recipe(*args)
        request=canonical(dict(code=recipe,timeout_seconds=30,request_nonce=ctx["nonce"]))
        if (o["exit_code"]!=0 or o["timed_out"] is not False
                or o["request_sha256"]!=hashlib.sha256(request).hexdigest()
                or o["code_sha256"]!=pins["recipes"][case]["code_sha256"]
                or o["configuration_sha256"]!=pins["recipes"][case]["configuration_sha256"]
                or o["stdout_sha256"]!=hashlib.sha256(record["stdout"].encode()).hexdigest()
                or o["stderr_sha256"]!=hashlib.sha256(b"").hexdigest()):
            raise ValueError("observed_consistency_recipe_or_output_changed")
        identities.append((ctx["target_ip"],ctx["target_port"],ctx["container_id"],ctx["image_id"],ctx["started_at"]))
        windows.extend([o["observed_before_ns"],o["observed_after_ns"]])
        raw[case]=dict(code_sha256=o["code_sha256"],stdout=record["stdout"])
        source[case]=dict(run_id=ctx["run_id"],nonce=ctx["nonce"],
            observation_sha256=hashlib.sha256(canonical(payload)).hexdigest(),
            stdout_sha256=o["stdout_sha256"])
    if len(set(identities))!=1 or max(windows)-min(windows)>240_000_000_000:
        raise ValueError("mixed_target_or_consistency_window")
    numeric=numerical.assess_with_analytic_reference(raw)
    return dict(FALSE_TRUST,source_prerequisites_passed=True,
        numeric_pass=numeric["numeric_pass"],quality_level=numeric["quality_level"],
        batch_id=batch_id,source=source,manifest_sha256=pins["sha256"],numeric=numeric,
        reason="bound_consistency_prerequisites_only_no_skill_admission")


def inspect_batch(*,batch_id,records):
    denied=dict(FALSE_TRUST,source_prerequisites_passed=False,numeric_pass=False,
        quality_level="Q0",reason="consistency_source_disabled")
    if config.SKILL_LIBRARY_ENABLED is not True or config.SKILL_LEARNING_ENABLED is not True:return denied
    try:
        with deadline.limited():
            with service.STORE._db_existing_write() as db:
                _budget(db);db.execute("BEGIN IMMEDIATE")
                return verify_connection(db,batch_id=batch_id,records=records,now=time.time_ns())
    except (ValueError,TypeError,KeyError,IndexError,UnicodeError,OverflowError,RecursionError,sqlite3.Error,OSError,ImportError):
        return dict(denied,reason="consistency_source_unavailable")
