"""Repeatable isolated executor diagnostic; zero LLM/production calls or learning."""
import hashlib
import json
import sys
from pathlib import Path
def main(profile='cylindrical'):
    sys.path.insert(0,"/app");sys.path.insert(0,"/workspace")
    import executor
    from scripts.validation.cylindrical_evidence import validate,validate_spherical
    from scripts.validation.interface_evidence import validate as validate_interface
    from scripts.validation.attenuation_evidence import validate as validate_attenuation
    from scripts.validation.plane_wave_diagnostic import validate as validate_plane
    if profile not in {"cylindrical","spherical","interface","attenuation","plane_wave"}:raise ValueError("controlled_profile")
    recipe="val1_plane_wave_v3.py" if profile=="plane_wave" else "val1_"+profile+"_v1.py"
    code=Path("/workspace/scripts/validation/recipes/"+recipe).read_text().strip()
    result=executor.execute(dict(code=code,timeout_seconds=30))
    print(json.dumps(dict(record=dict(code_sha256=hashlib.sha256(code.encode()).hexdigest(),
        exit_code=result.get("exit_code"),timed_out=result.get("timed_out"),duration_ms=result.get("duration_ms"),
        stdout=result.get("stdout",""),stderr=result.get("stderr","")))),flush=True)
    checked={"cylindrical":validate,"spherical":validate_spherical,"interface":validate_interface,"attenuation":validate_attenuation,"plane_wave":validate_plane}[profile](result.get("stdout",""))
    print(json.dumps(dict(summary=checked)),flush=True)
    return 0 if result.get("exit_code")==0 and result.get("timed_out") is False and checked["numeric_pass"] else 1
if __name__=="__main__":
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile",choices=["cylindrical","spherical","interface","attenuation","plane_wave"],default="cylindrical")
    raise SystemExit(main(parser.parse_args().profile))
