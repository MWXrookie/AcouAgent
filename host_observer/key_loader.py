"""Host-only encrypted key loader. Never prints or persists decrypted keys."""
import os
import stat
from scripts.validation.approved_host_root import approved_public_key

def load_operator_key(path,password):
    if os.name!="posix" or type(password) is not str:
        raise ValueError("invalid_operator_key_input")
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
    try:
        metadata=os.fstat(fd)
        if (not stat.S_ISREG(metadata.st_mode) or metadata.st_uid!=os.geteuid()
                or stat.S_IMODE(metadata.st_mode)&0o077 or metadata.st_nlink!=1
                or not 0<metadata.st_size<=8192):
            raise ValueError("unsafe_operator_key_file")
        with os.fdopen(fd,"rb",closefd=False) as stream:raw=stream.read(8193)
        if len(raw)>8192 or not raw.startswith(b"-----BEGIN ENCRYPTED PRIVATE KEY-----"):
            raise ValueError("encrypted_operator_key_required")
    finally:os.close(fd)
    from cryptography.hazmat.primitives.serialization import load_pem_private_key,Encoding,PublicFormat
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    try:key=load_pem_private_key(raw,password=password.encode())
    except (ValueError,TypeError):raise ValueError("operator_key_unlock_failed") from None
    if (not isinstance(key,Ed25519PrivateKey)
            or key.public_key().public_bytes(Encoding.Raw,PublicFormat.Raw)!=approved_public_key()):
        raise ValueError("operator_key_not_approved")
    return key
