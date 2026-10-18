"""Managed local executor exact-profile skills; no user signing setup.

Trust boundary: gateway, its server-selected SQLite history and internal
authenticated executor. Not cross-host attestation or user identity. Only the
reviewed fixed recipe and raw analytic checks can enter this basic profile.
"""
import hashlib
import json
import sqlite3
import time
import uuid
from pathlib import Path
from app import config,dashboard,executor_evidence,skill_library_service as service
from app import skill_generation_decision as decisions,controlled_dispatch,skill_local_catalog as catalog
from app.skill_store import skill_fingerprint
from scripts.validation import plane_wave_run_binding as pins
from scripts.validation.plane_wave_evidence import validate_plane_wave
from scripts.validation.host_forward_signature import canonical,unique,reject

PROFILE="managed-local-plane-wave.v1"
SCENE="initial_pressure_2d"
EXECUTOR_SOURCE_SHA256="9d528fc5b30938c7fd656c8efcdec458acfefcb66c1e88e7fd58a137e7ce4a4b"
ERRORS=(ValueError,TypeError,KeyError,IndexError,UnicodeError,OverflowError,RecursionError,sqlite3.Error,OSError,ImportError)

def _json(raw):
    return json.loads(raw,object_pairs_hook=unique,parse_constant=reject)

def _recipe(scope=PROFILE):
    return catalog.recipe(scope)

def _capsule_checked(capsule):
    if type(capsule) is not dict or set(capsule)!={"stdout","receipt","dispatch"}:
        raise ValueError("local_capsule_fields")
    if type(capsule["receipt"]) is not dict:raise ValueError("local_missing_receipt")
    raw=capsule["stdout"]
    if type(raw) is not str or len(raw.encode())>500000:raise ValueError("local_raw_budget")
    from app.skill_reference_registry import _safe_trace
    decoded=_json(raw)
    spec=catalog.from_raw(decoded);code=_recipe(spec["scope"])
    # The pinned executable emits this fixed display label; arbitrary text
    # remains forbidden and the receipt still binds the unchanged raw output.
    if "case" in decoded:
        if decoded["case"]!="1 平面波振幅守恒":raise ValueError("local_case_label")
        decoded=dict(decoded);decoded.pop("case")
    if spec["scope"]==PROFILE:
        _safe_trace(canonical(decoded).decode(),"")
        numeric=validate_plane_wave(_json(raw))
    elif spec["scope"] in catalog.PERIODIC_RECIPES:
        from scripts.validation.periodic_eigenmodes import validate
        numeric=validate(decoded)
    else:
        from scripts.validation.interface_waveform import validate_wide
        numeric=validate_wide(raw)
    if numeric.get("numeric_pass") is not True:raise ValueError("local_physics")
    dispatch=capsule["dispatch"]
    nonce=dispatch["request_nonce"]
    request=dict(code=code,timeout_seconds=30,request_nonce=nonce)
    expected=dict(version="local-dispatch.v1",source_kind="managed_executor_transport",
        request_sha256=pins.digest(canonical(request).decode()),code_sha256=spec["code_sha256"],
        request_nonce=nonce,timeout_seconds=30,target_url="http://jwave-executor:8010/execute",
        trust_boundary="gateway_and_internal_executor",independent_host_authenticated=False,
        receipt_correlated=capsule["receipt"].get("version")=="executor-receipt.v1")
    if type(dispatch) is not dict or set(dispatch)!=set(expected) or any(type(dispatch[k]) is not type(v) or dispatch[k]!=v for k,v in expected.items()):
        raise ValueError("local_dispatch_context")
    if capsule["receipt"].get("version")=="gateway-transport-receipt.v1":
        expected_receipt=dict(version="gateway-transport-receipt.v1",
            request_sha256=expected["request_sha256"],request_nonce=nonce,
            stdout_sha256=pins.digest(raw),stderr_sha256=pins.digest(""),
            exit_code=0,timed_out=False,executor_receipt_absent=True,
            authentication_configured=True,independent_host_authenticated=False)
        receipt=capsule["receipt"]
        if (set(receipt)!=set(expected_receipt)
                or any(type(receipt[k]) is not type(v) or receipt[k]!=v for k,v in expected_receipt.items())):
            raise ValueError("local_transport_receipt")
        # This authenticates only the managed internal transport boundary.
        # It makes no executor-side nonce or runtime/image attestation claim.
        return code,numeric
    checked=executor_evidence.verify_executor_receipt(capsule["receipt"],
        dict(stdout=raw,stderr="",exit_code=0,timed_out=False),code,30,nonce)
    if checked["matched"] is not True:raise ValueError("local_receipt")
    runtime=checked["receipt"]["runtime"]
    if runtime["executor_source_sha256"]!=EXECUTOR_SOURCE_SHA256 or runtime["library_versions"]["jwave"]!="0.2.1":
        raise ValueError("local_executor_build")
    return code,numeric

def _history(run_id):
    if type(run_id) is not str or not 1<=len(run_id)<=128:raise ValueError("local_run")
    path=Path(dashboard.DB_PATH).resolve()
    with sqlite3.connect(path.as_uri()+"?mode=ro",uri=True,timeout=1) as db:
        db.row_factory=sqlite3.Row
        db.set_progress_handler(lambda:1,1000000)
        rows=db.execute("""SELECT record_version,substr(code,1,20001) code,code_sha256,
            exit_code,timed_out,substr(stdout,1,500001) stdout,substr(stderr,1,100001) stderr,
            substr(executor_evidence_json,1,16001) evidence FROM executions WHERE run_id=? LIMIT 2""",(run_id,)).fetchall()
    if len(rows)!=1:raise ValueError("local_ambiguous_history")
    row=rows[0]
    if row["record_version"]!="gateway-execution.v1" or row["exit_code"]!=0 or row["timed_out"]!=0 or row["stderr"]!="":
        raise ValueError("local_failed_or_other_code")
    if len(row["stdout"].encode())>500000 or type(row["evidence"]) is not str or len(row["evidence"].encode())>16000:
        raise ValueError("local_history_budget")
    evidence=_json(row["evidence"])
    capsule=dict(stdout=row["stdout"],receipt=evidence.get("receipt",evidence.get("local_transport_receipt")),dispatch=evidence["local_dispatch"])
    checked_code,_=_capsule_checked(capsule)
    if row["code"]!=checked_code or row["code_sha256"]!=pins.digest(checked_code):raise ValueError("local_failed_or_other_code")
    return capsule

def record(run_id):
    """Internal explicit intake. No caller outputs, code, grade or database."""
    denied=dict(recorded=False,admitted=False,actual_use_authenticated=False,reason="local_intake_disabled")
    if not config.fixed_intake_enabled():return denied
    try:
        capsule=_history(run_id);code,numeric=_capsule_checked(capsule)
        payload=canonical(capsule).decode()
        if len(payload.encode())>600000:raise ValueError("local_capsule_budget")
        digest=pins.digest(payload)
        spec=catalog.from_raw(_json(capsule["stdout"]))
        params=spec["parameters"];ps=pins.digest(canonical(params).decode())
        store=service.STORE
        # Automatic learning is allowed to initialize its own SQLite store on
        # the first exact-profile success. Manual intake keeps the existing
        # explicit initialization requirement.
        if config.SKILL_LIBRARY_ENABLED is True and config.SKILL_LEARNING_ENABLED is True:
            store.migrate()
        with store._db_existing_write() as db:
            decisions._budget(db);db.execute("BEGIN IMMEDIATE")
            prior=db.execute("SELECT skill_id,capsule_sha256 FROM skill_local_sources WHERE source_run_id=?",(run_id,)).fetchone()
            if prior:
                if prior["capsule_sha256"]!=digest:raise ValueError("local_changed_source")
                return dict(denied,recorded=True,duplicate=True,skill_id=prior["skill_id"],reason="local_source_already_recorded")
            existing=db.execute("SELECT status,archived FROM simulation_skills WHERE fingerprint=?",(skill_fingerprint(spec["scene"],params),)).fetchone()
            if existing and (existing["status"]!="candidate" or existing["archived"]):
                raise ValueError("local_lifecycle")
            sid=store.create_candidate(scenario_type=spec["scene"],state_text=spec["state_text"],
                normalized_params=params,code_template=code,preconditions=dict(dimensions=2,parameters_sha256=ps),
                postconditions=dict(verdict="normal",fixed_analytic_pass=True),
                physics_evidence=dict(scope=spec["scope"],code_sha256=spec["code_sha256"],validator_sha256=spec["validator_sha256"],
                    source_run_id=run_id,capsule_sha256=digest,local_source_verified=True,independent_host_authenticated=False),
                source_run_id=run_id,validator_version=spec["scope"],quality_level="Q2",
                idempotent_source_run=True,_existing_connection=db)
            version=db.execute("SELECT MAX(version) FROM skill_versions WHERE skill_id=?",(sid,)).fetchone()[0]
            db.execute("INSERT INTO skill_local_sources VALUES (?,?,?,?,?)",(run_id,sid,version,payload,digest))
            db.execute("INSERT INTO skill_events(skill_id,event_type,detail,created_at) VALUES (?,?,?,?)",
                (sid,"local_profile_admitted",canonical(dict(version=version,source_run_id=run_id,scope=spec["scope"],
                    quality_level="Q4",parameters_sha256=ps,capsule_sha256=digest,actual_use_authenticated=False)).decode(),time.strftime("%Y-%m-%dT%H:%M:%SZ",time.gmtime())))
            db.execute("UPDATE simulation_skills SET status='active',quality_level='Q4' WHERE skill_id=?",(sid,))
            if not config.fixed_intake_enabled():raise ValueError("local_disabled_before_commit")
        return dict(denied,recorded=True,admitted=True,skill_id=sid,quality_level="Q4",scope=spec["scope"],reason="local_fixed_profile_accepted")
    except ERRORS:return dict(denied,reason="local_intake_unavailable")

def verify_connection(store,db,candidate,scenario_type,parameters):
    spec=catalog.matching(scenario_type,parameters)
    if spec is None:return None
    row=db.execute("""SELECT s.status,s.quality_level,s.archived,v.code_hash,substr(v.code_template,1,20001) code_template,substr(v.normalized_params,1,4097) normalized_params,
        substr(v.reference_skill_ids,1,4097) reference_skill_ids,substr(v.physics_evidence,1,16001) physics_evidence,s.scenario_type,substr(s.preconditions,1,4097) preconditions,l.source_run_id,substr(l.capsule_json,1,600001) capsule_json,l.capsule_sha256
        FROM simulation_skills s JOIN skill_versions v ON v.skill_id=s.skill_id
        JOIN skill_local_sources l ON l.skill_id=v.skill_id AND l.version=v.version
        WHERE s.skill_id=? AND v.version=? AND v.version=(SELECT MAX(version) FROM skill_versions WHERE skill_id=s.skill_id)
        ORDER BY l.source_run_id LIMIT 1""",(candidate["skill_id"],candidate["version"])).fetchone()
    if row is None:return None
    for field,limit in (("code_template",20000),("normalized_params",4096),
            ("reference_skill_ids",4096),("physics_evidence",16000),("preconditions",4096)):
        if type(row[field]) is not str or len(row[field].encode())>limit:
            raise ValueError("local_metadata_budget")
    if row["status"]!="active" or row["quality_level"]!="Q4" or row["archived"] or _json(row["reference_skill_ids"])!=[]:
        raise ValueError("local_consumer_lifecycle")
    if len(row["capsule_json"].encode())>600000 or pins.digest(row["capsule_json"])!=row["capsule_sha256"]:
        raise ValueError("local_capsule_integrity")
    code,_=_capsule_checked(_json(row["capsule_json"]))
    if row["code_hash"]!=spec["code_sha256"] or row["code_template"]!=code or _json(row["normalized_params"])!=parameters:
        raise ValueError("local_version_drift")
    evidence=_json(row["physics_evidence"])
    if (row["scenario_type"]!=scenario_type or evidence.get("scope")!=spec["scope"]
            or evidence.get("source_run_id")!=row["source_run_id"]
            or evidence.get("capsule_sha256")!=row["capsule_sha256"]
            or evidence.get("code_sha256")!=spec["code_sha256"]
            or evidence.get("validator_sha256")!=spec["validator_sha256"]
            or _json(row["preconditions"])!=dict(dimensions=2,parameters_sha256=pins.digest(canonical(parameters).decode()))):
        raise ValueError("local_scope")
    if db.execute("SELECT 1 FROM skill_feedback WHERE skill_id=? AND run_id=?",(candidate["skill_id"],row["source_run_id"])).fetchone():
        raise ValueError("local_negative_feedback")
    if not store._inspect_derivation_connection(db,candidate["skill_id"],candidate["version"])["references_current"]:
        raise ValueError("local_dependency")
    return dict(candidate,scenario_type=spec["scene"],scope=spec["scope"],quality_level="Q4",status="active",
        parameters_sha256=pins.digest(canonical(parameters).decode()),code_sha256=spec["code_sha256"],
        source_run_id=row["source_run_id"])

def _context(original,reference):
    return original+"\n\nValidated local fixed-profile reference (parameters must remain unchanged):\n"+canonical(
        dict(reference,code=_recipe(reference["scope"]),actual_use_authenticated=False)).decode()

def consume(*,decision_id,nonce,generation_run_id,scenario_type,parameters_json,original_context):
    denied=dict(decisions._base(original_context,"local_reference_unavailable"),consumed=False)
    if not decisions.enabled():return denied
    try:
        uuid.UUID(generation_run_id)
        params,ps,cs=decisions._request(scenario_type,parameters_json,original_context)
        store=service.STORE
        from app import skill_retrieval
        with store._db_existing_write() as db:
            decisions._budget(db);db.execute("BEGIN IMMEDIATE")
            row=db.execute("SELECT * FROM skill_generation_decisions WHERE decision_id=?",(decision_id,)).fetchone()
            now=time.time_ns()
            if row is None or row["nonce"]!=nonce or row["scenario_type"]!=scenario_type or row["parameters_sha256"]!=ps or row["context_sha256"]!=cs or not row["created_ns"]<=now<row["expires_ns"]:
                return denied
            if db.execute("SELECT 1 FROM skill_generation_consumptions WHERE decision_id=? OR generation_run_id=?",(decision_id,generation_run_id)).fetchone():return denied
            current=skill_retrieval._shortlist(store,db,scenario_type,params,3)
            snapshot=json.dumps(current,sort_keys=True,separators=(",",":"))
            if snapshot!=row["metadata_snapshot_json"]:return denied
            reference=None
            for candidate in current:
                checked=verify_connection(store,db,candidate,scenario_type,params)
                if checked is not None:reference=checked;break
            if reference is None:return denied
            context=_context(original_context,reference)
            db.execute("INSERT INTO skill_generation_consumptions VALUES (?,?,?,?,?)",
                (decision_id,generation_run_id,now,canonical([reference]).decode(),"local_fixed_reference_delivery"))
            if not decisions.enabled() or not row["created_ns"]<=time.time_ns()<row["expires_ns"]:raise ValueError("local_expired_before_commit")
        return dict(denied,consumed=True,context=context,skills=[reference],fallback=False,local_source_verified=True,
            actual_use_authenticated=False,audit_kind="local_fixed_reference_delivery",reason="local_fixed_reference_delivery")
    except ERRORS:return denied

def read_connection(db,*,decision_id,generation_run_id,scenario_type,parameters_json,original_context,_decision_at=None):
    """Private same-transaction reader; prebound execution time never skips current lifecycle."""
    if not decisions.enabled():return original_context
    params,ps,cs=decisions._request(scenario_type,parameters_json,original_context)
    row=db.execute("""SELECT d.*,c.metadata_current_json candidates_json,c.fallback_reason reason FROM skill_generation_decisions d
        JOIN skill_generation_consumptions c ON c.decision_id=d.decision_id
        WHERE d.decision_id=? AND c.generation_run_id=?""",(decision_id,generation_run_id)).fetchone()
    at=time.time_ns() if _decision_at is None else _decision_at
    if row is None or row["reason"]!="local_fixed_reference_delivery" or row["scenario_type"]!=scenario_type or row["parameters_sha256"]!=ps or row["context_sha256"]!=cs or not row["created_ns"]<=at<row["expires_ns"]:return original_context
    refs=_json(row["candidates_json"])
    if type(refs) is not list or len(refs)!=1:return original_context
    reference=verify_connection(service.STORE,db,refs[0],scenario_type,params)
    if reference!=refs[0]:return original_context
    return _context(original_context,reference)

def read_context(*,decision_id,generation_run_id,scenario_type,parameters_json,original_context):
    if not decisions.enabled():return original_context
    try:
        with service.STORE._read_db() as db:
            return read_connection(db,decision_id=decision_id,generation_run_id=generation_run_id,
                scenario_type=scenario_type,parameters_json=parameters_json,original_context=original_context)
    except ERRORS:return original_context
