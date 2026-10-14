"""Same-authority-DB fixed host evidence linkage; never promotion or reuse.

Only operator-signed lifecycle and exact dispatch approvals can reach a pinned
numeric check. This records a candidate's immutable source diagnostic, not full
VAL-1 acceptance, deployed host independence, or a trust grant.
"""
import hashlib
import json
import sqlite3
import time
from app import config,skill_library_service,skill_authority,controlled_dispatch
from app.skill_store import preconditions_match
from scripts.validation import host_forward_binding as context_binding
from scripts.validation import host_forward_signature as signatures
from scripts.validation import approved_host_root as approved
from scripts.validation import plane_wave_run_binding as pins
from scripts.validation.plane_wave_evidence import validate_plane_wave

TRUST=dict(skill_authority.TRUST,actual_use_authenticated=False)

def verify_source_connection(db, *, observation_document, stdout, stderr, now, recorded_evidence=False):
    """Authenticated fixed-profile raw source, before or after candidate intake."""
    if (type(observation_document) is not bytes or len(observation_document)>16384
            or type(stdout) is not str or len(stdout.encode())>500000
            or type(stderr) is not str or len(stderr.encode())>100000):
        raise ValueError("source_budget")
    envelope=json.loads(observation_document.decode("utf-8"),object_pairs_hook=signatures.unique,parse_constant=signatures.reject)
    if type(envelope) is not dict or set(envelope)!={"payload","signature"}:raise ValueError("observation_envelope")
    payload=envelope["payload"]
    if type(payload) is not dict or set(payload)!={"root_id","revision","observation"}:raise ValueError("observation_payload")
    obs=payload["observation"];run=obs["run_id"];nonce=obs["nonce"]
    expected=skill_authority.inspect_connection(db,run,nonce,now,recorded_evidence=recorded_evidence)
    if payload["root_id"]!=expected["root_id"] or payload["revision"]!=expected["revision"]:
        raise ValueError("observation_root_revision")
    if not context_binding.inspect_binding(payload,expected)["context_matched"]:
        raise ValueError("actual_observation_context_mismatch")
    if not signatures.verify_forward_signature(observation_document,
            public_key=approved.approved_public_key(),expected_payload=payload)["signature_matched"]:
        raise ValueError("host_signature_mismatch")
    code=(pins.DIRECTORY/"recipes/val1_plane_wave_v2.py").read_text().strip()
    if pins.digest(code)!=pins.RECIPE_SHA256:raise ValueError("source_recipe_pin")
    params=controlled_dispatch.fixed_retrieval_parameters()
    request={"code":code,"timeout_seconds":30,"request_nonce":nonce}
    if (obs["profile"]!=approved.PROFILE or obs["code_sha256"]!=pins.RECIPE_SHA256
            or obs["configuration_sha256"]!=skill_authority.fixed_scope()["configuration_sha256"]
            or obs["request_sha256"]!=hashlib.sha256(signatures.canonical(request)).hexdigest()
            or obs["stdout_sha256"]!=pins.digest(stdout) or obs["stderr_sha256"]!=pins.digest(stderr)
            or type(obs["exit_code"]) is not int or obs["exit_code"]!=0 or obs["timed_out"] is not False):
        raise ValueError("actual_code_parameters_output_mismatch")
    if hashlib.sha256((pins.DIRECTORY/"plane_wave_evidence.py").read_bytes()).hexdigest()!=pins.VALIDATOR_SHA256:
        raise ValueError("validator_pin_changed")
    raw=json.loads(stdout,object_pairs_hook=signatures.unique,parse_constant=signatures.reject)
    result=validate_plane_wave(raw)
    if not result["numeric_pass"]:raise ValueError("fixed_numeric_check_failed")
    return dict(run=run,nonce=nonce,payload=payload,expected=expected,result=result,
                code=code,parameters=params)

def verify_connection(store, db, *, skill_id, version, observation_document, stdout, stderr,
                      now, recorded_evidence=False, require_independent_root=False):
    """Fresh candidate/source verification inside the same decision transaction."""
    if type(skill_id) is not str or not 1<=len(skill_id)<=128 or type(version) is not int or version<1:
        raise ValueError("candidate_identity")
    checked=verify_source_connection(db,observation_document=observation_document,stdout=stdout,
        stderr=stderr,now=now,recorded_evidence=recorded_evidence)
    run=checked["run"];params=checked["parameters"]
    row=db.execute("""SELECT s.status,s.archived,substr(s.preconditions,1,4097) preconditions,substr(v.code_template,1,20001) code_template,v.code_hash,substr(v.normalized_params,1,4097) normalized_params
        FROM simulation_skills s JOIN skill_versions v ON v.skill_id=s.skill_id
        WHERE s.skill_id=? AND v.version=?
        AND v.version=(SELECT MAX(version) FROM skill_versions WHERE skill_id=s.skill_id)""",
        (skill_id,version)).fetchone()
    if row is None or row["archived"] or row["status"] not in {"candidate","active"}:
        raise ValueError("candidate_lifecycle")
    if len(row["preconditions"].encode())>4096 or len(row["code_template"].encode())>20000:
        raise ValueError("candidate_record_budget")
    if len(row["normalized_params"].encode())>4096:
        raise ValueError("candidate_parameters_budget")
    from app.skill_store import normalize_parameters
    declared=normalize_parameters(json.loads(row["normalized_params"],
        object_pairs_hook=signatures.unique,parse_constant=signatures.reject))
    if declared!=params:raise ValueError("candidate_parameters_drift")
    epoch=db.execute("SELECT id,substr(detail,1,4097) detail FROM skill_events WHERE skill_id=? AND event_type='revalidation_requested' ORDER BY id DESC LIMIT 1",
        (skill_id,)).fetchone()
    if epoch:
        if len(epoch["detail"].encode())>4096:raise ValueError("revalidation_epoch_budget")
        epoch_data=json.loads(epoch["detail"],object_pairs_hook=signatures.unique,parse_constant=signatures.reject)
        requested=epoch_data.get("requested_ns")
        if type(requested) is not int or not 0<=requested<checked["expected"]["registered_ns"]<=now:
            raise ValueError("fresh_dispatch_required_after_revalidation")
        if recorded_evidence or require_independent_root:
            epoch_receipt=db.execute("SELECT event_id FROM skill_host_verifications WHERE run_id=? AND nonce=?",
                (run,checked["nonce"])).fetchone()
            if epoch_receipt is None or epoch_receipt["event_id"]<=epoch["id"]:
                raise ValueError("fresh_receipt_required_after_revalidation")
    association=store._source_association_connection(db,skill_id,run)
    if (association.get("version")!=version or association.get("code_sha256")!=pins.RECIPE_SHA256
            or row["code_hash"]!=pins.RECIPE_SHA256
            or hashlib.sha256(row["code_template"].encode()).hexdigest()!=pins.RECIPE_SHA256):
        raise ValueError("candidate_source_identity")
    if not store._inspect_derivation_connection(db,skill_id,version,candidate_root=True)["references_current"]:
        raise ValueError("derived_lifecycle")
    declared_refs=db.execute("SELECT substr(reference_skill_ids,1,4097) refs FROM skill_versions WHERE skill_id=? AND version=?",
        (skill_id,version)).fetchone()
    if declared_refs is None or len(declared_refs["refs"].encode())>4096:
        raise ValueError("source_derivation_budget")
    refs=json.loads(declared_refs["refs"],object_pairs_hook=signatures.unique,parse_constant=signatures.reject)
    if type(refs) is not list or len(refs)>3 or any(type(v) is not str for v in refs) or len(refs)!=len(set(refs)):
        raise ValueError("source_derivation_declaration")
    derivation_sha=hashlib.sha256(signatures.canonical(dict(skill_id=skill_id,version=version,
        reference_skill_ids=sorted(refs)))).hexdigest()
    if recorded_evidence or require_independent_root:
        receipt=db.execute("SELECT substr(numeric_result_json,1,16385) result FROM skill_host_verifications WHERE skill_id=? AND version=? AND run_id=? AND nonce=?",
            (skill_id,version,run,checked["nonce"])).fetchone()
        if receipt is None or len(receipt["result"].encode())>16384:
            raise ValueError("source_derivation_receipt")
        result=json.loads(receipt["result"],object_pairs_hook=signatures.unique,parse_constant=signatures.reject)
        bound=result.get("source_derivation_sha256")
        # Historic standalone receipts remain usable; historic derived receipts
        # lacked an immutable declaration binding and cannot grant positive use.
        if bound!=derivation_sha and not (bound is None and refs==[]):
            raise ValueError("source_derivation_changed")
    if require_independent_root:
        from app.skill_derivation_authority import verify
        verify(store,db,skill_id,version,now)
    bounds=json.loads(row["preconditions"],object_pairs_hook=signatures.unique,parse_constant=signatures.reject)
    if not preconditions_match(bounds,params):raise ValueError("candidate_boundary")
    return dict(checked,association=association,source_derivation_sha256=derivation_sha)

def record(*,skill_id,version,observation_document,stdout,stderr):
    denied=dict(TRUST,recorded=False,quality_level="Q2")
    if not config.fixed_intake_enabled():
        return dict(denied,reason="host_intake_disabled")
    try:
        store=skill_library_service.STORE
        with store._db_existing_write() as db:
            steps=[0]
            def budget():
                steps[0]+=1000
                return int(steps[0]>1000000)
            db.set_progress_handler(budget,1000)
            db.execute("BEGIN IMMEDIATE");now=time.time_ns()
            checked=verify_connection(store,db,skill_id=skill_id,version=version,
                observation_document=observation_document,stdout=stdout,stderr=stderr,now=now)
            run=checked["run"];nonce=checked["nonce"];payload=checked["payload"]
            expected=checked["expected"];result=checked["result"];params=checked["parameters"]
            # The source-association helper restores its progress handler; this
            # bounded remainder is constant-size point lookup/append work.
            db.set_progress_handler(budget,1000)
            if db.execute("SELECT 1 FROM skill_host_verifications WHERE run_id=? OR nonce=?",(run,nonce)).fetchone():
                raise ValueError("host_evidence_already_consumed")
            digest=hashlib.sha256(signatures.canonical(payload)).hexdigest()
            parameter_sha=hashlib.sha256(signatures.canonical(params)).hexdigest()
            detail={"source_run_id":run,"version":version,"code_sha256":pins.RECIPE_SHA256,
                    "observation_sha256":digest,"parameters_sha256":parameter_sha,
                    "root_id":expected["root_id"],"revision":expected["revision"],
                    "candidate_only":True,"trust_verified":False,"actual_use_authenticated":False}
            event=db.execute("INSERT INTO skill_events(skill_id,event_type,detail,created_at) VALUES (?,?,?,?)",
                             (skill_id,"host_candidate_evidence",signatures.canonical(detail).decode(),
                              time.strftime("%Y-%m-%dT%H:%M:%SZ",time.gmtime())))
            db.execute("INSERT INTO skill_host_verifications VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                       (run,nonce,skill_id,version,expected["root_id"],expected["revision"],pins.RECIPE_SHA256,
                        parameter_sha,digest,pins.digest(stdout),pins.VALIDATOR_SHA256,
                        signatures.canonical(dict(result,source_derivation_sha256=checked["source_derivation_sha256"])).decode(),now,event.lastrowid))
            skill_authority.inspect_connection(db,run,nonce,time.time_ns(),recorded_evidence=True)
            if not config.fixed_intake_enabled():
                raise ValueError("host_intake_disabled_before_commit")

        return dict(denied,recorded=True,reason="fixed_host_candidate_evidence_linked")
    except (ValueError,TypeError,KeyError,IndexError,UnicodeError,OverflowError,RecursionError,sqlite3.Error,OSError,ImportError):
        return dict(denied,reason="host_intake_unavailable")
