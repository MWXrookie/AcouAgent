"""After-update plane-wave raw diagnostic; does not extend approved v2 root."""
import json
import math
from scripts.validation.cylindrical_evidence import unique,number
from scripts.validation.plane_wave_evidence import EXPECTED_CONFIGURATION
PROFILE="val1-plane-wave-v3"
CONFIGURATION=dict(EXPECTED_CONFIGURATION,sample_origin="after_update")
def _validate(document, configuration, profile):
    denied=dict(profile=profile,numeric_pass=False,quality_level="Q0",trusted_run=False,
        runtime_approved=False,run_bound=False,parameters_bound=False,reason="invalid_raw_plane_wave")
    try:
        if type(document) is not str or len(document.encode())>500000:raise ValueError("budget")
        value=json.loads(document,object_pairs_hook=unique,
            parse_constant=lambda _:(_ for _ in ()).throw(ValueError("nonfinite")))
        if type(value) is not dict or set(value)!={"raw_evidence"}:raise ValueError("raw_only")
        raw=value["raw_evidence"]
        if type(raw) is not dict or set(raw)!={"profile","configuration","units","dt_s","time","pressure_x1","pressure_x2"}:
            raise ValueError("fields")
        if raw["profile"]!=profile or raw["units"]!={"time":"s","pressure":"Pa"}:raise ValueError("units")
        if json.dumps(raw["configuration"],sort_keys=True)!=json.dumps(configuration,sort_keys=True):
            raise ValueError("config")
        dt=number(raw["dt_s"])
        cfg=raw["configuration"]
        if not 0<dt<=cfg["cfl"]*min(cfg["domain_dx_m"])/cfg["sound_speed_m_s"]*1.00001:raise ValueError("dt")
        times=raw["time"];traces=[raw["pressure_x1"],raw["pressure_x2"]]
        if type(times) is not list or not 100<=len(times)<=4096 or any(type(v) is not list or len(v)!=len(times) for v in traces):
            raise ValueError("shape")
        times=[number(t) for t in times];traces=[[number(p) for p in v] for v in traces]
        if any(abs(t-(i+1)*dt)>max(dt*0.0001,abs(t)*2e-7) for i,t in enumerate(times)):
            raise ValueError("axis")
        peaks=[];errors=[]
        sigma=cfg["sigma_grid"]*cfg["domain_dx_m"][0]/cfg["sound_speed_m_s"]
        for sensor,trace in zip(cfg["sensor_indices"],traces):
            offset=sensor[0]-cfg["source_x_index"]
            arrival=offset*cfg["domain_dx_m"][0]/cfg["sound_speed_m_s"]
            if times[0]>arrival-3*sigma or times[-1]<arrival+3*sigma:raise ValueError("window")
            indices=[i for i,t in enumerate(times) if abs(t-arrival)<=3*sigma]
            peak=max(indices,key=lambda i:trace[i]);peaks.append(trace[peak])
            if peaks[-1]<=0:raise ValueError("signal")
            # Arrival is checked separately from the strict amplitude/waveform 1% rule.
            if abs(times[peak]-arrival)>2*dt:raise ValueError("arrival")
            errors.append(max(abs(trace[i]-0.5*(math.exp(-0.5*((times[i]-arrival)/sigma)**2)
                +math.exp(-0.5*((times[i]+arrival)/sigma)**2)))/0.5*100 for i in indices))
            errors.append(abs(peaks[-1]/0.5-1)*100)
        errors.append(abs(peaks[1]/peaks[0]-1)*100)
        passed=all(e<1 for e in errors)
        return dict(denied,numeric_pass=passed,quality_level="Q2" if passed else "Q1",
            error_pct=errors,reason="fixed_numeric_diagnostic_only",complete_windows=True)
    except (ValueError,TypeError,KeyError,IndexError,OverflowError,RecursionError,UnicodeError):
        return denied


def validate(document):
    """Public fixed-profile validator; diagnostic variants do not expand this contract."""
    return _validate(document, CONFIGURATION, PROFILE)
