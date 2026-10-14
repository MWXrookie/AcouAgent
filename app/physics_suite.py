"""Five fixed raw diagnostics, no run authentication or automatic admission."""
import hashlib
from scripts.validation.plane_wave_diagnostic import validate as plane
from scripts.validation.cylindrical_evidence import validate as cylinder,validate_spherical as sphere
from scripts.validation.interface_evidence import validate as interface
from scripts.validation.attenuation_evidence import validate as attenuation
CASES={"plane_wave":plane,"cylindrical":cylinder,"interface":interface,"spherical":sphere,"attenuation":attenuation}
def assess(documents):
    closed=dict(numeric_pass=False,quality_level="Q0",trusted_run=False,runtime_approved=False,
        run_bound=False,parameters_bound=False,actual_use_authenticated=False,
        reason="incomplete_or_invalid_physics_suite",cases={})
    if type(documents) is not dict or set(documents)!=set(CASES):return closed
    if any(type(v) is not str for v in documents.values()):return closed
    try:
        if sum(len(v.encode()) for v in documents.values())>2000000:return closed
        results={name:dict(check(documents[name]),raw_sha256=hashlib.sha256(documents[name].encode()).hexdigest())
            for name,check in CASES.items()}
        passed=all(v["numeric_pass"] is True for v in results.values())
        return dict(closed,numeric_pass=passed,quality_level="Q2" if passed else "Q1",
            cases=results,reason="five_fixed_diagnostics_only_not_authenticated")
    except (ValueError,TypeError,OverflowError,RecursionError,UnicodeError):
        return closed
