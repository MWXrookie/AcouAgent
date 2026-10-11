"""Explicit host-only four-case coordinator; no CLI/service/keys/enrollment.

An operator supplies an already unlocked approved key, shared token and exact
target. A separately recorded owner policy for this NEW namespace must already
exist. No root policies/flags/migrations are created. It registers each run
before its own forward; failure stops remaining requests, without retries.
All complete outputs go through independent analytic and consistency checks.
Result is a Q2 diagnostic, never active/reference code or authenticated use.
"""
import time
import uuid
from app import config,skill_consistency_source as source,skill_consistency_admission as admission
from host_observer.consistency_signing import dispatch
from host_observer.signing import forward_consistency_with_approved_key

MAX_BATCH_SECONDS=180


def observe_consistency_source(target,*,key,shared_token,revision):
    denied=dict(source.FALSE_TRUST,recorded=False,diagnostic_recorded=False,admitted=False,
        completed_cases=0,stage="disabled",failed_case=None,reason="consistency_runner_disabled")
    if config.SKILL_LIBRARY_ENABLED is not True or config.SKILL_LEARNING_ENABLED is not True:
        return denied
    batch_id=str(uuid.uuid4());runs={};records={};started=time.monotonic()
    stage="target";case=None
    try:
        target.validate()
        if type(revision) is not int or not 1<=revision<=2147483647:
            raise ValueError("explicit_revision")
        stage="authority"
        with source.deadline.limited(),source.service.STORE._read_db() as db:
            source._budget(db);source._require_current_schema(db);source._root(db,revision,time.time_ns())
        for case in source.numerical.PROFILES:
            stage="dispatch"
            if time.monotonic()-started>MAX_BATCH_SECONDS-40:
                raise ValueError("batch_deadline")
            now=time.time_ns()
            context=dict(root_id=source.ROOT_ID,revision=revision,batch_id=batch_id,case=case,
                run_id=str(uuid.uuid4()),nonce=uuid.uuid4().hex,target_ip=target.ip,target_port=target.port,
                container_id=target.container_id,image_id=target.image_id,started_at=target.started_at,
                dispatch_not_before_ns=now,dispatch_not_after_ns=now+60_000_000_000)
            # Signing alone does not authorize forwarding. The authority DB
            # checks current root policy and pins under its writer lock first.
            registered=source.record_dispatch(context,dispatch(key,context=context))
            if not registered["recorded"]:
                raise ValueError("consistency_dispatch_rejected")
            runs[case]=context["run_id"]
            stage="execution"
            observed=forward_consistency_with_approved_key(target,case=case,run_id=context["run_id"],
                nonce=context["nonce"],shared_token=shared_token,key=key,revision=revision)
            raw=observed["result"]
            if type(raw["exit_code"]) is not int or raw["exit_code"]!=0 or raw["timed_out"] is not False or raw["stderr"]!="":
                raise ValueError("consistency_case_execution_failed")
            records[case]=dict(observation_document=observed["signed_observation"].decode(),
                stdout=raw["stdout"],stderr="")
        if time.monotonic()-started>MAX_BATCH_SECONDS:raise ValueError("batch_deadline")
        stage="candidate";case=None
        candidate=admission.accumulate_candidate(batch_id=batch_id,records=records)
        if not candidate["recorded"]:
            return dict(denied,batch_id=batch_id,runs=runs,completed_cases=len(records),
                stage=stage,reason="consistency_source_or_physical_diagnostic_failed")
        stage="diagnostic"
        skill_id=candidate["skill_id"]
        # Same-code observations may retain an existing version. Read identity;
        # the atomic diagnostic repeats latest version/source checks itself.
        with source.deadline.limited(),source.service.STORE._read_db() as db:
            source._budget(db)
            latest=db.execute("SELECT MAX(version) FROM skill_versions WHERE skill_id=?",(skill_id,)).fetchone()[0]
        diagnostic=admission.record_diagnostic(skill_id=skill_id,version=latest,batch_id=batch_id,records=records)
        return dict(denied,recorded=True,diagnostic_recorded=diagnostic["diagnostic_recorded"],
            skill_id=skill_id,version=latest,batch_id=batch_id,runs=runs,
            completed_cases=len(records),quality_level="Q2",
            stage="complete" if diagnostic["diagnostic_recorded"] else stage,reason=diagnostic["reason"])
    except Exception:
        # Keep independent dispatch ledger for diagnosis. Do not expose raw
        # third-party exception text, response logs, shared token or key.
        return dict(denied,batch_id=batch_id,runs=runs,completed_cases=len(records),
            stage=stage,failed_case=case,reason="consistency_runner_failed")
