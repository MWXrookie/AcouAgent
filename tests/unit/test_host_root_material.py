import hashlib
import json
import os
import stat
import pytest
from cryptography.hazmat.primitives.serialization import load_pem_private_key,Encoding,PublicFormat
from host_observer.root_material import generate_offline

def test_operator_material_is_encrypted_exclusive_and_unapproved(tmp_path):
    password="test-only-passphrase-123"
    result=generate_offline(tmp_path,password)
    private=tmp_path/"observer-private.pem";public=tmp_path/"observer-public.json"
    assert b"ENCRYPTED PRIVATE KEY" in private.read_bytes()
    with pytest.raises((ValueError,TypeError)):load_pem_private_key(private.read_bytes(),password=None)
    key=load_pem_private_key(private.read_bytes(),password=password.encode())
    raw=key.public_key().public_bytes(Encoding.Raw,PublicFormat.Raw)
    assert result["public_key_hex"]==raw.hex()
    assert result["sha256_raw_public_key"]==hashlib.sha256(raw).hexdigest()
    assert not result["approved"] and not result["enrolled"]
    assert json.loads(public.read_text())==result
    assert stat.S_IMODE(private.stat().st_mode)==0o600
    before=private.read_bytes()
    with pytest.raises(ValueError):generate_offline(tmp_path,password)
    assert private.read_bytes()==before

def test_invalid_password_never_writes_material(tmp_path):
    with pytest.raises(ValueError):generate_offline(tmp_path,"short")
    assert not list(tmp_path.iterdir())
