"""Operator-only one-shot observer. No daemon, production DB or skill grant."""
import argparse
import getpass
import json
import os
import stat
import time
import uuid
from pathlib import Path
from host_observer.forwarder import Target,canonical,unique,reject
from host_observer.key_loader import load_operator_key
from host_observer.token_loader import read_project_token
from host_observer.signing import forward_with_approved_key
from scripts.validation.approved_host_root import ROOT_ID,root_statement
from scripts.validation import plane_wave_run_binding as binding

def read_target(path):
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError("regular_target_configuration_required")
        with os.fdopen(fd,"rb",closefd=False) as stream:raw=stream.read(4097)
    finally:os.close(fd)
    if len(raw)>4096:raise ValueError("target_configuration_budget")
    value=json.loads(raw.decode("utf-8"),object_pairs_hook=unique,parse_constant=reject)
    if type(value) is not dict or set(value)!={"ip","port","container_id","image_id","started_at"}:
        raise ValueError("invalid_operator_target_configuration")
    target=Target(**value);target.validate()
    return target

def exclusive_json(path,value):
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,"wb") as stream:
        stream.write(canonical(value));stream.flush();os.fsync(stream.fileno())

def observe_once(target,key,shared_token,output_directory):
    directory=Path(output_directory).resolve(strict=True)
    if not directory.is_dir():raise ValueError("existing_evidence_directory_required")
    root_statement() # Validate the independently approved public pin first.
    run_id=str(uuid.uuid4());nonce=uuid.uuid4().hex
    first=time.time_ns()
    dispatch={"root_id":ROOT_ID,"revision":1,"run_id":run_id,"nonce":nonce,
        "target_ip":target.ip,"target_port":target.port,"container_id":target.container_id,
        "image_id":target.image_id,"started_at":target.started_at,
        "dispatch_not_before_ns":first,"dispatch_not_after_ns":first+60_000_000_000}
    # Durable dispatch exists before forwarding. A failed attempt leaves this
    # record for diagnosis; never automatically retry or overwrite evidence.
    exclusive_json(directory/(run_id+"-dispatch.json"),dispatch)
    result=forward_with_approved_key(target,run_id=run_id,nonce=nonce,
                                    shared_token=shared_token,key=key)
    last=time.time_ns()
    envelope=json.loads(result["signed_observation"].decode("utf-8"),object_pairs_hook=unique,parse_constant=reject)
    from scripts.validation.host_forward_binding import inspect_binding
    context=dict(dispatch,registered_ns=first,now_ns=last)
    check=inspect_binding(envelope["payload"],context)
    if not check["context_matched"]:raise ValueError("one_shot_context_mismatch")
    raw=result["result"]
    code=(binding.DIRECTORY/"recipes/val1_plane_wave_v2.py").read_text().strip()
    import hashlib
    if (binding.digest(code)!=binding.RECIPE_SHA256
            or hashlib.sha256((binding.DIRECTORY/"plane_wave_evidence.py").read_bytes()).hexdigest()!=binding.VALIDATOR_SHA256):
        raise ValueError("one_shot_validator_pin_changed")
    from scripts.validation.plane_wave_evidence import validate_plane_wave
    numeric=validate_plane_wave(json.loads(raw["stdout"],object_pairs_hook=unique,parse_constant=reject))
    exclusive_json(directory/(run_id+"-result.json"),{"signed_observation":envelope,
        "raw_result":raw,"numeric_diagnostic":numeric,"authenticated":False,
        "runtime_approved":False,"trusted_run":False,"parameters_bound":False,"run_bound":False})
    return {"run_id":run_id,"numeric_pass":numeric["numeric_pass"],"authenticated":False,
        "trusted_run":False,"reason":"one_shot_evidence_only"}

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target-config",required=True)
    parser.add_argument("--output-directory",required=True)
    parser.add_argument("--token-from-project-env",action="store_true",
        help="Read existing project token locally without displaying or modifying it")
    args=parser.parse_args()
    try:
        target=read_target(args.target_config)
        token=read_project_token() if args.token_from_project_env else None
        key=load_operator_key(Path.home()/".local/share/acouagent-observer/observer-private.pem",
                              getpass.getpass("Observer private-key passphrase: "))
        if token is None:
            token=getpass.getpass("Existing executor shared token (not logged): ")
        result=observe_once(target,key,token,args.output_directory)
    except Exception as error:
        # Do not print third-party exception text, credentials or response data.
        raise SystemExit("Observer failed: "+type(error).__name__)
    print(json.dumps(result,indent=2))

if __name__=="__main__":main()
