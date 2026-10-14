"""Offline fixed-request adapter. Integrity checks never authenticate a host."""
import hashlib
import json
import re
import uuid
from app.controlled_request import build_request

def _canonical(value):
    return json.dumps(value,sort_keys=True,separators=(",",":"),allow_nan=False)

def make_envelope(run_id, nonce):
    if type(run_id) is not str or str(uuid.UUID(run_id)) != run_id:
        raise ValueError("invalid_run_id")
    if type(nonce) is not str or not re.fullmatch("[0-9a-f]{32}",nonce):
        raise ValueError("invalid_nonce")
    request=build_request('{"version":1,"profile":"val1-plane-wave-v2"}')
    context={"version":1,"profile":"val1-plane-wave-v2","run_id":run_id,
             "nonce":nonce,"code_sha256":request["code_sha256"],
             "configuration_sha256":request["configuration_sha256"],"timeout_seconds":30}
    return {"context":context,"context_sha256":hashlib.sha256(_canonical(context).encode()).hexdigest(),
            "code":request["code"]}

def verify_envelope(envelope, *, expected_run_id, expected_nonce):
    denied={"request_context_matched":False,"trusted_run":False,"parameters_bound":False,
            "run_bound":False,"authenticated":False}
    try:
        if type(envelope) is not dict or set(envelope)!={"context","context_sha256","code"}:
            return denied
        expected=make_envelope(expected_run_id,expected_nonce)
        matched=_canonical(envelope)==_canonical(expected)
        return dict(denied,request_context_matched=matched)
    except (ValueError,TypeError,OverflowError,RecursionError):
        return denied
