import copy
import pytest
from app.controlled_request_adapter import make_envelope,verify_envelope
RUN="12345678-1234-4234-8234-123456789012"
NONCE="a"*32

def test_context_is_fixed_and_still_unauthenticated():
    e=make_envelope(RUN,NONCE)
    result=verify_envelope(e,expected_run_id=RUN,expected_nonce=NONCE)
    assert result["request_context_matched"]
    assert all(not v for k,v in result.items() if k!="request_context_matched")
    assert not verify_envelope(e,expected_run_id=RUN,expected_nonce="b"*32)["request_context_matched"]

@pytest.mark.parametrize("field",["run_id","nonce","code_sha256","configuration_sha256","timeout_seconds","profile","version","code","extra"])
def test_drift_rejected_even_if_caller_rehashes(field):
    import hashlib,json
    e=make_envelope(RUN,NONCE)
    if field=="code":e["code"]+="\nprint(1)"
    elif field=="extra":e["authenticated"]=True
    else:e["context"][field]="changed"
    e["context_sha256"]=hashlib.sha256(json.dumps(e["context"],sort_keys=True,separators=(",",":")).encode()).hexdigest()
    assert not verify_envelope(e,expected_run_id=RUN,expected_nonce=NONCE)["request_context_matched"]
