"""Offline public-key envelope verifier; not a host observer or deployed root.

The public key and expected context are independently selected test inputs.
Successful signature verification never authenticates actual request routing.
Private signing exists only in tests, not in this consumer or gateway.
"""
import json
import re

DOMAIN=b"acouagent.host-public-key.prototype.v1\x00"


def canonical(value):
    return json.dumps(value,sort_keys=True,separators=(",",":"),allow_nan=False).encode()


def inspect_test_signature(document, public_key, *, expected, revoked=False):
    denied={"signature_matched":False,"authenticated":False,"request_observed":False,
        "trusted_run":False,"runtime_approved":False,"run_bound":False,"parameters_bound":False}
    if (not isinstance(document,bytes) or len(document)>16384
            or not isinstance(public_key,bytes) or len(public_key)!=32 or revoked is not False):
        return dict(denied,reason="invalid_or_revoked_test_root")
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    except ImportError:
        return dict(denied,reason="crypto_unavailable")
    try:
        def unique(pairs):
            value={}
            for key,item in pairs:
                if key in value: raise ValueError("duplicate_key")
                value[key]=item
            return value
        def reject(value): raise ValueError("nonfinite")
        data=json.loads(document.decode("utf-8"),object_pairs_hook=unique,parse_constant=reject)
        if (not isinstance(data,dict) or set(data)!={"payload","signature"}
                or not isinstance(data["payload"],dict)
                or not isinstance(data["signature"],str)
                or not re.fullmatch("[0-9a-f]{128}",data["signature"])):
            return dict(denied,reason="invalid_envelope")
        body=canonical(data["payload"])
        if len(body)>8192: return dict(denied,reason="invalid_envelope")
        Ed25519PublicKey.from_public_bytes(public_key).verify(bytes.fromhex(data["signature"]),DOMAIN+body)
        if not isinstance(expected,dict) or body!=canonical(expected):
            return dict(denied,reason="context_mismatch")
        return dict(denied,signature_matched=True,reason="test_signature_only")
    except InvalidSignature:
        return dict(denied,reason="signature_mismatch")
    except (ValueError,TypeError,UnicodeError,RecursionError):
        return dict(denied,reason="invalid_envelope")
