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
        local_profiles.update({'managed-local-periodic-water-axial.v1': ('initial_pressure_2d', '1127686cc15179eb22637dc419d831abb83984fb0f607be1493d43f9e6f8d6e0'), 'managed-local-periodic-water-oblique.v1': ('initial_pressure_2d', 'd8cc1f074744599c428571909414dfa3ca7a6ae436cf42187f65c7b22b9402f1'), 'managed-local-periodic-water-crossed.v1': ('initial_pressure_2d', '29a993deeb3eee35a6ec20f9a6d7ecffad374b3209f540368d5235e14b004ca5'), 'managed-local-periodic-water-harmonic.v1': ('initial_pressure_2d', '8e068c63fbf5e5a69c2834ea961a1fdfa2b994537ab6361b0f9d51b51923dac6'), 'managed-local-periodic-water-beating.v1': ('initial_pressure_2d', '5e25f010233d59368dacfdad19860a31a3531d7473851fc594a184c46b399d48'), 'managed-local-periodic-soft_tissue-axial.v1': ('initial_pressure_2d', '8f6e54b2efabcc5fe156bf4dcf9e8c1befd0e7d5bd2c3fca2a38c441f0bca824'), 'managed-local-periodic-soft_tissue-oblique.v1': ('initial_pressure_2d', '5f9fcd2a6ad9d332a0cf9011d84f1a48e9091c54c1fd1b3ddaa07992e52c31a6'), 'managed-local-periodic-soft_tissue-crossed.v1': ('initial_pressure_2d', '27e6dc1800ffd1f5e67994c4b629781671e217b2d7f223be8aa942d0900242b9'), 'managed-local-periodic-soft_tissue-harmonic.v1': ('initial_pressure_2d', '76d117863eb14881df55325157359f31884f15cbc26f2e1912c144c88755560a'), 'managed-local-periodic-soft_tissue-beating.v1': ('initial_pressure_2d', '37ad1b8e38a26330f6554a2e7fd9b2d69721d153992f9ac95a9e8d845efcedc1'), 'managed-local-periodic-air-axial.v1': ('initial_pressure_2d', 'f6ad4216b55f8ef985a7529b573ed8df60a34be7d306594268a98b34c23517c6'), 'managed-local-periodic-air-oblique.v1': ('initial_pressure_2d', 'f89a630216af4a89c84042f0c198a9193184b17f1cb407b2abc2399d2df378ef'), 'managed-local-periodic-air-crossed.v1': ('initial_pressure_2d', '5bd6b556745bb6fd44d0ffdfcdba5c183d7acbdb2f7018d62db4eb71fbdcf128'), 'managed-local-periodic-air-harmonic.v1': ('initial_pressure_2d', '94dc79d46bddbb9a5d1e87beb89b8d26052b0f8c4b589a37df2f34f09a6401bc'), 'managed-local-periodic-air-beating.v1': ('initial_pressure_2d', '0a922df1099ec77f9bad3598a0e72c94ea067fb8d8cf7ccf776da955fcf6c553')})
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
