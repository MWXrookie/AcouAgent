"""Internal, default-off fixed-profile reference handoff.

Not a public search tool, automatic promotion, generic scene validator, or proof
of actual model/executor/user use. A handoff requires an explicit owner signature
for this generation decision plus fresh signed raw host evidence. Production
activation and five-class VAL-1 acceptance remain separate requirements.
"""
import hashlib
import re
import json
import sqlite3
import time
import uuid
from contextlib import nullcontext
from app import skill_generation_decision as decisions,skill_authority as authority
from app import skill_host_intake as intake,skill_library_service as service,skill_retrieval
from scripts.validation.host_forward_signature import canonical
from scripts.validation import plane_wave_run_binding as pins

FIELDS=authority.RUN_FIELDS-{"run_id","nonce","context_sha256"}|{
    "decision_id","decision_nonce","generation_run_id","skill_id","skill_version",
    "parameters_sha256","generation_context_sha256","observation_sha256",
    "validator_sha256","expires_ns","scenario_type"}

POLICY_FIELDS=authority.RUN_FIELDS-{"run_id","nonce","context_sha256"}|{
    "skill_id","skill_version","parameters_sha256","observation_sha256",
    "validator_sha256","valid_from_ns","valid_until_ns","scenario_type"}

def _approval(document):
    # Parse only enough to select a signature schema; both paths then verify
    # exact field sets and the approved root signature.
    from scripts.validation.host_forward_signature import unique,reject
    if type(document) is not str or len(document.encode())>8192:
        raise ValueError("reference_policy_budget")
    envelope=json.loads(document,object_pairs_hook=unique,parse_constant=reject)
    purpose=envelope["payload"]["purpose"]
    fields=POLICY_FIELDS if purpose=="approve_skill_reference_policy" else FIELDS
    return authority._document(document,fields)

def consume(*,decision_id,nonce,generation_run_id,scenario_type,parameters_json,
            original_context,approval_document,skill_id,version,
            observation_document,stdout,stderr):
    denied=dict(decisions.FALSE_TRUST,context=original_context,skills=[],
                fallback=True,consumed=False,reason="reference_consumer_disabled")
    if not decisions.enabled():return denied
    try:
        if (type(decision_id) is not str or str(uuid.UUID(decision_id))!=decision_id
                or type(generation_run_id) is not str or str(uuid.UUID(generation_run_id))!=generation_run_id
                or type(nonce) is not str or len(nonce)!=32
                or type(skill_id) is not str or not 1<=len(skill_id)<=128
                or type(version) is not int or version<1):
            raise ValueError("reference_identity")
        params,param_sha,context_sha=decisions._request(scenario_type,parameters_json,original_context)
        body=_approval(approval_document)
        authority._run_scope(body)
        store=service.STORE
        with store._db_existing_write() as db:
            decisions._budget(db);db.execute("BEGIN IMMEDIATE");now=time.time_ns()
            row=db.execute("SELECT * FROM skill_generation_decisions WHERE decision_id=?",(decision_id,)).fetchone()
            if (row is None or row["nonce"]!=nonce or row["scenario_type"]!=scenario_type
                    or row["parameters_sha256"]!=param_sha or row["context_sha256"]!=context_sha
                    or not row["created_ns"]<=now<row["expires_ns"]):
                raise ValueError("decision_context_or_expiry")
            if db.execute("SELECT 1 FROM skill_generation_consumptions WHERE decision_id=? OR generation_run_id=?",
                          (decision_id,generation_run_id)).fetchone():
                raise ValueError("decision_already_consumed")
            expected=dict(skill_id=skill_id,skill_version=version,scenario_type=scenario_type,parameters_sha256=param_sha,
                          validator_sha256=pins.VALIDATOR_SHA256)
            if body["purpose"]=="approve_skill_reference_policy":
                first,last=body["valid_from_ns"],body["valid_until_ns"]
                if (type(first) is not int or type(last) is not int
                        or not 0<=first<=now<last<=2**63-1 or last-first>86400_000_000_000):
                    raise ValueError("reference_policy_expiry")
            else:
                expected.update(purpose="approve_generation_reference",decision_id=decision_id,
                    decision_nonce=nonce,generation_run_id=generation_run_id,generation_context_sha256=context_sha)
                if type(body["expires_ns"]) is not int or not now<body["expires_ns"]<=row["expires_ns"]:
                    raise ValueError("specific_reference_expiry")
            if any(type(body[k]) is not type(v) or body[k]!=v for k,v in expected.items()):
                raise ValueError("specific_reference_approval")
            checked=intake.verify_connection(store,db,skill_id=skill_id,version=version,
                observation_document=observation_document,stdout=stdout,stderr=stderr,now=now,recorded_evidence=True,require_independent_root=True)
            if (scenario_type not in {"plane_wave_2d","initial_pressure_2d"} or params!=checked["parameters"]
                    or body["root_id"]!=checked["expected"]["root_id"]
                    or body["revision"]!=checked["expected"]["revision"]
                    or body["observation_sha256"]!=hashlib.sha256(canonical(checked["payload"])).hexdigest()):
                raise ValueError("reference_scope")
            # A source check does not promote a candidate. Only a separately
            # established active Q4 record can enter this fixed-profile path.
            life=db.execute("SELECT status,quality_level,archived,scenario_type FROM simulation_skills WHERE skill_id=?",
                            (skill_id,)).fetchone()
            if life is None or life["status"]!="active" or life["quality_level"]!="Q4" or life["archived"] or life["scenario_type"]!=scenario_type:
                raise ValueError("reference_lifecycle")
            receipt=db.execute("SELECT * FROM skill_host_verifications WHERE skill_id=? AND version=? AND run_id=? AND nonce=?",
                (skill_id,version,checked["run"],checked["nonce"])).fetchone()
            if (receipt is None or receipt["code_sha256"]!=pins.RECIPE_SHA256
                    or receipt["observation_sha256"]!=body["observation_sha256"]
                    or receipt["stdout_sha256"]!=pins.digest(stdout)
                    or receipt["validator_sha256"]!=pins.VALIDATOR_SHA256
                    or receipt["parameters_sha256"]!=param_sha):
                raise ValueError("source_receipt_mismatch")
            decisions._budget(db)
            current=skill_retrieval._shortlist(store,db,scenario_type,params,3)
            snapshot=json.dumps(current,sort_keys=True,separators=(",",":"))
            if snapshot!=row["metadata_snapshot_json"] or {"skill_id":skill_id,"version":version} not in current:
                raise ValueError("reference_snapshot_changed")
            reference=dict(skill_id=skill_id,version=version,code=checked["code"],
                           code_sha256=pins.RECIPE_SHA256,source_run_id=checked["run"])
            context=original_context+"\n\nValidated fixed-profile reference (parameters must remain unchanged):\n"+canonical(reference).decode()
            if len(context.encode())>90000:raise ValueError("reference_context_budget")
            audit=dict(reference={k:v for k,v in reference.items() if k!="code"},
                approval_sha256=hashlib.sha256(approval_document.encode()).hexdigest(),
                source_observation_sha256=body["observation_sha256"],
                context_sha256=hashlib.sha256(context.encode()).hexdigest(),
                actual_use_authenticated=False)
            db.execute("INSERT INTO skill_generation_consumptions VALUES (?,?,?,?,?)",
                (decision_id,generation_run_id,now,canonical(audit).decode(),"fixed_profile_reference_handoff"))
        # Code is handed off only after the transaction commits. No execution.
        return dict(denied,context=context,skills=[reference],fallback=False,consumed=True,
            reason="fixed_profile_reference_handoff",audit_kind="generation_reference_handoff",
            trusted_run=True,runtime_approved=True,run_bound=True,parameters_bound=True,
            actual_use_authenticated=False,scope="val1-plane-wave-v2")
    except (ValueError,TypeError,KeyError,IndexError,UnicodeError,OverflowError,RecursionError,
            sqlite3.Error,OSError,ImportError):
        return dict(denied,reason="reference_authorization_unavailable")

def read_committed_context(*,decision_id,generation_run_id,scenario_type,parameters_json,original_context,
                           _db=None,_decision_at=None):
    """Read-only pointer resolution, not caller JSON/flags authorization.

    Used as a second workflow step so a forged tool result or stale descriptor
    cannot authorize injection. Returns only a newly reverified context string.
    """
    if not decisions.enabled():return original_context
    if _db is None:
        from app import skill_local_profile
        local=skill_local_profile.read_context(decision_id=decision_id,generation_run_id=generation_run_id,
            scenario_type=scenario_type,parameters_json=parameters_json,original_context=original_context)
        if local!=original_context:return local
    try:
        if (type(original_context) is not str or len(original_context.encode())>65536
                or type(decision_id) is not str or str(uuid.UUID(decision_id))!=decision_id
                or type(generation_run_id) is not str or str(uuid.UUID(generation_run_id))!=generation_run_id):
            raise ValueError("handoff_pointer")
        params,ps,cs=decisions._request(scenario_type,parameters_json,original_context)
        store=service.STORE
        with (nullcontext(_db) if _db is not None else store._read_db()) as db:
            now=time.time_ns()
            decision_at=now if _decision_at is None else _decision_at
            if type(decision_at) is not int or not 0<=decision_at<=now:raise ValueError('decision_time')
            row=db.execute("""SELECT d.*,c.metadata_current_json,c.fallback_reason result_reason
                FROM skill_generation_decisions d JOIN skill_generation_consumptions c
                ON c.decision_id=d.decision_id WHERE d.decision_id=? AND c.generation_run_id=?""",
                (decision_id,generation_run_id)).fetchone()
            if (row is None or row["result_reason"]!="fixed_profile_reference_handoff"
                    or row["scenario_type"]!=scenario_type or row["parameters_sha256"]!=ps
                    or row["context_sha256"]!=cs or not row["created_ns"]<=decision_at<row["expires_ns"]):
                raise ValueError("committed_reference_scope")
            from scripts.validation.host_forward_signature import unique,reject
            if len(row["metadata_current_json"].encode())>4096:raise ValueError("handoff_audit_budget")
            audit=json.loads(row["metadata_current_json"],object_pairs_hook=unique,parse_constant=reject)
            if (type(audit) is not dict or set(audit)!={"reference","approval_sha256",
                    "source_observation_sha256","context_sha256","actual_use_authenticated"}
                    or audit["actual_use_authenticated"] is not False):
                raise ValueError("handoff_audit_fields")
            reference=audit["reference"]
            if (type(reference) is not dict or set(reference)!={"skill_id","version","code_sha256","source_run_id"}
                    or reference["code_sha256"]!=pins.RECIPE_SHA256):
                raise ValueError("handoff_reference")
            current=skill_retrieval._shortlist(store,db,scenario_type,params,3)
            if {"skill_id":reference["skill_id"],"version":reference["version"]} not in current:
                raise ValueError("handoff_lifecycle_changed")
            capsules=db.execute("""SELECT substr(capsule_json,1,600001) capsule_json,capsule_sha256
                FROM skill_reference_capsules WHERE skill_id=? AND version=? AND parameters_sha256=?
                AND valid_until_ns>? ORDER BY registered_ns DESC LIMIT 3""",
                (reference["skill_id"],reference["version"],ps,now)).fetchall()
            for entry in capsules:
                raw=entry["capsule_json"]
                if len(raw.encode())>600000 or hashlib.sha256(raw.encode()).hexdigest()!=entry["capsule_sha256"]:
                    continue
                proof=json.loads(raw,object_pairs_hook=unique,parse_constant=reject)
                if hashlib.sha256(proof["approval_document"].encode()).hexdigest()!=audit["approval_sha256"]:
                    continue
                body=_approval(proof["approval_document"])
                if body["purpose"]!="approve_skill_reference_policy":continue
                first,last=body["valid_from_ns"],body["valid_until_ns"]
                if (type(first) is not int or type(last) is not int or not 0<=first<=now<last<=2**63-1
                        or last-first>86400_000_000_000
                        or body["skill_id"]!=reference["skill_id"]
                        or type(body["skill_version"]) is not int or body["skill_version"]!=reference["version"]
                        or body["scenario_type"]!=scenario_type or body["parameters_sha256"]!=ps
                        or body["validator_sha256"]!=pins.VALIDATOR_SHA256):
                    continue
                authority._run_scope(body)
                checked=intake.verify_connection(store,db,skill_id=reference["skill_id"],version=reference["version"],
                    observation_document=proof["observation_document"].encode(),stdout=proof["stdout"],
                    stderr=proof["stderr"],now=now,recorded_evidence=True,require_independent_root=True)
                digest=hashlib.sha256(canonical(checked["payload"])).hexdigest()
                if (checked["run"]!=reference["source_run_id"] or checked["parameters"]!=params
                        or digest!=audit["source_observation_sha256"] or digest!=body["observation_sha256"]
                        or body["root_id"]!=checked["expected"]["root_id"] or body["revision"]!=checked["expected"]["revision"]):
                    continue
                life=db.execute("SELECT status,quality_level,archived,scenario_type FROM simulation_skills WHERE skill_id=?",
                                (reference["skill_id"],)).fetchone()
                if life is None or life["status"]!="active" or life["quality_level"]!="Q4" or life["archived"] or life["scenario_type"]!=scenario_type:
                    continue
                capsule_reference=dict(reference,code=checked["code"])
                context=original_context+"\n\nValidated fixed-profile reference (parameters must remain unchanged):\n"+canonical(capsule_reference).decode()
                if len(context.encode())<=90000 and hashlib.sha256(context.encode()).hexdigest()==audit["context_sha256"]:
                    return context
    except (ValueError,TypeError,KeyError,IndexError,UnicodeError,OverflowError,RecursionError,
            sqlite3.Error,OSError,ImportError):
        pass
    return original_context
