import hashlib
import os
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding,PrivateFormat,PublicFormat,BestAvailableEncryption
from scripts.validation import approved_host_root as policy
from host_observer import key_loader
from host_observer.signing import forward_with_approved_key

def test_owner_approved_public_pin_does_not_grant_runtime():
    raw=policy.approved_public_key()
    assert len(raw)==32 and hashlib.sha256(raw).hexdigest()==policy.FINGERPRINT
    statement=policy.root_statement()
    assert statement["owner_approved"]
    assert not any(statement[k] for k in ("runtime_approved","trusted_run","parameters_bound","run_bound","production_enrolled"))

def encrypted(tmp_path):
    key=Ed25519PrivateKey.generate();password="test-only-password-123"
    path=tmp_path/"test.pem"
    path.write_bytes(key.private_bytes(Encoding.PEM,PrivateFormat.PKCS8,BestAvailableEncryption(password.encode())))
    path.chmod(0o600)
    return key,password,path

def test_loader_unlocks_only_matching_operator_test_key(tmp_path,monkeypatch):
    key,password,path=encrypted(tmp_path)
    raw=key.public_key().public_bytes(Encoding.Raw,PublicFormat.Raw)
    monkeypatch.setattr(key_loader,"approved_public_key",lambda:raw)
    loaded=key_loader.load_operator_key(path,password)
    assert loaded.public_key().public_bytes(Encoding.Raw,PublicFormat.Raw)==raw
    with pytest.raises(ValueError,match="unlock_failed"):key_loader.load_operator_key(path,"wrong")

def test_loader_rejects_other_key_and_unsafe_files(tmp_path):
    key,password,path=encrypted(tmp_path)
    with pytest.raises(ValueError,match="not_approved"):key_loader.load_operator_key(path,password)
    path.chmod(0o640)
    with pytest.raises(ValueError,match="unsafe"):key_loader.load_operator_key(path,password)
    path.chmod(0o600)
    link=tmp_path/"link.pem";link.symlink_to(path)
    with pytest.raises(OSError):key_loader.load_operator_key(link,password)
    hard=tmp_path/"hard.pem";os.link(path,hard)
    with pytest.raises(ValueError,match="unsafe"):key_loader.load_operator_key(path,password)

def test_wrong_approved_signer_never_forwards(monkeypatch):
    from host_observer import signing
    calls=[]
    monkeypatch.setattr(signing,"forward_fixed_request",lambda *a,**k:calls.append(True))
    with pytest.raises(ValueError,match="not_approved"):
        forward_with_approved_key(None,run_id="",nonce="",shared_token="",key=Ed25519PrivateKey.generate())
    assert not calls
