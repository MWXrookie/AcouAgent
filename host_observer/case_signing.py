"""Scoped signatures using only an explicitly unlocked owner key; no key I/O."""
import time
from app import skill_case_source as source
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
    body=dict(purpose="case_root_lifecycle",root_id=source.ROOT_ID,profile=source.PROFILE,
        revision=revision,action=action,valid_from_ns=valid_from_ns,valid_until_ns=valid_until_ns,
        manifest_sha256=source.manifest()["sha256"])
    if type(revision) is not int or not 1<=revision<=2147483647:raise ValueError("explicit_revision")
    source._window(body,time.time_ns())
    return _sign(key,body)

def dispatch(key,*,context):
    digest=source._context(context,time.time_ns())
    return _sign(key,dict(purpose="approve_case_run",root_id=source.ROOT_ID,profile=source.PROFILE,
        revision=context["revision"],batch_id=context["batch_id"],case=context["case"],
        run_id=context["run_id"],nonce=context["nonce"],context_sha256=digest,
        manifest_sha256=source.manifest()["sha256"],**source.registry.pins(context["case"])))
