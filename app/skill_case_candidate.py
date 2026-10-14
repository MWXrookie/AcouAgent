"""Same-transaction exact case candidate intake, never active or reference grant."""
import hashlib
import json
import time
from app import skill_case_source as source
from app.skill_store import normalize_parameters,skill_fingerprint
from scripts.validation.host_forward_signature import canonical

def parameters(case):
    cfg,_=source.registry.specification(case)
    return normalize_parameters(dict(grid_shape=cfg["domain_N"],dx_m=cfg["domain_dx_m"],source_geometry=cfg))

def scenario(case):
    if "plane" in case:return "plane_wave_2d"
    if "cylinder" in case:return "point_source_2d"
    if "sphere" in case:return "point_source_3d"
    if "interface" in case:return "interface_2d"
    return "attenuation_helmholtz_2d"

def accumulate_candidate(*,case,record):
    denied=dict(source.FALSE_TRUST,recorded=False,admitted=False,quality_level="Q2",reason="case_candidate_disabled")
    if not source.enabled():return denied
    try:
        with source.deadline.limited():
            store=source.service.STORE
            with store._db_existing_write() as db:
                source._budget(db);db.execute("BEGIN IMMEDIATE")
                checked=source.verify_connection(db,case=case,record=record,now=time.time_ns())
                if not checked["source_prerequisites_passed"] or not checked["numeric_pass"]:
                    raise ValueError("case_candidate_prerequisites")
                cfg,code=source.registry.specification(case);params=parameters(case)
                fingerprint=skill_fingerprint(scenario(case),params)
                existing=db.execute("SELECT status,quality_level,archived FROM simulation_skills WHERE fingerprint=?",(fingerprint,)).fetchone()
                if existing and (existing["status"]!="candidate" or existing["quality_level"]!="Q2" or existing["archived"]):
                    raise ValueError("case_candidate_lifecycle")
                evidence=dict(source.FALSE_TRUST,scope=source.PROFILE,case=case,
                    root_id=source.ROOT_ID,revision=checked["revision"],
                    batch_id=checked["batch_id"],run_id=checked["run_id"],nonce=checked["nonce"],
                    manifest_sha256=checked["manifest_sha256"],
                    observation_sha256=hashlib.sha256(record["observation_document"].encode()).hexdigest(),
                    stdout_sha256=hashlib.sha256(record["stdout"].encode()).hexdigest(),
                    numeric=checked["numeric"],source_prerequisites_passed=True,
                    physical_acceptance_passed=False)
                sid=store.create_candidate(scenario_type=scenario(case),
                    state_text="固定公开案例；有来源前提及数值诊断的Q2候选，尚未获准可信复用",
                    normalized_params=params,code_template=code,
                    preconditions=dict(dimensions=len(cfg["domain_N"]),parameters_sha256=hashlib.sha256(canonical(params)).hexdigest()),
                    postconditions=dict(verdict="normal",numeric_diagnostic_passed=True),
                    physics_evidence=evidence,source_run_id=checked["run_id"],validator_version=source.PROFILE,
                    quality_level="Q2",idempotent_source_run=True,_existing_connection=db)
                # Expiry can occur while validating/inserting; reject before commit.
                source._root(db,checked["revision"],time.time_ns())
                source.deadline.check()
        return dict(denied,recorded=True,skill_id=sid,reason="atomic_case_candidate_only")
    except source.ERRORS:return dict(denied,reason="case_candidate_unavailable")
