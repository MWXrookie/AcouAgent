"""Server-held public evidence for repeated exact-profile generation.

No secrets, arbitrary stdout/stderr logs, execution or public learning tool.
Registration revalidates signed policy/source under one authority transaction.
Retrieval is a bounded read; the consumer repeats authorization before handoff.
"""
import hashlib
import json
import math
import sqlite3
import time
from app import skill_generation_decision as decisions,skill_reference_consumer as consumer
from app import skill_library_service as service,skill_retrieval,skill_host_intake as intake
from scripts.validation.host_forward_signature import canonical,unique,reject

PROOF_FIELDS={"skill_id","version","approval_document","observation_document","stdout","stderr"}

def _safe_trace(stdout,stderr):
    if type(stdout) is not str or len(stdout.encode())>500000 or stderr!="":
        raise ValueError("only_structured_trace_without_error_logs")
    raw=json.loads(stdout,object_pairs_hook=unique,parse_constant=reject)
    if type(raw) is not dict or "raw_evidence" not in raw or not set(raw)<={"raw_evidence","measured","theory","err_pct","status"}:
        raise ValueError("trace_fields")
    evidence=raw["raw_evidence"]
    if type(evidence) is not dict or set(evidence)!={"profile","configuration","units","time","pressure_x1","pressure_x2"}:
        raise ValueError("raw_trace_fields")
    if evidence["profile"]!=intake.approved.PROFILE:
        raise ValueError("trace_profile")
    allowed={"measured":{"peak_x1","peak_x2","ratio_x2_x1"},
             "theory":{"peak","ratio"},"err_pct":{"peak_x1","peak_x2","ratio"}}
    for key,fields in allowed.items():
        if key in raw and (type(raw[key]) is not dict or not set(raw[key])<=fields
                or any(type(v) not in (int,float) or not math.isfinite(v) for v in raw[key].values())):
            raise ValueError("numeric_summary_only")
    if "status" in raw and raw["status"] not in {"PASS","FAIL"}:
        raise ValueError("bounded_status")
    # Exact configuration, units, finite arrays/length/time axis are checked by
    # the pinned validator again inside verify_connection, before persistence.

def register(proof):
    denied=dict(recorded=False,reason="reference_registry_disabled")
    if not decisions.enabled():return denied
    try:
        if type(proof) is not dict or set(proof)!=PROOF_FIELDS:
            raise ValueError("capsule_fields")
        _safe_trace(proof["stdout"],proof["stderr"])
        body=consumer._approval(proof["approval_document"])
        if body["purpose"]!="approve_skill_reference_policy":
            raise ValueError("repeated_policy_required")
        store=service.STORE
        with store._db_existing_write() as db:
            decisions._budget(db);db.execute("BEGIN IMMEDIATE");now=time.time_ns()
            checked=intake.verify_connection(store,db,skill_id=proof["skill_id"],version=proof["version"],
                observation_document=proof["observation_document"],stdout=proof["stdout"],stderr="",
                now=now,recorded_evidence=True,require_independent_root=True)
            first,last=body["valid_from_ns"],body["valid_until_ns"]
            ps=hashlib.sha256(canonical(checked["parameters"])).hexdigest()
            expected=dict(skill_id=proof["skill_id"],skill_version=proof["version"],parameters_sha256=ps,
                observation_sha256=hashlib.sha256(canonical(checked["payload"])).hexdigest(),
                validator_sha256=intake.pins.VALIDATOR_SHA256,root_id=checked["expected"]["root_id"],
                revision=checked["expected"]["revision"])
            consumer.authority._run_scope(body)
            if (type(first) is not int or type(last) is not int
                    or not 0<=first<=now<last<=2**63-1 or last-first>86400_000_000_000
                    or any(type(body[k]) is not type(v) or body[k]!=v for k,v in expected.items())):
                raise ValueError("capsule_policy_scope")
            decisions._budget(db)
            receipt=db.execute("SELECT * FROM skill_host_verifications WHERE run_id=? AND nonce=?",
                               (checked["run"],checked["nonce"])).fetchone()
            if (receipt is None or receipt["skill_id"]!=proof["skill_id"] or receipt["version"]!=proof["version"]
                    or receipt["observation_sha256"]!=expected["observation_sha256"]
                    or receipt["stdout_sha256"]!=intake.pins.digest(proof["stdout"])
                    or receipt["parameters_sha256"]!=ps or receipt["validator_sha256"]!=intake.pins.VALIDATOR_SHA256):
                raise ValueError("capsule_receipt_mismatch")
            life=db.execute("SELECT status,quality_level,archived,scenario_type FROM simulation_skills WHERE skill_id=?",
                            (proof["skill_id"],)).fetchone()
            if life is None or life["status"]!="active" or life["quality_level"]!="Q4" or life["archived"] or body["scenario_type"]!=life["scenario_type"]:
                raise ValueError("capsule_lifecycle")
            saved=dict(proof,observation_document=proof["observation_document"].decode())
            payload=canonical(saved).decode()
            if len(payload.encode())>600000:raise ValueError("capsule_budget")
            digest=hashlib.sha256(payload.encode()).hexdigest()
            policy=hashlib.sha256(canonical(json.loads(proof["approval_document"],object_pairs_hook=unique,parse_constant=reject))).hexdigest()
            decisions._budget(db)
            prior=db.execute("SELECT capsule_sha256 FROM skill_reference_capsules WHERE policy_sha256=?",(policy,)).fetchone()
            if prior:
                if prior[0]!=digest:raise ValueError("capsule_conflict")
                return dict(denied,reason="reference_capsule_already_recorded",duplicate=True)
            db.execute("INSERT INTO skill_reference_capsules VALUES (?,?,?,?,?,?,?,?,?)",
                (policy,proof["skill_id"],proof["version"],ps,checked["run"],last,now,payload,digest))
        return dict(recorded=True,reason="public_reference_capsule_recorded",policy_sha256=policy)
    except (ValueError,TypeError,KeyError,IndexError,UnicodeError,OverflowError,RecursionError,sqlite3.Error,OSError,ImportError):
        return dict(denied,reason="reference_registry_unavailable")

def find(scenario_type,parameters_json):
    """Metadata read only; returning a capsule never grants authorization."""
    if not decisions.enabled():return None
    try:
        params=skill_retrieval._request(scenario_type,parameters_json,3)
        ps=hashlib.sha256(canonical(params)).hexdigest()
        store=service.STORE
        with store._read_db() as db:
            for candidate in skill_retrieval._shortlist(store,db,scenario_type,params,3):
                row=db.execute("""SELECT substr(capsule_json,1,600001) capsule_json,capsule_sha256
                    FROM skill_reference_capsules WHERE skill_id=? AND version=? AND parameters_sha256=?
                    AND valid_until_ns>? ORDER BY registered_ns DESC LIMIT 1""",
                    (candidate["skill_id"],candidate["version"],ps,time.time_ns())).fetchone()
                if row is None:continue
                raw=row["capsule_json"]
                if len(raw.encode())>600000 or hashlib.sha256(raw.encode()).hexdigest()!=row["capsule_sha256"]:
                    continue
                proof=json.loads(raw,object_pairs_hook=unique,parse_constant=reject)
                if type(proof) is not dict or set(proof)!=PROOF_FIELDS:continue
                proof["observation_document"]=proof["observation_document"].encode()
                return proof
    except (ValueError,TypeError,KeyError,UnicodeError,OverflowError,RecursionError,sqlite3.Error,OSError):
        pass
    return None

def verified_metadata_connection(store,db,candidate,scenario_type,parameters,now):
    """Read-only source metadata; never a generation or execution authorization."""
    ps=hashlib.sha256(canonical(parameters)).hexdigest()
    row=db.execute("""SELECT substr(capsule_json,1,600001) capsule_json,capsule_sha256,policy_sha256
        FROM skill_reference_capsules WHERE skill_id=? AND version=? AND parameters_sha256=?
        AND valid_until_ns>? ORDER BY registered_ns DESC LIMIT 1""",
        (candidate["skill_id"],candidate["version"],ps,now)).fetchone()
    if row is None:return None
    raw=row["capsule_json"]
    if len(raw.encode())>600000 or hashlib.sha256(raw.encode()).hexdigest()!=row["capsule_sha256"]:
        raise ValueError("metadata_capsule_hash")
    proof=json.loads(raw,object_pairs_hook=unique,parse_constant=reject)
    if type(proof) is not dict or set(proof)!=PROOF_FIELDS:raise ValueError("metadata_fields")
    if proof["skill_id"]!=candidate["skill_id"] or type(proof["version"]) is not int or proof["version"]!=candidate["version"]:
        raise ValueError("metadata_identity")
    _safe_trace(proof["stdout"],proof["stderr"])
    body=consumer._approval(proof["approval_document"])
    policy_sha=hashlib.sha256(canonical(json.loads(proof["approval_document"],object_pairs_hook=unique,parse_constant=reject))).hexdigest()
    if policy_sha!=row["policy_sha256"]:raise ValueError("metadata_policy_hash")
    consumer.authority._run_scope(body)
    first,last=body.get("valid_from_ns"),body.get("valid_until_ns")
    if (body["purpose"]!="approve_skill_reference_policy" or type(first) is not int or type(last) is not int
            or not 0<=first<=now<last<=2**63-1 or last-first>86400_000_000_000):
        raise ValueError("metadata_policy_time")
    checked=intake.verify_connection(store,db,skill_id=candidate["skill_id"],version=candidate["version"],
        observation_document=proof["observation_document"].encode(),stdout=proof["stdout"],stderr="",
        now=now,recorded_evidence=True,require_independent_root=True)
    expected=dict(skill_id=candidate["skill_id"],skill_version=candidate["version"],scenario_type=scenario_type,
        parameters_sha256=ps,observation_sha256=hashlib.sha256(canonical(checked["payload"])).hexdigest(),
        root_id=checked["expected"]["root_id"],revision=checked["expected"]["revision"],
        validator_sha256=intake.pins.VALIDATOR_SHA256)
    if checked["parameters"]!=parameters or any(type(body[k]) is not type(v) or body[k]!=v for k,v in expected.items()):
        raise ValueError("metadata_policy_scope")
    receipt=db.execute("SELECT * FROM skill_host_verifications WHERE run_id=? AND nonce=?",
        (checked["run"],checked["nonce"])).fetchone()
    if (receipt is None or receipt["skill_id"]!=candidate["skill_id"] or receipt["version"]!=candidate["version"]
            or receipt["code_sha256"]!=intake.pins.RECIPE_SHA256 or receipt["parameters_sha256"]!=ps
            or receipt["observation_sha256"]!=expected["observation_sha256"]
            or receipt["stdout_sha256"]!=intake.pins.digest(proof["stdout"])
            or receipt["validator_sha256"]!=intake.pins.VALIDATOR_SHA256):
        raise ValueError("metadata_receipt")
    life=db.execute("SELECT status,quality_level,archived,scenario_type FROM simulation_skills WHERE skill_id=?",
        (candidate["skill_id"],)).fetchone()
    if life is None or life["status"]!="active" or life["quality_level"]!="Q4" or life["archived"] or life["scenario_type"]!=scenario_type:
        raise ValueError("metadata_lifecycle")
    return dict(skill_id=candidate["skill_id"],version=candidate["version"],scenario_type=scenario_type,
        scope=intake.approved.PROFILE,parameters_sha256=ps,code_sha256=intake.pins.RECIPE_SHA256,
        source_run_id=checked["run"],quality_level="Q4",status="active")
