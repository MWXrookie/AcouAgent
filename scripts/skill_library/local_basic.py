"""Explicit local basic-profile preparation; run inside the configured gateway.

init creates only a missing store; it never migrates or overwrites an existing
database. seed executes one reviewed recipe through the existing executor.
No model calls, signing configuration, automatic learning or arbitrary paths.
"""
import argparse
import hashlib
import json
import os
import sqlite3
import uuid
from app import config, dashboard, execution, skill_library_service as service
from app import skill_local_profile as local, skill_retrieval
from app.skill_store import SCHEMA_VERSION
from scripts.validation.host_forward_signature import canonical

OBSOLETE_PLANE_CODE_SHA256="4561ef66b6daa4ffdd40ff6d9c35005fbdb1a3958fa70fb9309a29d4928d6b2b"
OBSOLETE_PLANE_PARAMETERS=dict(grid_shape=[384,384],dimensions=2,dx_m=[.00025,.00025],
    sound_speed_m_s=1500.0,density_kg_m3=1000.0,pml_size=20,cfl=.1,t_end_s=34e-6,
    initial_pressure_pa=1.0,initial_velocity="zero",initial_sigma_m=.003,
    source_type="gaussian_initial_pressure",source_geometry="planar_x_uniform_y",
    source_index=[160,192],sensor_indices=[[224,192],[304,192]],smooth_initial=False,
    solver_mode="time_domain",medium_type="homogeneous_water")


def initialize():
    if not config.fixed_intake_enabled():
        return dict(ready=False, reason="local_intake_disabled")
    path=service.STORE.path
    if path.exists():
        with service.STORE._read_db() as db:
            versions={row[0] for row in db.execute("SELECT version FROM schema_migrations")}
        return dict(ready=versions==set(range(1,SCHEMA_VERSION+1)),
                    created=False, reason="existing_store_not_modified")
    path.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive reservation prevents an accidental overwrite during bootstrap.
    descriptor=os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    os.close(descriptor)
    service.STORE.migrate()
    return dict(ready=True, created=True, reason="empty_store_initialized")


def retire_superseded_plane_profile():
    """Archive only the exact former fixed recipe after its 30s budget became invalid."""
    from app.skill_store import skill_fingerprint
    path=service.STORE.path
    if not path.exists():
        return dict(ready=True,retired=False,reason="obsolete_profile_absent")
    with service.STORE._read_db() as db:
        rows=db.execute("""SELECT s.skill_id,s.status,s.quality_level,s.archived,s.updated_at,
            v.code_hash,substr(v.code_template,1,20001) code_template
            FROM simulation_skills s JOIN skill_versions v ON v.skill_id=s.skill_id
            WHERE s.fingerprint=? AND v.version=(SELECT MAX(version) FROM skill_versions x WHERE x.skill_id=s.skill_id)
            LIMIT 2""",(skill_fingerprint("initial_pressure_2d",OBSOLETE_PLANE_PARAMETERS),)).fetchall()
    if len(rows)!=1:
        return dict(ready=True,retired=False,reason="obsolete_profile_absent")
    row=rows[0]
    exact=(row["code_hash"]==OBSOLETE_PLANE_CODE_SHA256
        and hashlib.sha256(row["code_template"].encode()).hexdigest()==OBSOLETE_PLANE_CODE_SHA256)
    if not exact or row["status"]!="active" or row["quality_level"]!="Q4":
        return dict(ready=True,retired=False,reason="obsolete_profile_not_exact_active")
    if row["archived"]:
        return dict(ready=True,retired=False,reason="obsolete_profile_already_archived")
    result=service.STORE.set_archived(row["skill_id"],archived=True,
        expected_updated_at=row["updated_at"],actor="internal-system",
        reason="superseded fixed plane-wave recipe exceeded the 30s runtime budget")
    return dict(ready=True,retired=result.get("archived") is True,
        skill_id=row["skill_id"],reason="obsolete_profile_archived")


def seed(profile=local.PROFILE):
    if not config.fixed_intake_enabled():
        return dict(ready=False, reason="local_intake_disabled")
    spec=local.catalog.specification(profile)
    params=spec["parameters"]
    parameter_json=canonical(params).decode()
    found=skill_retrieval.search(spec["scene"],parameter_json)
    if found["skills"]:
        return dict(ready=True, executed=False, skills=found["skills"],
                    reason="verified_local_profile_available")
    # Never rerun or reactivate a withdrawn/archived profile implicitly.
    from app.skill_store import skill_fingerprint
    with service.STORE._read_db() as db:
        old=db.execute("SELECT status,archived FROM simulation_skills WHERE fingerprint=?",
                       (skill_fingerprint(spec["scene"],params),)).fetchone()
        if old and (old["status"]!="candidate" or old["archived"]):
            return dict(ready=False, executed=False, reason="local_profile_requires_review")
    code=local._recipe(profile)
    result=execution._execute_code(code,30)
    run=str(uuid.uuid4())
    dashboard.record_execution("local_basic_seed",code,result.get("exit_code"),
        result.get("timed_out",False),result.get("duration_ms"),
        result.get("stdout",""),result.get("stderr",""),run_id=run,
        execution_evidence=result.get("execution_evidence"))
    accepted=local.record(run)
    return dict(ready=accepted.get("admitted") is True, executed=True, run_id=run,
                reason=accepted.get("reason"), admission=accepted,
                actual_use_authenticated=False,
                learning_enabled=config.SKILL_LEARNING_ENABLED is True)


def bootstrap():
    """Idempotently prepare every reviewed fixed profile on a fresh data volume."""
    if config.SKILL_BOOTSTRAP_ALL_FIXED is not True:
        return dict(ready=True, skipped=True, profile_count=0,
                    reason="fixed_profile_bootstrap_disabled")
    if not (config.SKILL_LIBRARY_ENABLED is True
            and config.SKILL_RETRIEVAL_ENABLED is True
            and config.SKILL_MANUAL_INTAKE_ENABLED is True):
        return dict(ready=False, skipped=False, profile_count=0,
                    reason="fixed_profile_bootstrap_flags_incomplete")
    dashboard.init_db()
    initialized=initialize()
    if initialized.get("ready") is not True:
        return dict(ready=False, skipped=False, profile_count=0,
                    reason=initialized.get("reason","store_initialization_failed"))
    retirement=(retire_superseded_plane_profile() if initialized.get("created") is False
        else dict(ready=True,retired=False,reason="fresh_store"))
    if retirement.get("ready") is not True:
        return dict(ready=False,skipped=False,profile_count=0,
            reason=retirement.get("reason","obsolete_profile_retirement_failed"))
    items=[]
    for profile in local.catalog.SCOPES:
        attempts=[]
        for attempt in range(1,4):
            result=seed(profile)
            attempts.append(dict(attempt=attempt,ready=result.get("ready") is True,
                executed=result.get("executed") is True,reason=result.get("reason")))
            if result.get("ready") is True or result.get("executed") is not True:
                break
        item=dict(profile=profile,ready=result.get("ready") is True,
            executed=any(row["executed"] for row in attempts),attempts=attempts,
            reason=result.get("reason"))
        admission=result.get("admission")
        if isinstance(admission,dict):
            item["skill_id"]=admission.get("skill_id")
            item["quality_level"]=admission.get("quality_level")
        items.append(item)
        if item["ready"] is not True:
            return dict(ready=False,skipped=False,profile_count=len(items),
                failed_profile=profile,reason=item["reason"],profiles=items)
    return dict(ready=True,skipped=False,profile_count=len(items),
        executed_count=sum(1 for item in items if item["executed"]),
        retired_count=1 if retirement.get("retired") is True else 0,
        reason="all_fixed_profiles_ready",profiles=items)


def status_all():
    items=[]
    for profile in local.catalog.SCOPES:
        spec=local.catalog.specification(profile)
        found=skill_retrieval.search(spec["scene"],canonical(spec["parameters"]).decode())
        matched=[item for item in found.get("skills",[]) if item.get("scope")==profile]
        items.append(dict(profile=profile,ready=len(matched)==1,
            skill_id=matched[0].get("skill_id") if matched else None))
    return dict(ready=all(item["ready"] for item in items),
        profile_count=len(items),ready_count=sum(1 for item in items if item["ready"]),profiles=items)


def verify_reuse(profile=local.PROFILE):
    """Explicit one-shot pilot: server builds the reference, then executor uses its fixed code."""
    if not config.fixed_intake_enabled():
        return dict(ready=False,reason="local_intake_disabled")
    spec=local.catalog.specification(profile)
    params=canonical(spec["parameters"]).decode()
    built=skill_retrieval.generation_context("local basic reuse verification",
        scenario_type=spec["scene"],parameters_json=params,_existing_store=True)
    usage=built.get("skill_usage",{})
    if usage.get("fallback") is not False:
        return dict(ready=False,executed=False,reason="local_reference_unavailable")
    from app.skill_local_execution import execute_reference
    result=execute_reference(decision_id=usage["decision_id"],
        generation_run_id=usage["generation_intent_id"],scenario_type=spec["scene"],
        parameters_json=params,original_context="local basic reuse verification")
    return dict(result,ready=result.get("execution_reuse_verified") is True)

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action",choices=("init","seed","status","status-all","bootstrap","verify-reuse"))
    parser.add_argument("--profile",choices=local.catalog.SCOPES,default=local.PROFILE)
    args=parser.parse_args();action=args.action
    try:
        if action=="init":result=initialize()
        elif action=="seed":result=seed(args.profile)
        elif action=="bootstrap":result=bootstrap()
        elif action=="status-all":result=status_all()
        elif action=="verify-reuse":result=verify_reuse(args.profile)
        else:
            spec=local.catalog.specification(args.profile)
            result=skill_retrieval.search(spec["scene"],canonical(spec["parameters"]).decode())
    except (ValueError,TypeError,KeyError,OSError,sqlite3.Error):
        result=dict(ready=False,reason="local_preparation_unavailable")
    print(json.dumps(result,ensure_ascii=False,allow_nan=False))
    return 0 if result.get("ready") is True or action=="status" else 1


if __name__=="__main__":
    raise SystemExit(main())
