"""Explicit local basic-profile preparation; run inside the configured gateway.

init creates only a missing store; it never migrates or overwrites an existing
database. seed executes one reviewed recipe through the existing executor.
No model calls, signing configuration, automatic learning or arbitrary paths.
"""
import argparse
import json
import os
import sqlite3
import uuid
from app import config, dashboard, execution, skill_library_service as service
from app import skill_local_profile as local, skill_retrieval
from app.skill_store import SCHEMA_VERSION
from scripts.validation.host_forward_signature import canonical


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
                admission=accepted, actual_use_authenticated=False,
                learning_enabled=config.SKILL_LEARNING_ENABLED is True)


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
    parser.add_argument("action",choices=("init","seed","status","verify-reuse"))
    parser.add_argument("--profile",choices=(local.catalog.PLANE,local.catalog.INTERFACE),default=local.PROFILE)
    args=parser.parse_args();action=args.action
    try:
        if action=="init":result=initialize()
        elif action=="seed":result=seed(args.profile)
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
