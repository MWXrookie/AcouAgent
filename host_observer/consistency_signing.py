"""Explicit scoped owner signatures; no enrollment/execution/key loading."""
import time
from app import skill_consistency_source as source
from scripts.validation import approved_host_root as approved
from scripts.validation.host_forward_signature import canonical


def _sign(key,body):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding,PublicFormat
    if (not isinstance(key,Ed25519PrivateKey)
            or key.public_key().public_bytes(Encoding.Raw,PublicFormat.Raw)!=approved.approved_public_key()):
        raise ValueError("operator_key_not_approved")
    return canonical(dict(payload=body,signature=key.sign(source.DOMAIN+canonical(body)).hex())).decode()


def policy(key,*,revision,action,valid_from_ns,valid_until_ns):
    body=dict(purpose="consistency_root_lifecycle",root_id=source.ROOT_ID,profile=source.PROFILE,
        revision=revision,action=action,valid_from_ns=valid_from_ns,valid_until_ns=valid_until_ns,
        manifest_sha256=source.manifest()["sha256"])
    if type(revision) is not int or not 1<=revision<=2147483647:raise ValueError("explicit_revision")
    source._window(body,time.time_ns())
    return _sign(key,body)


def dispatch(key,*,context):
    digest=source._context(context,time.time_ns())
    pins=source.manifest()
    body=dict(purpose="approve_consistency_run",root_id=source.ROOT_ID,profile=source.PROFILE,
        revision=context["revision"],batch_id=context["batch_id"],case=context["case"],
        run_id=context["run_id"],nonce=context["nonce"],context_sha256=digest,
        manifest_sha256=pins["sha256"],**pins["recipes"][context["case"]])
    return _sign(key,body)


def reference_policy(key,*,skill_id,version,batch_id,records,revision,valid_from_ns,valid_until_ns):
    """Only an explicitly unlocked owner key; root/run approval is insufficient."""
    from app.skill_consistency_reference import policy_payload
    body=policy_payload(skill_id=skill_id,version=version,batch_id=batch_id,records=records,
        revision=revision,valid_from_ns=valid_from_ns,valid_until_ns=valid_until_ns)
    if type(valid_from_ns) is not int or type(valid_until_ns) is not int or not 0<=valid_from_ns<=time.time_ns()<valid_until_ns<=2**63-1 or valid_until_ns-valid_from_ns>86400_000_000_000:
        raise ValueError("explicit_reference_policy_window")
    return _sign(key,body)
