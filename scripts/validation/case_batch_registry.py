"""Closed ten-case registry for provenance diagnostics, never a trust grant."""
import hashlib
from pathlib import Path
from scripts.validation import case_batch1 as variants, plane_wave_diagnostic as plane
from scripts.validation import cylindrical_evidence as radial, interface_evidence as interface
from scripts.validation import attenuation_evidence as attenuation
from scripts.validation.host_forward_signature import canonical

BASES = {
 "base-plane_wave": (plane, "val1_plane_wave_v3.py"),
 "base-cylindrical": (radial, "val1_cylindrical_v1.py"),
 "base-spherical": (radial, "val1_spherical_v1.py"),
 "base-interface": (interface, "val1_interface_v1.py"),
 "base-attenuation": (attenuation, "val1_attenuation_v1.py"),
}
CASES = tuple(BASES) + variants.CASE_IDS

def specification(case):
    if type(case) is not str or case not in CASES:
        raise ValueError("closed_case_required")
    if case not in BASES:
        return variants.specification(case)
    module, filename = BASES[case]
    configuration = radial.SPHERICAL_CONFIGURATION if case == "base-spherical" else module.CONFIGURATION
    import copy
    return copy.deepcopy(configuration), (Path(__file__).parent/"recipes"/filename).read_text().strip()

def pins(case):
    cfg, code = specification(case)
    return dict(code_sha256=hashlib.sha256(code.encode()).hexdigest(),
                configuration_sha256=hashlib.sha256(canonical(cfg)).hexdigest())

def assess(case, record):
    if case not in BASES:
        return variants.assess(case, record)
    denied=dict(numeric_pass=False,quality_level="Q0",trusted_run=False,
        runtime_approved=False,run_bound=False,parameters_bound=False,actual_use_authenticated=False)
    if (type(record) is not dict or record.get("code_sha256")!=pins(case)["code_sha256"]
            or type(record.get("exit_code")) is not int or record["exit_code"]!=0
            or record.get("timed_out") is not False or record.get("stderr")!=""):
        return denied
    module,_ = BASES[case]
    result=radial.validate_spherical(record.get("stdout")) if case=="base-spherical" else module.validate(record.get("stdout"))
    return dict(result,actual_use_authenticated=False)
