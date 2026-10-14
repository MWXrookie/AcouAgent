"""Fixed raw-array interface R/T diagnostic, not authenticated learning."""
import json
from scripts.validation.cylindrical_evidence import unique,number
PROFILE="val1-interface-v1"
CONFIGURATION=dict(domain_N=[384,256],domain_dx_m=[0.00025,0.00025],
    sound_speed_m_s=[1500.0,2500.0],density_kg_m3=[1000.0,2000.0],
    interface_x_index=192,pml_size=20,cfl=0.1,t_end_s=24e-6,
    initial_pressure_pa=1.0,initial_velocity="zero",source_x_index=128,sigma_grid=10,
    sensor_indices=[[160,128],[224,128]],smooth_initial=False,sample_origin="after_update")
def _validate(document, configuration, profile):
    denied=dict(profile=profile,numeric_pass=False,quality_level="Q0",trusted_run=False,
        runtime_approved=False,run_bound=False,parameters_bound=False,reason="invalid_raw_interface")
    try:
        if type(document) is not str or len(document.encode())>500000:raise ValueError("budget")
        value=json.loads(document,object_pairs_hook=unique,
            parse_constant=lambda _:(_ for _ in ()).throw(ValueError("nonfinite")))
        if type(value) is not dict or set(value)!={"raw_evidence"}:raise ValueError("raw_only")
        raw=value["raw_evidence"]
        if type(raw) is not dict or set(raw)!={"profile","configuration","units","dt_s","time","pressure_left","pressure_right"}:
            raise ValueError("raw_fields")
        if raw["profile"]!=profile or raw["units"]!={"time":"s","pressure":"Pa"}:
            raise ValueError("profile_units")
        if json.dumps(raw["configuration"],sort_keys=True)!=json.dumps(configuration,sort_keys=True):
            raise ValueError("fixed_configuration")
        cfg=raw["configuration"];dt=number(raw["dt_s"])
        if not 0<dt<=cfg["cfl"]*min(cfg["domain_dx_m"])/max(cfg["sound_speed_m_s"])*1.00001:
            raise ValueError("dt_constraint")
        times,left,right=(raw[k] for k in ("time","pressure_left","pressure_right"))
        if (any(type(v) is not list for v in (times,left,right)) or not 32<=len(times)<=10000
                or len(times)!=len(left) or len(times)!=len(right)):raise ValueError("trace_shape")
        times=[number(v) for v in times];left=[number(v) for v in left];right=[number(v) for v in right]
        if any(abs(t-(i+1)*dt)>max(dt*0.0001,abs(t)*2e-7) for i,t in enumerate(times)):raise ValueError("sample_axis")
        def peak(trace,start,end):
            if times[0]>start or times[-1]<end:raise ValueError("incomplete_window")
            signal=[abs(p) for t,p in zip(times,trace) if start<=t<=end]
            if not signal or max(signal)<=0:raise ValueError("missing_signal")
            return max(signal)
        incoming=peak(left,3e-6,9e-6);reflected=peak(left,14e-6,19e-6);transmitted=peak(right,11e-6,17e-6)
        z1,z2=[c*rho for c,rho in zip(cfg["sound_speed_m_s"],cfg["density_kg_m3"])]
        expected_r=(z2-z1)/(z2+z1);expected_t=2*z2/(z1+z2)
        error_r=abs((reflected/incoming)/expected_r-1)*100
        error_t=abs((transmitted/incoming)/expected_t-1)*100
        passed=error_r<1 and error_t<1
        return dict(denied,numeric_pass=passed,quality_level="Q2" if passed else "Q1",
            error_pct=dict(reflection=error_r,transmission=error_t),complete_windows=True,
            reason="fixed_numeric_diagnostic_only")
    except (ValueError,TypeError,KeyError,OverflowError,RecursionError,UnicodeError):
        return denied


def validate(document):
    """Public fixed-profile validator; diagnostic variants do not expand this contract."""
    return _validate(document, CONFIGURATION, PROFILE)
