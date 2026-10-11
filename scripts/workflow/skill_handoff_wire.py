"""Formatting guard AFTER the configured read_simulation_generation_context tool.

This function is not an authorizer and cannot authenticate a standalone string.
Graph bindings must use the gateway's freshly verified reader output; caller
JSON/search results must never bind this variable. It checks transport shape,
original input and fixed recipe bytes; no trust booleans are consumed.
"""
import json
import hashlib
import uuid
from scripts.validation.plane_wave_run_binding import RECIPE_SHA256
PREFIX="\n\nValidated fixed-profile reference (parameters must remain unchanged):\n"
LOCAL_PREFIX="\n\nValidated local fixed-profile reference (parameters must remain unchanged):\n"
def _unique(pairs):
    obj={}
    for k,v in pairs:
        if k in obj:raise ValueError("duplicate_key")
        obj[k]=v
    return obj
def main(original_context,server_context):
    result={"generation_context":original_context}
    try:
        if type(original_context) is not str:raise ValueError("original_context")
        if type(server_context) is not str or len(server_context.encode())>90000:return result
        if server_context==original_context:return result
        local=server_context.startswith(original_context+LOCAL_PREFIX)
        prefix=original_context+(LOCAL_PREFIX if local else PREFIX)
        if not server_context.startswith(prefix):return result
        reference=json.loads(server_context[len(prefix):],object_pairs_hook=_unique)
        fields={"skill_id","version","code","code_sha256","source_run_id"}
        if local:
            fields|={"scenario_type","scope","quality_level","status","parameters_sha256","actual_use_authenticated"}
        if type(reference) is not dict or set(reference)!=fields:return result
        local_profiles={"managed-local-plane-wave.v1":("initial_pressure_2d",RECIPE_SHA256),
            "managed-local-interface-wide.v1":("interface_2d","d28fb8bee0b0dfdd98a5833797b1e43cf80214aacf2cf3a1a3c0a553498a4df3")}
        expected=local_profiles.get(reference.get("scope")) if local else ("initial_pressure_2d",RECIPE_SHA256)
        if expected is None:return result
        if local and (reference["scenario_type"]!=expected[0]
                or reference["quality_level"]!="Q4" or reference["status"]!="active"
                or reference["actual_use_authenticated"] is not False
                or type(reference["parameters_sha256"]) is not str
                or len(reference["parameters_sha256"])!=64
                or any(c not in "0123456789abcdef" for c in reference["parameters_sha256"])):return result
        if (type(reference["version"]) is not int or reference["version"]<1
                or reference["code_sha256"]!=expected[1] or type(reference["code"]) is not str
                or hashlib.sha256(reference["code"].encode()).hexdigest()!=expected[1]
                or any(type(reference[k]) is not str or str(uuid.UUID(reference[k]))!=reference[k]
                       for k in ("skill_id","source_run_id"))):
            return result
        return {"generation_context":server_context}
    except (ValueError,TypeError,KeyError,RecursionError,UnicodeError):return result
