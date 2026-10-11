"""Bind a signed host observation to independently selected dispatch context.

Diagnostic only. The caller must obtain expected from server-owned policy and
dispatch records, never reconstruct it from the submitted signed observation.
No production policy/root approval or trust grant is implemented here.
"""
import ipaddress
import re
import uuid
from scripts.validation import executor_host_observation as docker

CONTEXT={"root_id","revision","run_id","nonce","target_ip","target_port",
         "container_id","image_id","started_at","dispatch_not_before_ns",
         "dispatch_not_after_ns","now_ns","registered_ns"}
HASHES={"request_sha256","code_sha256","configuration_sha256","response_sha256",
        "stdout_sha256","stderr_sha256"}
TIMES={"observed_before_ns","request_start_ns","request_end_ns","observed_after_ns"}
FLAGS={"authenticated","request_observed","parameters_bound","trusted_run"}
FIELDS={"version","run_id","nonce","profile","exit_code","timed_out",
        "target_ip","target_port","peer_ip","identity"}|HASHES|TIMES|FLAGS

def _inspect_binding(payload,expected,profile):
    denied={"context_matched":False,"authenticated":False,"request_observed":False,
            "trusted_run":False,"runtime_approved":False,"run_bound":False,"parameters_bound":False}
    try:
        if type(expected) is not dict or set(expected)!=CONTEXT:
            return dict(denied,reason="missing_independent_context")
        if (type(expected["root_id"]) is not str or not re.fullmatch("[A-Za-z0-9_-]{1,64}",expected["root_id"])
                or type(expected["revision"]) is not int or expected["revision"]<1
                or type(expected["run_id"]) is not str or str(uuid.UUID(expected["run_id"]))!=expected["run_id"]
                or type(expected["nonce"]) is not str or not re.fullmatch("[0-9a-f]{32}",expected["nonce"])
                or any(type(expected[k]) is not int or not 0<=expected[k]<=2**63-1
                       for k in ("dispatch_not_before_ns","dispatch_not_after_ns","now_ns","registered_ns"))
                or not expected["dispatch_not_before_ns"]<=expected["dispatch_not_after_ns"]
                or not expected["dispatch_not_before_ns"]<=expected["registered_ns"]<=min(expected["now_ns"],expected["dispatch_not_after_ns"])
                or expected["dispatch_not_after_ns"]-expected["dispatch_not_before_ns"]>90_000_000_000):
            return dict(denied,reason="invalid_independent_context")
        ip=ipaddress.IPv4Address(expected["target_ip"])
        if (not (ip.is_private or ip.is_loopback) or ip.is_unspecified or ip.is_multicast or ip.is_reserved
                or type(expected["target_port"]) is not int or not 1<=expected["target_port"]<=65535):
            return dict(denied,reason="invalid_independent_target")
        if (type(payload) is not dict or set(payload)!={"root_id","revision","observation"}
                or type(payload["revision"]) is not int or payload["revision"]!=expected["revision"]
                or payload["root_id"]!=expected["root_id"]):
            return dict(denied,reason="root_context_mismatch")
        o=payload["observation"]
        if (type(o) is not dict or set(o)!=FIELDS
                or o["version"]!="host-forward-observation.v1" or o["profile"]!=profile
                or any(type(o[k]) is not str or not re.fullmatch("[0-9a-f]{64}",o[k]) for k in HASHES)
                or any(o[k] is not False for k in FLAGS)
                or type(o["exit_code"]) is not int or type(o["timed_out"]) is not bool
                or type(o["target_port"]) is not int):
            return dict(denied,reason="invalid_observation_schema")
        for key in ("run_id","nonce","target_ip","target_port"):
            if o[key]!=expected[key]:return dict(denied,reason="dispatch_context_mismatch")
        identity=o["identity"]
        if type(identity) is not dict or set(identity)!=docker.FIELDS|{"addresses"}:
            return dict(denied,reason="invalid_target_identity")
        if not docker._valid({k:identity[k] for k in docker.FIELDS}):
            return dict(denied,reason="invalid_target_identity")
        if (any(identity[k]!=expected[k] for k in ("container_id","image_id","started_at"))
                or o["peer_ip"]!=expected["target_ip"] or type(identity["addresses"]) is not str
                or len(identity["addresses"])>512 or expected["target_ip"] not in identity["addresses"].split()):
            return dict(denied,reason="target_identity_mismatch")
        if any(type(o[k]) is not int or not 0<=o[k]<=2**63-1 for k in TIMES):
            return dict(denied,reason="invalid_observation_window")
        first,start,end,last=[o[k] for k in ("observed_before_ns","request_start_ns","request_end_ns","observed_after_ns")]
        if (not expected["registered_ns"]<=first<=start<end<=last<=min(expected["dispatch_not_after_ns"],expected["now_ns"])
                or last-first>60_000_000_000 or expected["now_ns"]-last>300_000_000_000):
            return dict(denied,reason="observation_outside_dispatch_window")
        return dict(denied,context_matched=True,reason="independent_context_diagnostic_only")
    except (ValueError,TypeError,KeyError,OverflowError,AttributeError):
        return dict(denied,reason="invalid_independent_binding")


def inspect_binding(payload,expected):
    return _inspect_binding(payload,expected,"val1-plane-wave-v2")


def inspect_consistency_binding(payload,expected,case):
    if type(case) is not str or case not in {"base","grid","time","equivalent"}:
        raise ValueError("reviewed_consistency_case_required")
    return _inspect_binding(payload,expected,"consistency-sourcefree-v2."+case)
