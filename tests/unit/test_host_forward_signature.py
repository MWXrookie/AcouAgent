import json
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from host_observer.signing import forward_and_sign
from scripts.validation.host_forward_signature import verify_forward_signature
from tests.unit.test_host_forwarder import endpoint,RUN,NONCE,TOKEN

def test_actual_forwarding_to_signed_public_key_diagnostic(endpoint):
    target,server,calls=endpoint
    key=Ed25519PrivateKey.generate()
    seed=key.private_bytes(serialization.Encoding.Raw,serialization.PrivateFormat.Raw,serialization.NoEncryption())
    public=key.public_key().public_bytes(serialization.Encoding.Raw,serialization.PublicFormat.Raw)
    result=forward_and_sign(target,run_id=RUN,nonce=NONCE,shared_token=TOKEN,
                            private_seed=seed,root_id="temporary-test-root",revision=1)
    raw=result["signed_observation"]
    # This expected value is a test fixture, not authenticated production context.
    expected=json.loads(raw)["payload"]
    checked=verify_forward_signature(raw,public_key=public,expected_payload=expected)
    assert checked["signature_matched"] and all(not v for k,v in checked.items() if k not in ("signature_matched","reason"))
    assert TOKEN.encode() not in raw and seed not in raw and b"actual-output" not in raw
    expected["observation"]["nonce"]="b"*32
    assert not verify_forward_signature(raw,public_key=public,expected_payload=expected)["signature_matched"]
    wrong=Ed25519PrivateKey.generate().public_key().public_bytes(serialization.Encoding.Raw,serialization.PublicFormat.Raw)
    assert not verify_forward_signature(raw,public_key=wrong,expected_payload=json.loads(raw)["payload"])["signature_matched"]
