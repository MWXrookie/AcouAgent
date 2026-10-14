"""Test-only HMAC envelope. Not a production signer, host observer or trust root.

Caller-supplied keys cannot authenticate a host. Shared-key verification also
allows the verifier to forge envelopes; production must separate signing keys.
No persisted keys, HTTP API, Docker access or runtime trust changes.
"""
import hashlib
import hmac
import json

DOMAIN = b"acouagent.host-envelope.prototype.v1\x00"
MAX_BYTES = 16384


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def test_envelope(payload, key):
    """For isolated tests only; never provision or persist credentials."""
    if not isinstance(key, bytes) or len(key) < 32 or not isinstance(payload, dict):
        raise ValueError("invalid_test_envelope")
    body=canonical(payload)
    if len(body)>8192:
        raise ValueError("payload_size")
    return {"payload": payload, "mac": hmac.new(key,DOMAIN+body,hashlib.sha256).hexdigest()}


def inspect_test_envelope(document, key, *, expected, revoked=False):
    """Envelope integrity diagnostic; every host/runtime trust field stays false."""
    denied={"envelope_matched":False,"authenticated":False,"request_observed":False,
            "trusted_run":False,"runtime_approved":False,"run_bound":False,"parameters_bound":False}
    try:
        if (revoked is not False or not isinstance(key,bytes) or len(key)<32
                or not isinstance(document,bytes) or len(document)>MAX_BYTES):
            return dict(denied,reason="invalid_or_revoked_test_root")
        def unique(pairs):
            value={}
            for k,v in pairs:
                if k in value: raise ValueError("duplicate_key")
                value[k]=v
            return value
        def reject(value): raise ValueError("nonfinite")
        data=json.loads(document.decode("utf-8"),object_pairs_hook=unique,parse_constant=reject)
        if not isinstance(data,dict) or set(data)!={"payload","mac"}:
            return dict(denied,reason="invalid_envelope")
        body=canonical(data["payload"])
        if len(body)>8192 or not isinstance(data["mac"],str):
            return dict(denied,reason="invalid_envelope")
        if not hmac.compare_digest(data["mac"],hmac.new(key,DOMAIN+body,hashlib.sha256).hexdigest()):
            return dict(denied,reason="mac_mismatch")
        # Independent expected context includes run/nonce/configuration/result
        # and host identity/window. Exact context prevents cross-request replay;
        # same-request replay still needs an atomic consumption ledger.
        if canonical(data["payload"]) != canonical(expected):
            return dict(denied,reason="context_mismatch")
        return dict(denied,envelope_matched=True,reason="test_envelope_only")
    except (ValueError,TypeError,UnicodeError,RecursionError):
        return dict(denied,reason="invalid_envelope")
