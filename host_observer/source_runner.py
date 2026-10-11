"""Explicit host-only source intake coordinator; no daemon, enrollment or key loading."""
import time
import uuid
import hashlib
from app import controlled_dispatch,config,skill_authority as authority,skill_fixed_pipeline as pipeline
from app import skill_reference_registry as registry
from host_observer.authority_signing import dispatch_approval,skill_reference_policy
from host_observer.signing import forward_with_approved_key
from scripts.validation.approved_host_root import ROOT_ID
from scripts.validation.host_forward_signature import canonical,unique,reject
import json

def observe_fixed_source(target,*,key,shared_token,revision,scenario_type="initial_pressure_2d",
                         reference_policy_window=None):
    """Caller supplies the already unlocked approved key and explicit target.

    An existing approved root policy is mandatory. No flags are changed and no
    source is run unless its exact independent dispatch approval is recorded.
    Optional repeated reference approval requires an explicit bounded window;
    omission never signs or registers a reference policy.
    """
    denied=dict(recorded=False,admitted=False,reference_policy_registered=False,
        actual_use_authenticated=False,reason="source_runner_disabled")
    if config.SKILL_LIBRARY_ENABLED is not True or config.SKILL_LEARNING_ENABLED is not True:return denied
    try:
        if scenario_type not in {"initial_pressure_2d","plane_wave_2d"}:raise ValueError("fixed_scene")
        # Validate optional authorization BEFORE a request. Invalid scope must
        # not consume a run or send HTTP.
        if reference_policy_window is not None:
            if (type(reference_policy_window) is not tuple or len(reference_policy_window)!=2
                    or any(type(v) is not int for v in reference_policy_window)
                    or not 0<=reference_policy_window[0]<=time.time_ns()<reference_policy_window[1]<=2**63-1
                    or reference_policy_window[1]-reference_policy_window[0]>86400_000_000_000):
                raise ValueError("explicit_reference_window")
            if not registry.decisions.enabled():raise ValueError("reference_registration_disabled")
        target.validate();first=time.time_ns()
        context=dict(root_id=ROOT_ID,revision=revision,run_id=str(uuid.uuid4()),nonce=uuid.uuid4().hex,
            target_ip=target.ip,target_port=target.port,container_id=target.container_id,
            image_id=target.image_id,started_at=target.started_at,
            dispatch_not_before_ns=first,dispatch_not_after_ns=first+60_000_000_000)
        approved=authority.record_dispatch(context,dispatch_approval(key,context=context))
        if not approved["recorded"]:return dict(denied,reason="source_dispatch_rejected")
        observed=forward_with_approved_key(target,run_id=context["run_id"],nonce=context["nonce"],
            shared_token=shared_token,key=key,revision=revision)
        raw=observed["result"]
        outcome=pipeline.accumulate(observation_document=observed["signed_observation"],
            stdout=raw["stdout"],stderr=raw["stderr"],scenario_type=scenario_type)
        result=dict(denied,recorded=outcome["recorded"],admitted=outcome["admitted"],
            dispatch_recorded=True,run_id=context["run_id"],reason=outcome["reason"])
        if outcome.get("skill_id"):result.update(skill_id=outcome["skill_id"],version=outcome["version"])
        if outcome["admitted"] and reference_policy_window is not None:
            # Source admission may race withdrawal; registry repeats all source
            # and lifecycle checks under its own authoritative transaction.
            params=controlled_dispatch.fixed_retrieval_parameters()
            payload=json.loads(observed["signed_observation"],object_pairs_hook=unique,parse_constant=reject)["payload"]
            statement=skill_reference_policy(key,skill_id=outcome["skill_id"],version=outcome["version"],
                parameters_sha256=hashlib.sha256(canonical(params)).hexdigest(),
                observation_sha256=hashlib.sha256(canonical(payload)).hexdigest(),revision=revision,
                valid_from_ns=reference_policy_window[0],valid_until_ns=reference_policy_window[1],
                scenario_type=scenario_type)
            registered=registry.register(dict(skill_id=outcome["skill_id"],version=outcome["version"],
                approval_document=statement,observation_document=observed["signed_observation"],
                stdout=raw["stdout"],stderr=raw["stderr"]))
            result["reference_policy_registered"]=registered["recorded"]
        return result
    except Exception:
        # Persisted approved dispatch remains for diagnosis. No automatic retry,
        # third-party exception text, credential, raw response or key is emitted.
        return dict(denied,reason="source_runner_failed")
