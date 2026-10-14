import json
from scripts.validation import host_envelope_prototype as prototype

KEY=b"k"*32
CONTEXT={"run_id":"r","nonce":"a"*32,"configuration_sha256":"b"*64,
         "container_id":"c"*64,"image_id":"sha256:"+ "d"*64,
         "started_ns":1,"request_window":[2,3],"stdout_sha256":"e"*64}


def document(payload=CONTEXT):
    return json.dumps(prototype.test_envelope(payload,KEY)).encode()


def test_valid_test_envelope_never_authenticates_host():
    out=prototype.inspect_test_envelope(document(),KEY,expected=CONTEXT)
    assert out["envelope_matched"]
    assert all(out[k] is False for k in ("authenticated","request_observed","trusted_run","runtime_approved","run_bound","parameters_bound"))


def test_tamper_wrong_root_revocation_and_cross_request_fail():
    data=json.loads(document());data["payload"]["run_id"]="other"
    assert not prototype.inspect_test_envelope(json.dumps(data).encode(),KEY,expected=CONTEXT)["envelope_matched"]
    assert not prototype.inspect_test_envelope(document(),b"x"*32,expected=CONTEXT)["envelope_matched"]
    assert not prototype.inspect_test_envelope(document(),KEY,expected=CONTEXT,revoked=True)["envelope_matched"]
    assert not prototype.inspect_test_envelope(document(),KEY,expected=dict(CONTEXT,nonce="z"*32))["envelope_matched"]


def test_duplicate_nonfinite_oversize_and_same_request_replay_scope():
    for bad in (b'{"payload":{},"payload":{},"mac":"x"}',b'{"payload":NaN,"mac":"x"}',b"x"*16385):
        assert not prototype.inspect_test_envelope(bad,KEY,expected=CONTEXT)["envelope_matched"]
    assert prototype.inspect_test_envelope(document(),KEY,expected=CONTEXT)["envelope_matched"]
    assert prototype.inspect_test_envelope(document(),KEY,expected=CONTEXT)["envelope_matched"]
