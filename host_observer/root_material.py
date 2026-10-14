"""Operator-only offline encrypted root material; never approves/enrolls a root.

Run manually on the Ubuntu host. Not copied into the gateway image.
"""
import argparse
import getpass
import hashlib
import json
import os
from pathlib import Path

def generate_offline(directory, password):
    if os.name!="posix":
        raise ValueError("ubuntu_host_required")
    if type(password) is not str or len(password)<16:
        raise ValueError("passphrase_minimum_16_characters")
    directory=Path(directory).resolve(strict=True)
    if not directory.is_dir():raise ValueError("existing_operator_directory_required")
    private=directory/"observer-private.pem"
    public=directory/"observer-public.json"
    if private.exists() or public.exists():
        raise ValueError("root_material_already_exists")
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import (
        Encoding,PrivateFormat,PublicFormat,BestAvailableEncryption)
    key=Ed25519PrivateKey.generate()
    encrypted=key.private_bytes(Encoding.PEM,PrivateFormat.PKCS8,BestAvailableEncryption(password.encode()))
    raw=key.public_key().public_bytes(Encoding.Raw,PublicFormat.Raw)
    result={"algorithm":"Ed25519","public_key_hex":raw.hex(),
            "sha256_raw_public_key":hashlib.sha256(raw).hexdigest(),
            "approved":False,"enrolled":False}
    # Exclusive creation: never overwrite an existing key. No permission changes
    # to any existing file. A partial write leaves files for the owner to inspect.
    for path,data in ((private,encrypted),(public,(json.dumps(result,indent=2)+"\n").encode())):
        fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
        with os.fdopen(fd,"wb") as stream:
            stream.write(data);stream.flush();os.fsync(stream.fileno())
    return result

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory",required=True)
    args=parser.parse_args()
    password=getpass.getpass("New observer passphrase (>=16 characters): ")
    confirm=getpass.getpass("Repeat passphrase: ")
    if password!=confirm:raise SystemExit("Passphrases differ; no files written")
    try:result=generate_offline(args.directory,password)
    except (ValueError,OSError,ImportError) as exc:
        # No file contents, private values, passwords or third-party exception text.
        raise SystemExit("Root generation failed: "+type(exc).__name__)
    print(json.dumps(result,indent=2))

if __name__=="__main__":main()
