"""Operator-approved public root only; no private key, service or trust grant.

Approval recorded from the owner's explicit conversation on 2026-10-08.
Scope: fixed VAL-1 host observer. This does not approve an executor deployment,
run, parameters, physics, expiry policy, or positive skill consumption.
"""
import hashlib
ROOT_ID="acou-host-val1-20261008"
PUBLIC_KEY_HEX="9ea06200220aa5d6e7a8e78a379045cc958717e592e9b92861c25527a817e1b3"
FINGERPRINT="215f1433cb5a79493cd95e73fd2395ada40e7c31c9182f0bd731f979f7387cf2"
PROFILE="val1-plane-wave-v2"

def approved_public_key():
    raw=bytes.fromhex(PUBLIC_KEY_HEX)
    if len(raw)!=32 or hashlib.sha256(raw).hexdigest()!=FINGERPRINT:
        raise ValueError("approved_public_root_pin_mismatch")
    return raw

def root_statement():
    approved_public_key()
    return {"root_id":ROOT_ID,"public_key_hex":PUBLIC_KEY_HEX,
        "sha256_raw_public_key":FINGERPRINT,"profile":PROFILE,
        "owner_approved":True,"approval_date":"2026-10-08",
        "approval_scope":"fixed_val1_observer_only","runtime_approved":False,
        "trusted_run":False,"parameters_bound":False,"run_bound":False,
        "production_enrolled":False}
