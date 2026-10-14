"""Public-key diagnostic consumer; a signature is not approved provenance."""
import json
import re
def canonical(value):
    return json.dumps(value,sort_keys=True,separators=(",",":"),allow_nan=False).encode()
def unique(pairs):
    out={}
    for key,value in pairs:
        if key in out:raise ValueError("duplicate_key")
        out[key]=value
    return out
def reject(_):
    raise ValueError("nonfinite_json")
DOMAIN=b"acouagent.host-forward-observation.v1\x00"

def verify_forward_signature(document, *, public_key, expected_payload):
    denied={"signature_matched":False,"authenticated":False,"request_observed":False,
            "trusted_run":False,"parameters_bound":False,"run_bound":False,"runtime_approved":False}
    if type(document) is not bytes or len(document)>16384 or type(public_key) is not bytes or len(public_key)!=32:
        return dict(denied,reason="invalid_signature_input")
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    except ImportError:
        return dict(denied,reason="crypto_unavailable")
    try:
        data=json.loads(document.decode("utf-8"),object_pairs_hook=unique,parse_constant=reject)
        if (type(data) is not dict or set(data)!={"payload","signature"}
                or type(data["signature"]) is not str or not re.fullmatch("[0-9a-f]{128}",data["signature"])):
            return dict(denied,reason="invalid_signature_envelope")
        body=canonical(data["payload"])
        if len(body)>8192 or body!=canonical(expected_payload):
            return dict(denied,reason="independent_context_mismatch")
        Ed25519PublicKey.from_public_bytes(public_key).verify(bytes.fromhex(data["signature"]),DOMAIN+body)
        return dict(denied,signature_matched=True,reason="signature_diagnostic_only")
    except (ValueError,TypeError,UnicodeError,RecursionError,InvalidSignature):
        return dict(denied,reason="signature_mismatch_or_invalid")
