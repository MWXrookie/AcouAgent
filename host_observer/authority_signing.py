"""Host-only owner statements. No enrollment, forwarding, or trust grant.

These helpers require an already manually unlocked approved key. They never
load a private file or infer approval/expiry. Not imported by the gateway.
"""
import time
from scripts.validation import approved_host_root as approved
from app import skill_authority as authority

def _sign(key,payload):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding,PublicFormat
    if (not isinstance(key,Ed25519PrivateKey)
            or key.public_key().public_bytes(Encoding.Raw,PublicFormat.Raw)!=approved.approved_public_key()):
        raise ValueError("operator_key_not_approved")
    return authority.canonical({"payload":payload,
        "signature":key.sign(authority.DOMAIN+authority.canonical(payload)).hex()}).decode()

def root_policy(key,*,revision,action,valid_from_ns,valid_until_ns):
    now=time.time_ns()
    if (type(revision) is not int or not 1<=revision<=2147483647
            or action not in {"approve","revoke"} or type(valid_from_ns) is not int
            or type(valid_until_ns) is not int or not 0<=valid_from_ns<=now<valid_until_ns<=2**63-1
            or valid_until_ns-valid_from_ns>86400_000_000_000):
        raise ValueError("explicit_owner_policy_required")
    payload=dict(purpose="root_lifecycle",root_id=approved.ROOT_ID,profile=approved.PROFILE,
                 revision=revision,action=action,valid_from_ns=valid_from_ns,valid_until_ns=valid_until_ns)
    return _sign(key,payload)

def dispatch_approval(key,*,context):
    digest=authority._context(context,time.time_ns())
    payload=dict(purpose="approve_run",root_id=approved.ROOT_ID,profile=approved.PROFILE,
                 revision=context["revision"],run_id=context["run_id"],nonce=context["nonce"],
                 context_sha256=digest,**authority.fixed_scope())
    return _sign(key,payload)


def generation_reference_approval(key, *, payload):
    """Explicit bounded reference grant; signing only, no DB/key loading.

    The operator receives the prepared decision and verified source summary.
    This signature is specific to one generation intent, not blanket reuse.
    """
    from app import skill_reference_consumer as consumer
    import re
    import uuid
    if type(payload) is not dict or set(payload)!=consumer.FIELDS:
        raise ValueError("explicit_reference_scope_required")
    now=time.time_ns()
    if (payload["purpose"]!="approve_generation_reference"
            or payload["scenario_type"] not in {"plane_wave_2d","initial_pressure_2d"}
            or payload["root_id"]!=approved.ROOT_ID or payload["profile"]!=approved.PROFILE
            or type(payload["revision"]) is not int or not 1<=payload["revision"]<=2147483647
            or type(payload["skill_version"]) is not int or payload["skill_version"]<1
            or type(payload["skill_id"]) is not str or not 1<=len(payload["skill_id"])<=128
            or type(payload["expires_ns"]) is not int or not now<payload["expires_ns"]<=now+30_000_000_000):
        raise ValueError("explicit_reference_policy")
    for field in ("decision_id","generation_run_id"):
        if type(payload[field]) is not str or str(uuid.UUID(payload[field]))!=payload[field]:
            raise ValueError("explicit_reference_identity")
    if type(payload["decision_nonce"]) is not str or not re.fullmatch("[0-9a-f]{32}",payload["decision_nonce"]):
        raise ValueError("explicit_reference_nonce")
    for field in ("parameters_sha256","generation_context_sha256","observation_sha256","validator_sha256"):
        if type(payload[field]) is not str or not re.fullmatch("[0-9a-f]{64}",payload[field]):
            raise ValueError("explicit_reference_digest")
    authority._run_scope(payload)
    if payload["validator_sha256"]!=consumer.pins.VALIDATOR_SHA256:
        raise ValueError("explicit_reference_validator")
    return _sign(key,dict(payload))


def skill_reference_policy(key, *, skill_id, version, parameters_sha256,
                          observation_sha256, revision, valid_from_ns, valid_until_ns,
                          scenario_type="plane_wave_2d"):
    """Approve one exact verified skill version for repeated reference handoffs.

    Root withdrawal, source validity, lifecycle and per-generation transaction
    checks remain mandatory. No DB enrollment, activation or execution occurs.
    """
    import re
    from app import skill_reference_consumer as consumer
    now=time.time_ns()
    if (scenario_type not in {"plane_wave_2d","initial_pressure_2d"}
            or type(skill_id) is not str or not 1<=len(skill_id)<=128
            or type(version) is not int or version<1 or type(revision) is not int
            or not 1<=revision<=2147483647 or type(valid_from_ns) is not int
            or type(valid_until_ns) is not int or not 0<=valid_from_ns<=now<valid_until_ns<=2**63-1
            or valid_until_ns-valid_from_ns>86400_000_000_000
            or any(type(v) is not str or not re.fullmatch("[0-9a-f]{64}",v)
                   for v in (parameters_sha256,observation_sha256))):
        raise ValueError("explicit_reference_policy_required")
    payload=dict(purpose="approve_skill_reference_policy",scenario_type=scenario_type,root_id=approved.ROOT_ID,
        profile=approved.PROFILE,revision=revision,skill_id=skill_id,skill_version=version,
        parameters_sha256=parameters_sha256,observation_sha256=observation_sha256,
        validator_sha256=consumer.pins.VALIDATOR_SHA256,valid_from_ns=valid_from_ns,
        valid_until_ns=valid_until_ns,**authority.fixed_scope())
    return _sign(key,payload)
