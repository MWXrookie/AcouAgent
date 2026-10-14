"""Explicit host-only fixed reference execution, no daemon or key loading.

Requires an already unlocked approved key, existing root policy and concrete
target. It does not enroll roots, change flags, retry, or run skill code in the
gateway. Forwarding stays inside the bounded independent host HTTP observer.
"""
import time
import uuid
from app import skill_authority as authority,skill_reference_execution as usage
from host_observer.authority_signing import dispatch_approval
from host_observer.signing import forward_with_approved_key
from scripts.validation.approved_host_root import ROOT_ID

def execute_bound_reference(target,*,reference,key,shared_token,revision):
    denied=dict(recorded=False,execution_reuse_verified=False,actual_use_authenticated=False,
                reason="reference_runner_disabled")
    if not usage.decisions.enabled():return denied
    try:
        target.validate()
        first=time.time_ns()
        context=dict(root_id=ROOT_ID,revision=revision,run_id=str(uuid.uuid4()),nonce=uuid.uuid4().hex,
            target_ip=target.ip,target_port=target.port,container_id=target.container_id,
            image_id=target.image_id,started_at=target.started_at,
            dispatch_not_before_ns=first,dispatch_not_after_ns=first+60_000_000_000)
        approval=dispatch_approval(key,context=context)
        # The native record and generation association commit together before
        # any HTTP request. Rejection must never invoke the observer.
        recorded=authority.record_dispatch(context,approval,generation_reference=reference)
        if not recorded["recorded"]:return dict(denied,reason="reference_dispatch_rejected")
        result=forward_with_approved_key(target,run_id=context["run_id"],nonce=context["nonce"],
                                        shared_token=shared_token,key=key,revision=revision)
        raw=result["result"]
        checked=usage.observe(observation_document=result["signed_observation"],
                              stdout=raw["stdout"],stderr=raw["stderr"])
        return dict(checked,dispatch_recorded=True)
    except Exception:
        # Never emit third-party exception text, token, key or result logs.
        return dict(denied,reason="reference_runner_failed")
