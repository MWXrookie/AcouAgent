"""Fixed raw complex-pressure absorption diagnostic; no authenticated grant."""
import json
import math
from scripts.validation.cylindrical_evidence import unique,number
PROFILE="val1-attenuation-v1"
CONFIGURATION=dict(domain_N=[160,160],domain_dx_m=[0.0005,0.0005],
    sound_speed_m_s=1500.0,density_kg_m3=1000.0,pml_size=20,
    source_frequency_hz=1000000.0,attenuation_db_mhz2_cm=1.0,attenuation_power=2.0,
    solver_mode="frequency_domain",solver_method="gmres",source_index=[80,80],
    sensor_indices=[[104,80],[128,80]])

def theory_ratio(cfg):
    # dB/(MHz^2 cm) -> amplitude Np/m at the specified frequency.
    absorption=cfg["attenuation_db_mhz2_cm"]*100*math.log(10)/20*(cfg["source_frequency_hz"]/1e6)**2
    source=cfg["source_index"]
    radii=[math.sqrt(sum(((a-b)*dx)**2 for a,b,dx in zip(p,source,cfg["domain_dx_m"])))
        for p in cfg["sensor_indices"]]
    return math.sqrt(radii[0]/radii[1])*math.exp(-absorption*(radii[1]-radii[0]))

def _validate(document, configuration, profile):
    denied=dict(profile=profile,numeric_pass=False,quality_level="Q0",trusted_run=False,
        runtime_approved=False,run_bound=False,parameters_bound=False,
        reason="invalid_raw_attenuation",solver_convergence_authenticated=False)
    try:
        if type(document) is not str or len(document.encode())>16000:raise ValueError("budget")
        value=json.loads(document,object_pairs_hook=unique,
            parse_constant=lambda _:(_ for _ in ()).throw(ValueError("nonfinite")))
        if type(value) is not dict or set(value)!={"raw_evidence"}:raise ValueError("raw_only")
        raw=value["raw_evidence"]
        if type(raw) is not dict or set(raw)!={"profile","configuration","units","pressure_complex"}:
            raise ValueError("raw_fields")
        if raw["profile"]!=profile or raw["units"]!={"pressure":"Pa","attenuation":"dB/(MHz^2 cm)"}:
            raise ValueError("profile_units")
        if json.dumps(raw["configuration"],sort_keys=True)!=json.dumps(configuration,sort_keys=True):
            raise ValueError("fixed_configuration")
        cfg=raw["configuration"]
        for p in [cfg["source_index"]]+cfg["sensor_indices"]:
            if not all(cfg["pml_size"]<=v<n-cfg["pml_size"] for v,n in zip(p,cfg["domain_N"])):
                raise ValueError("pml_geometry")
        pairs=raw["pressure_complex"]
        if type(pairs) is not list or len(pairs)!=2 or any(type(p) is not list or len(p)!=2 for p in pairs):
            raise ValueError("complex_shape")
        amps=[math.hypot(number(p[0]),number(p[1])) for p in pairs]
        if any(a<=0 or not math.isfinite(a) for a in amps):raise ValueError("missing_signal")
        expected=theory_ratio(cfg);ratio=amps[1]/amps[0];error=abs(ratio/expected-1)*100
        return dict(denied,numeric_pass=error<1,quality_level="Q2" if error<1 else "Q1",
            measured_ratio=ratio,theory_ratio=expected,error_pct=error,pml_geometry_passed=True,
            reason="fixed_numeric_diagnostic_only")
    except (ValueError,TypeError,KeyError,OverflowError,RecursionError,UnicodeError):
        return denied


def validate(document):
    """Public fixed-profile validator; diagnostic variants do not expand this contract."""
    return _validate(document, CONFIGURATION, PROFILE)
