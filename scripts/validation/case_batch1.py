"""Closed first-batch numerical diagnostics; no production registration or grant."""
import copy
import hashlib
import json
from pathlib import Path
from scripts.validation import cylindrical_evidence as radial
from scripts.validation import plane_wave_diagnostic as plane
from scripts.validation import interface_evidence as interface
from scripts.validation import attenuation_evidence as attenuation

CASE_IDS = ("plane-width10", "cylinder-speed1540", "sphere-speed1540",
            "interface-density1800", "attenuation-coeff08")

def specification(case):
    if case not in CASE_IDS:
        raise ValueError("closed_case_required")
    if case.startswith("plane"):
        mod, base, filename = plane, plane.CONFIGURATION, "val1_plane_wave_v3.py"
        replacements = [('(2*12**2)', '(2*10**2)'), ('sigma_grid=12', 'sigma_grid=10')]
        cfg = dict(copy.deepcopy(base), sigma_grid=10)
    elif case.startswith("cylinder"):
        mod, base, filename = radial, radial.CONFIGURATION, "val1_cylindrical_v1.py"
        replacements = [('speed=1500.0', 'speed=1540.0')]
        cfg = dict(copy.deepcopy(base), sound_speed_m_s=1540.0)
    elif case.startswith("sphere"):
        mod, base, filename = radial, radial.SPHERICAL_CONFIGURATION, "val1_spherical_v1.py"
        replacements = [('speed=1500.0', 'speed=1540.0')]
        cfg = dict(copy.deepcopy(base), sound_speed_m_s=1540.0)
    elif case.startswith("interface"):
        mod, base, filename = interface, interface.CONFIGURATION, "val1_interface_v1.py"
        replacements = [('jnp.where(xs<192,1000.0,2000.0)', 'jnp.where(xs<192,1000.0,1800.0)'),
                        ('density_kg_m3=[1000.0,2000.0]', 'density_kg_m3=[1000.0,1800.0]')]
        cfg = dict(copy.deepcopy(base), density_kg_m3=[1000.0, 1800.0])
    else:
        mod, base, filename = attenuation, attenuation.CONFIGURATION, "val1_attenuation_v1.py"
        replacements = [('attenuation=1.0', 'attenuation=0.8'),
                        ('attenuation_db_mhz2_cm=1.0', 'attenuation_db_mhz2_cm=0.8')]
        cfg = dict(copy.deepcopy(base), attenuation_db_mhz2_cm=0.8)
    code = (Path(__file__).parent / "recipes" / filename).read_text().strip()
    base_profile = radial.SPHERICAL_PROFILE if case.startswith("sphere") else (
        radial.PROFILE if case.startswith("cylinder") else mod.PROFILE)
    replacements.append((base_profile, "case-batch1-" + case))
    for old, new in replacements:
        if code.count(old) != 1:
            raise ValueError("recipe_source_changed")
        code = code.replace(old, new)
    return cfg, code

def assess(case, record):
    cfg, code = specification(case)
    denied = dict(numeric_pass=False, quality_level="Q0", trusted_run=False,
                  runtime_approved=False, run_bound=False, parameters_bound=False,
                  actual_use_authenticated=False, reason="run_record_rejected")
    if (type(record) is not dict or
        record.get("code_sha256") != hashlib.sha256(code.encode()).hexdigest() or
        type(record.get("exit_code")) is not int or record["exit_code"] != 0 or
        record.get("timed_out") is not False or record.get("stderr") != ""):
        return denied
    raw = record.get("stdout")
    profile = "case-batch1-" + case
    if case.startswith("cylinder") or case.startswith("sphere"):
        result = radial._validate(raw, cfg, profile, 1.0 if case.startswith("sphere") else 0.5)
    else:
        module = plane if case.startswith("plane") else interface if case.startswith("interface") else attenuation
        result = module._validate(raw, cfg, profile)
    return dict(result, actual_use_authenticated=False,
                validator_version="case-batch1.v1",
                code_sha256=record["code_sha256"],
                stdout_sha256=hashlib.sha256(raw.encode()).hexdigest() if type(raw) is str else None)

def main():
    import argparse
    import sys
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", required=True, choices=CASE_IDS)
    args = parser.parse_args()
    _, code = specification(args.case)
    sys.path.insert(0, "/app")
    import executor
    output = executor.execute(dict(code=code, timeout_seconds=30))
    record = dict(code_sha256=hashlib.sha256(code.encode()).hexdigest(),
                  exit_code=output.get("exit_code"), timed_out=output.get("timed_out"),
                  stdout=output.get("stdout", ""), stderr=output.get("stderr", ""),
                  duration_ms=output.get("duration_ms"))
    print(json.dumps(dict(case=args.case, record=record)), flush=True)
    result = assess(args.case, record)
    print(json.dumps(dict(summary=result)), flush=True)
    return 0 if result["numeric_pass"] else 1

if __name__ == "__main__":
    raise SystemExit(main())
