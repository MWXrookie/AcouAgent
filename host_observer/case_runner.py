"""Explicit one-case host coordinator. No key loading, enrollment or skill grant.
Caller supplies an unlocked key and already-approved new-scope policy.
Dispatch registration precedes HTTP. No retry, daemon, migration or flag changes.
"""
import time
import uuid
from app import config,skill_case_source as source,skill_case_candidate as candidate
from host_observer import case_signing,forwarder
from scripts.validation.host_forward_signature import canonical,DOMAIN

def observe_case(target,*,case,key,shared_token,revision):
    """Observe only; never writes a skill."""
    return _run(target,case=case,key=key,shared_token=shared_token,revision=revision,intake=False)

def observe_and_accumulate_candidate(target,*,case,key,shared_token,revision):
    """Explicit fresh observation and Q2 intake. No approval, activation or retry."""
    return _run(target,case=case,key=key,shared_token=shared_token,revision=revision,intake=True)

def _run(target,*,case,key,shared_token,revision,intake):
    denied=dict(source.FALSE_TRUST,source_prerequisites_passed=False,numeric_pass=False,
        admitted=False,recorded=False,dispatch_recorded=False,quality_level="Q0",stage="disabled",reason="case_runner_disabled")
    if not source.enabled():return denied
    stage="target";registered=False
    try:
        source.registry.specification(case);target.validate()
        now=time.time_ns()
        ctx=dict(root_id=source.ROOT_ID,revision=revision,batch_id=str(uuid.uuid4()),case=case,
            run_id=str(uuid.uuid4()),nonce=uuid.uuid4().hex,target_ip=target.ip,target_port=target.port,
            container_id=target.container_id,image_id=target.image_id,started_at=target.started_at,
            dispatch_not_before_ns=now,dispatch_not_after_ns=now+60_000_000_000)
        stage="dispatch"
        approval=case_signing.dispatch(key,context=ctx)
        if not source.record_dispatch(ctx,approval)["recorded"]:raise ValueError("dispatch_rejected")
        registered=True;stage="execution"
        got=forwarder.forward_case_request(target,case=case,run_id=ctx["run_id"],nonce=ctx["nonce"],shared_token=shared_token)
        stage="verification"
        body=dict(root_id=source.ROOT_ID,revision=revision,observation=got["observation"])
        envelope=canonical(dict(payload=body,signature=key.sign(DOMAIN+canonical(body)).hex())).decode()
        record=dict(observation_document=envelope,
            stdout=got["result"]["stdout"],stderr=got["result"]["stderr"])
        if intake:
            stage="candidate"
            result=candidate.accumulate_candidate(case=case,record=record)
            recorded=result["recorded"] is True
            return dict(result,source_prerequisites_passed=recorded,numeric_pass=recorded,
                admitted=False,dispatch_recorded=True,stage="complete" if recorded else "candidate")
        result=source.inspect_case(case=case,record=record)
        return dict(result,admitted=False,recorded=False,dispatch_recorded=True,stage="complete" if result["source_prerequisites_passed"] else "verification")
    except Exception:
        # Retain dispatch record, but never expose peer exception text or secrets.
        return dict(denied,stage=stage,dispatch_recorded=registered,reason="case_runner_failed")
