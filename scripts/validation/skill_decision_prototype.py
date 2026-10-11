"""Single-DB test decision, not production reuse or root approval."""
import hashlib
import json
import re
import sqlite3
import time
from scripts.validation.host_dispatch_ledger import inspect_test_dispatch
from app.skill_store import SkillStore,preconditions_match
from scripts.validation import host_consumption_prototype as ledger
from scripts.validation.host_forward_binding import inspect_binding
from scripts.validation import host_forward_signature as signature
from scripts.validation import plane_wave_run_binding as binding
from scripts.validation.plane_wave_evidence import EXPECTED_CONFIGURATION,validate_plane_wave

def initialize_test_decisions(path):
    with ledger._db(path) as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute("""CREATE TABLE test_skill_decisions (
            run_id TEXT,nonce TEXT,skill_id TEXT,version INTEGER,
            code_sha256 TEXT,root_id TEXT,revision INTEGER,context_sha256 TEXT,
            PRIMARY KEY(run_id,nonce))""")
        for operation in ("UPDATE","DELETE"):
            db.execute(f"""CREATE TRIGGER test_decision_no_{operation.lower()}
                BEFORE {operation} ON test_skill_decisions BEGIN
                SELECT RAISE(ABORT,'append-only'); END""")
        db.execute("""CREATE TRIGGER test_decision_no_replace BEFORE INSERT ON test_skill_decisions
            WHEN EXISTS(SELECT 1 FROM test_skill_decisions WHERE run_id=NEW.run_id AND nonce=NEW.nonce)
            BEGIN SELECT RAISE(ABORT,'append-only'); END""")

def consume_test_decision(path, *, skill_id, version, root_id, revision, document,
                          expected_payload, stdout, stderr):
    denied={"consumed":False,"authenticated":False,"trusted_run":False,
            "parameters_bound":False,"run_bound":False,"runtime_approved":False}
    try:
        if (type(version) is not int or version<1 or type(revision) is not int or revision<1
                or type(expected_payload) is not dict or set(expected_payload)!={"root_id","revision","observation"}
                or type(expected_payload["revision"]) is not int
                or expected_payload["root_id"]!=root_id or expected_payload["revision"]!=revision
                or type(stdout) is not str or type(stderr) is not str
                or len(stdout.encode())>500000 or len(stderr.encode())>100000):
            return dict(denied,reason="invalid_test_decision")
        observation=expected_payload["observation"]
        run_id=observation["run_id"];nonce=observation["nonce"]
        if (type(run_id) is not str or not re.fullmatch("[A-Za-z0-9-]{1,128}",run_id)
                or type(nonce) is not str or not re.fullmatch("[0-9a-f]{32}",nonce)):
            return dict(denied,reason="invalid_test_decision")
        with ledger._db(path) as db:
            db.row_factory=sqlite3.Row
            budget=[0]
            def progress():
                budget[0]+=1
                return int(budget[0]>1000)
            db.set_progress_handler(progress,1000)
            db.execute("BEGIN IMMEDIATE")
            root=db.execute("SELECT revision,key,state FROM roots WHERE id=? ORDER BY revision DESC LIMIT 1",(root_id,)).fetchone()
            if root is None or root["revision"]!=revision or root["state"]!="test-active":
                return dict(denied,reason="root_unavailable_or_stale")
            expected_context=inspect_test_dispatch(db,run_id,nonce,time.time_ns())
            bound=inspect_binding(expected_payload,expected_context)
            if not bound["context_matched"]:
                return dict(denied,reason=bound["reason"])
            checked=signature.verify_forward_signature(document,public_key=root["key"],expected_payload=expected_payload)
            if not checked["signature_matched"]:
                return dict(denied,reason="signature_or_context_mismatch")
            skill=db.execute("SELECT status,archived,quality_level,preconditions FROM simulation_skills WHERE skill_id=?",(skill_id,)).fetchone()
            latest=db.execute("SELECT MAX(version) FROM skill_versions WHERE skill_id=?",(skill_id,)).fetchone()[0]
            if (skill is None or skill["archived"] or skill["status"]!="active"
                    or skill["quality_level"] not in ("Q3","Q4") or version!=latest):
                return dict(denied,reason="skill_lifecycle_or_version")
            bounds=json.loads(skill["preconditions"],object_pairs_hook=signature.unique,parse_constant=signature.reject)
            fixed_parameters={"domain_N":EXPECTED_CONFIGURATION["domain_N"],
                              "dx_m":EXPECTED_CONFIGURATION["domain_dx_m"][0]}
            if not preconditions_match(bounds,fixed_parameters):
                return dict(denied,reason="skill_boundary_mismatch")
            code=db.execute("SELECT code_template FROM skill_versions WHERE skill_id=? AND version=?",(skill_id,version)).fetchone()[0]
            if binding.digest(code)!=binding.RECIPE_SHA256:
                return dict(denied,reason="fixed_code_required")
            dependency=SkillStore(path)._inspect_derivation_connection(db,skill_id,version)
            if not dependency["references_current"]:
                return dict(denied,reason="derived_lifecycle_unavailable")
            expected_request={"code":code,"timeout_seconds":30,"request_nonce":nonce}
            if (observation["profile"]!="val1-plane-wave-v2"
                    or observation["code_sha256"]!=binding.RECIPE_SHA256
                    or observation["configuration_sha256"]!=hashlib.sha256(signature.canonical(EXPECTED_CONFIGURATION)).hexdigest()
                    or observation["request_sha256"]!=hashlib.sha256(signature.canonical(expected_request)).hexdigest()
                    or observation["stdout_sha256"]!=binding.digest(stdout)
                    or observation["stderr_sha256"]!=binding.digest(stderr)
                    or type(observation["exit_code"]) is not int or observation["exit_code"]!=0
                    or observation["timed_out"] is not False):
                return dict(denied,reason="evidence_binding_mismatch")
            validator=(binding.DIRECTORY/"plane_wave_evidence.py").read_bytes()
            if hashlib.sha256(validator).hexdigest()!=binding.VALIDATOR_SHA256:
                return dict(denied,reason="validator_pin_changed")
            raw=json.loads(stdout,object_pairs_hook=signature.unique,parse_constant=signature.reject)
            if not validate_plane_wave(raw)["numeric_pass"]:
                return dict(denied,reason="physics_failed")
            if db.execute("SELECT 1 FROM consumption WHERE run_id=? AND nonce=?",(run_id,nonce)).fetchone():
                return dict(denied,reason="already_consumed")
            digest=hashlib.sha256(signature.canonical(expected_payload)).hexdigest()
            db.execute("INSERT INTO consumption VALUES(?,?,?,?,?)",(run_id,nonce,digest,root_id,revision))
            db.execute("INSERT INTO test_skill_decisions VALUES(?,?,?,?,?,?,?,?)",
                       (run_id,nonce,skill_id,version,binding.RECIPE_SHA256,root_id,revision,digest))
            return dict(denied,consumed=True,reason="test_joint_decision_only")
    except (sqlite3.Error,OSError,ValueError,TypeError,KeyError,IndexError,RecursionError,OverflowError):
        return dict(denied,reason="test_decision_unavailable")
