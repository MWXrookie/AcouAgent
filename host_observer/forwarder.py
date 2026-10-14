"""Host-only fixed-recipe forwarding observer; no daemon, keys or trust grant.

Not imported by the gateway. Operator-owned target identity is separate from the
request. No retries, redirects, proxy environment, arbitrary code or URL input.
"""
import hashlib
import http.client
import ipaddress
import json
import re
import socket
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from scripts.validation import executor_host_observation as docker
from scripts.validation.plane_wave_code_configuration import derive_configuration

RECIPE_SHA256="4561ef66b6daa4ffdd40ff6d9c35005fbdb1a3958fa70fb9309a29d4928d6b2b"
MAX_RESPONSE=2*1024*1024
MAX_REQUEST_SECONDS=40
FORMAT=docker.FORMAT[:-1]+',"addresses":"{{range .NetworkSettings.Networks}}{{.IPAddress}} {{end}}"}'

def canonical(value):
    return json.dumps(value,sort_keys=True,separators=(",",":"),allow_nan=False).encode()

def digest(value):
    return hashlib.sha256(value).hexdigest()

@dataclass(frozen=True)
class Target:
    ip: str
    port: int
    container_id: str
    image_id: str
    started_at: str

    def validate(self):
        ip=ipaddress.IPv4Address(self.ip)
        if not (ip.is_private or ip.is_loopback) or ip.is_unspecified or ip.is_multicast or ip.is_reserved:
            raise ValueError("invalid_operator_target")
        if type(self.port) is not int or not 1<=self.port<=65535:
            raise ValueError("invalid_operator_target")
        identity={"container_id":self.container_id,"image_id":self.image_id,
                  "started_at":self.started_at,"running":True,"restart_count":0}
        if not docker._valid(identity):
            raise ValueError("invalid_operator_identity")

def unique(pairs):
    value={}
    for key,item in pairs:
        if key in value:raise ValueError("duplicate_key")
        value[key]=item
    return value

def reject(_):
    raise ValueError("nonfinite_json")

def _snapshot():
    raw=docker._bounded_output(["docker","inspect","--type","container","--format",FORMAT,
                               docker.CONTAINER],timeout=5,limit=4096)
    value=json.loads(raw,object_pairs_hook=unique,parse_constant=reject)
    if type(value) is not dict or set(value)!=docker.FIELDS|{"addresses"}:
        raise ValueError("invalid_host_snapshot")
    identity={k:value[k] for k in docker.FIELDS}
    if not docker._valid(identity) or type(value["addresses"]) is not str:
        raise ValueError("invalid_host_snapshot")
    return value

def _matches(snapshot,target):
    return (snapshot["container_id"]==target.container_id
            and snapshot["image_id"]==target.image_id
            and snapshot["started_at"]==target.started_at
            and snapshot["running"] is True
            and target.ip in snapshot["addresses"].split())

def _forward_recipe(target, *, run_id, nonce, shared_token, code, configuration, profile):
    target.validate()
    if type(run_id) is not str or str(uuid.UUID(run_id))!=run_id:
        raise ValueError("invalid_run")
    if type(nonce) is not str or not re.fullmatch("[0-9a-f]{32}",nonce):
        raise ValueError("invalid_nonce")
    if type(shared_token) is not str or not 32<=len(shared_token)<=256 or not re.fullmatch(r"[A-Za-z0-9._~+/=-]+",shared_token):
        raise ValueError("invalid_host_token")
    request=canonical({"code":code,"timeout_seconds":30,"request_nonce":nonce})
    before=_snapshot()
    if not _matches(before,target):raise ValueError("target_identity_mismatch")
    observed_before=time.time_ns()
    deadline=time.monotonic()+MAX_REQUEST_SECONDS
    connection=http.client.HTTPConnection(target.ip,target.port,timeout=MAX_REQUEST_SECONDS)
    response = None
    deadline_timer = None
    def remaining():
        value = deadline-time.monotonic()
        if value <= 0:
            raise ValueError("target_deadline")
        return value
    try:
        connection.connect()
        transport = connection.sock
        # Interrupt slow header/body trickles too; per-read socket timeouts alone
        # do not impose a total wall-clock deadline.
        def abort_transport():
            try: transport.shutdown(socket.SHUT_RDWR)
            except OSError: pass
        deadline_timer=threading.Timer(remaining(),abort_transport)
        deadline_timer.daemon=True
        deadline_timer.start()
        peer=connection.sock.getpeername()[0]
        if peer!=target.ip:raise ValueError("target_peer_mismatch")
        transport.settimeout(remaining())
        request_start=time.time_ns()
        connection.request("POST","/execute",body=request,
                           headers={"Content-Type":"application/json","X-Executor-Token":shared_token})
        transport.settimeout(remaining())
        response=connection.getresponse()
        if response.status!=200:raise ValueError("target_http_failure")
        lengths=response.headers.get_all("Content-Length",[])
        if (len(lengths)!=1 or not re.fullmatch("[0-9]+",lengths[0])
                or response.headers.get("Transfer-Encoding") is not None):
            raise ValueError("target_response_framing")
        expected_length=int(lengths[0])
        if expected_length>MAX_RESPONSE:raise ValueError("target_response_budget")
        raw=bytearray()
        while True:
            transport.settimeout(remaining())
            chunk=response.read1(min(65536,MAX_RESPONSE+1-len(raw)))
            raw.extend(chunk)
            if len(raw)>MAX_RESPONSE:raise ValueError("target_response_budget")
            if not chunk or response.isclosed():break
        if len(raw)!=expected_length:raise ValueError("target_response_truncated")
        remaining()
        request_end=time.time_ns()
    finally:
        if deadline_timer is not None: deadline_timer.cancel()
        if response is not None: response.close()
        connection.close()
    after=_snapshot()
    observed_after=time.time_ns()
    if before!=after or not _matches(after,target):raise ValueError("target_lifecycle_changed")
    if not observed_before<=request_start<request_end<=observed_after or observed_after-observed_before>60_000_000_000:
        raise ValueError("invalid_observation_window")
    result=json.loads(raw.decode("utf-8"),object_pairs_hook=unique,parse_constant=reject)
    if (type(result) is not dict or type(result.get("stdout")) is not str
            or type(result.get("stderr")) is not str or type(result.get("exit_code")) is not int
            or type(result.get("timed_out")) is not bool):
        raise ValueError("invalid_target_response")
    observation={"version":"host-forward-observation.v1","run_id":run_id,"nonce":nonce,
        "profile":profile,"request_sha256":digest(request),
        "code_sha256":digest(code.encode()),"configuration_sha256":digest(canonical(configuration)),
        "response_sha256":digest(raw),"stdout_sha256":digest(result["stdout"].encode()),
        "stderr_sha256":digest(result["stderr"].encode()),"exit_code":result["exit_code"],
        "timed_out":result["timed_out"],"target_ip":target.ip,"target_port":target.port,
        "peer_ip":peer,"identity":before,"observed_before_ns":observed_before,
        "request_start_ns":request_start,"request_end_ns":request_end,"observed_after_ns":observed_after,
        "authenticated":False,"request_observed":False,"parameters_bound":False,"trusted_run":False}
    return {"result":result,"observation":observation}


def forward_fixed_request(target, *, run_id, nonce, shared_token):
    recipe=Path(__file__).resolve().parents[1]/"scripts/validation/recipes/val1_plane_wave_v2.py"
    code=recipe.read_text().strip()
    if digest(code.encode())!=RECIPE_SHA256:
        raise ValueError("unreviewed_host_recipe")
    return _forward_recipe(target,run_id=run_id,nonce=nonce,shared_token=shared_token,
        code=code,configuration=derive_configuration(code),profile="val1-plane-wave-v2")


def forward_consistency_request(target, *, case, run_id, nonce, shared_token):
    """Only four reviewed source-free recipes; no caller code/configuration."""
    from app.controlled_consistency import PROFILES,configuration_for
    from scripts.validation.consistency_experiment import recipe
    if type(case) is not str or case not in PROFILES:
        raise ValueError("reviewed_consistency_case_required")
    return _forward_recipe(target,run_id=run_id,nonce=nonce,shared_token=shared_token,
        code=recipe(*PROFILES[case]),configuration=configuration_for(case),
        profile="consistency-sourcefree-v2."+case)


def forward_case_request(target, *, case, run_id, nonce, shared_token):
    """Closed case registry only. Caller must register an approved dispatch first."""
    from scripts.validation.case_batch_registry import specification
    configuration,code=specification(case)
    return _forward_recipe(target,run_id=run_id,nonce=nonce,shared_token=shared_token,
        code=code,configuration=configuration,profile="case-source.v1."+case)
