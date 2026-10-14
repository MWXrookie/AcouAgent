"""Host-only forwarding + signing entry; never generates or stores a key."""
from host_observer.forwarder import forward_fixed_request,canonical
DOMAIN=b"acouagent.host-forward-observation.v1\x00"
def forward_and_sign(target, *, run_id, nonce, shared_token, private_seed, root_id, revision):
    import re
    if (type(private_seed) is not bytes or len(private_seed)!=32
            or type(root_id) is not str or not re.fullmatch("[A-Za-z0-9_-]{1,64}",root_id)
            or type(revision) is not int or revision<1):
        raise ValueError("invalid_host_signer_configuration")
    # Missing dependency fails before forwarding; no HMAC fallback.
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    key=Ed25519PrivateKey.from_private_bytes(private_seed)
    observed=forward_fixed_request(target,run_id=run_id,nonce=nonce,shared_token=shared_token)
    payload={"root_id":root_id,"revision":revision,"observation":observed["observation"]}
    body=canonical(payload)
    signature=key.sign(DOMAIN+body).hex()
    return {"result":observed["result"],"signed_observation":canonical({"payload":payload,"signature":signature})}


def forward_with_approved_key(target, *, run_id, nonce, shared_token, key, revision=1):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding,PublicFormat
    from scripts.validation.approved_host_root import approved_public_key,ROOT_ID
    if (type(revision) is not int or not 1<=revision<=2147483647
            or not isinstance(key,Ed25519PrivateKey)
            or key.public_key().public_bytes(Encoding.Raw,PublicFormat.Raw)!=approved_public_key()):
        raise ValueError("operator_key_not_approved")
    observed=forward_fixed_request(target,run_id=run_id,nonce=nonce,shared_token=shared_token)
    payload={"root_id":ROOT_ID,"revision":revision,"observation":observed["observation"]}
    return {"result":observed["result"],"signed_observation":canonical({
        "payload":payload,"signature":key.sign(DOMAIN+canonical(payload)).hex()})}


def forward_consistency_with_approved_key(target, *, case, run_id, nonce, shared_token, key, revision):
    """Only explicit separately authorized consistency cases; no key loading."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding,PublicFormat
    from scripts.validation.approved_host_root import approved_public_key
    from app.skill_consistency_source import ROOT_ID
    from host_observer.forwarder import forward_consistency_request
    if (type(revision) is not int or not 1<=revision<=2147483647
            or not isinstance(key,Ed25519PrivateKey)
            or key.public_key().public_bytes(Encoding.Raw,PublicFormat.Raw)!=approved_public_key()):
        raise ValueError("operator_key_not_approved")
    observed=forward_consistency_request(target,case=case,run_id=run_id,nonce=nonce,shared_token=shared_token)
    payload={"root_id":ROOT_ID,"revision":revision,"observation":observed["observation"]}
    return {"result":observed["result"],"signed_observation":canonical({
        "payload":payload,"signature":key.sign(DOMAIN+canonical(payload)).hex()})}
