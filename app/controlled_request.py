"""Internal fixed-profile request constructor; no public endpoint or trust grant."""
import hashlib
import json

from app import execution
from scripts.validation import plane_wave_run_binding as binding
from scripts.validation.plane_wave_code_configuration import derive_configuration, verify_configuration
from scripts.validation import plane_wave_evidence

REQUEST = {"version": 1, "profile": "val1-plane-wave-v2"}

def build_request(request_json):
    if type(request_json) is not str or len(request_json.encode("utf-8")) > 512:
        raise ValueError("invalid_controlled_request")
    def pairs(items):
        out = {}
        for key, value in items:
            if key in out:
                raise ValueError("duplicate_controlled_request_key")
            out[key] = value
        return out
    try:
        request = json.loads(request_json, object_pairs_hook=pairs,
                             parse_constant=lambda _: (_ for _ in ()).throw(ValueError("invalid_constant")))
    except (ValueError, TypeError, RecursionError) as exc:
        raise ValueError("invalid_controlled_request") from exc
    if (type(request) is not dict or set(request) != set(REQUEST)
            or type(request["version"]) is not int or request["version"] != 1
            or request["profile"] != REQUEST["profile"]):
        raise ValueError("unsupported_controlled_request")
    code = (binding.DIRECTORY / "recipes/val1_plane_wave_v2.py").read_text().strip()
    validator = (binding.DIRECTORY / "plane_wave_evidence.py").read_bytes()
    if (binding.digest(code) != binding.RECIPE_SHA256
            or hashlib.sha256(validator).hexdigest() != binding.VALIDATOR_SHA256
            or execution._clean_code(code) != code):
        raise ValueError("unreviewed_controlled_recipe_or_validator")
    if not verify_configuration(code, plane_wave_evidence.EXPECTED_CONFIGURATION)["static_configuration_matched"]:
        raise ValueError("controlled_recipe_configuration_mismatch")
    configuration = derive_configuration(code)
    canonical = json.dumps(configuration, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return {"code": code, "timeout_seconds": 30, "configuration": configuration,
            "configuration_sha256": hashlib.sha256(canonical.encode()).hexdigest(),
            "code_sha256": binding.RECIPE_SHA256, "parameters_bound": False,
            "trusted_run": False}
