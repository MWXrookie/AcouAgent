import json
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding,PublicFormat
from scripts.validation import host_public_key_prototype as p


def fixture():
    private=Ed25519PrivateKey.generate()
    public=private.public_key().public_bytes(Encoding.Raw,PublicFormat.Raw)
    context={"run_id":"run","nonce":"a"*32,"configuration_sha256":"b"*64,
             "image_id":"sha256:"+"c"*64,"container_id":"d"*64,
             "started_ns":1,"window":[2,3],"stdout_sha256":"e"*64}
    signature=private.sign(p.DOMAIN+p.canonical(context)).hex()
    document=json.dumps({"payload":context,"signature":signature}).encode()
    return public,context,document


def test_public_only_consumer_verifies_without_granting_host_trust():
    public,context,document=fixture()
    out=p.inspect_test_signature(document,public,expected=context)
    assert out["signature_matched"]
    assert all(out[k] is False for k in ("authenticated","request_observed","trusted_run","runtime_approved","run_bound","parameters_bound"))
    # Same-context diagnosis is repeatable; no one-time consumption is claimed.
    assert p.inspect_test_signature(document,public,expected=context)["signature_matched"]


def test_wrong_root_tamper_revocation_and_cross_context_reject():
    public,context,document=fixture()
    other,_,_=fixture()
    assert p.inspect_test_signature(document,other,expected=context)["reason"]=="signature_mismatch"
    data=json.loads(document);data["payload"]["run_id"]="forged"
    assert not p.inspect_test_signature(json.dumps(data).encode(),public,expected=context)["signature_matched"]
    assert not p.inspect_test_signature(document,public,expected=context,revoked=True)["signature_matched"]
    assert p.inspect_test_signature(document,public,expected=dict(context,nonce="z"*32))["reason"]=="context_mismatch"


def test_strict_envelope_boundaries():
    public,context,document=fixture()
    for invalid in (b"x"*16385,b'{"payload":{},"payload":{},"signature":"a"}',b'{"payload":NaN,"signature":"a"}',b"\xff"):
        assert not p.inspect_test_signature(invalid,public,expected=context)["signature_matched"]
    data=json.loads(document);data["public_key"]=public.hex()
    assert not p.inspect_test_signature(json.dumps(data).encode(),public,expected=context)["signature_matched"]
