"""Raw-array fixed cylindrical diagnostic; no source credential or skill grant."""
import json
import math

PROFILE="val1-cylindrical-v1"
CONFIGURATION=dict(domain_N=[256,256],domain_dx_m=[0.0005,0.0005],
    sound_speed_m_s=1500.0,density_kg_m3=1000.0,pml_size=20,cfl=0.1,t_end_s=60e-6,
    source_frequency_hz=200000.0,burst_cycles=3,source_index=[128,128],
    sensor_indices=[[192,128],[224,128]],sample_origin="after_update")

def unique(pairs):
    obj={}
    for key,value in pairs:
        if key in obj:raise ValueError("duplicate_key")
        obj[key]=value
    return obj

def number(value):
    if type(value) not in (int,float):raise ValueError("numeric_type")
    result=float(value)
    if not math.isfinite(result):raise ValueError("finite_required")
    return result

def _validate(document,configuration,profile,power):
    denied=dict(profile=profile,numeric_pass=False,quality_level="Q0",trusted_run=False,
        runtime_approved=False,run_bound=False,parameters_bound=False,reason="invalid_raw_cylindrical")
    try:
        if type(document) is not str or len(document.encode())>500000:raise ValueError("budget")
        value=json.loads(document,object_pairs_hook=unique,
            parse_constant=lambda _:(_ for _ in ()).throw(ValueError("nonfinite")))
        if type(value) is not dict or set(value)!={"raw_evidence"}:raise ValueError("raw_only")
        raw=value["raw_evidence"]
        if type(raw) is not dict or set(raw)!={"profile","configuration","units","dt_s","time","pressure_r1","pressure_r2"}:
            raise ValueError("raw_fields")
        if raw["profile"]!=profile or raw["units"]!={"time":"s","pressure":"Pa"}:
            raise ValueError("profile_units")
        # Canonical JSON distinguishes booleans masquerading as numeric config.
        if json.dumps(raw["configuration"],sort_keys=True)!=json.dumps(configuration,sort_keys=True):
            raise ValueError("fixed_configuration")
        config=raw["configuration"];source=config["source_index"];sensors=config["sensor_indices"]
        if any(not all(config["pml_size"]<=p<n-config["pml_size"] for p,n in zip(position,config["domain_N"]))
               for position in [source]+sensors):raise ValueError("pml_geometry")
        dt=number(raw["dt_s"])
        if not 0<dt<=config["cfl"]*min(config["domain_dx_m"])/config["sound_speed_m_s"]*1.00001:
            raise ValueError("dt_constraint")
        times,first,second=(raw[k] for k in ("time","pressure_r1","pressure_r2"))
        if (any(type(v) is not list for v in (times,first,second)) or not 32<=len(times)<=10000
                or len(first)!=len(times) or len(second)!=len(times)):raise ValueError("trace_shape")
        times=[number(v) for v in times]
        traces=[[number(v) for v in trace] for trace in (first,second)]
        if any(abs(t-(i+1)*dt)>max(dt*0.0001,abs(t)*2e-7) for i,t in enumerate(times)):
            raise ValueError("sample_axis")
        radii=[math.sqrt(sum(((a-b)*dx)**2 for a,b,dx in zip(sensor,source,config["domain_dx_m"]))) for sensor in sensors]
        burst=config["burst_cycles"]/config["source_frequency_hz"]
        peaks=[]
        for radius,trace in zip(radii,traces):
            arrival=radius/config["sound_speed_m_s"]
            start,end=arrival-burst/2,arrival+burst
            if times[0]>start or times[-1]<end:raise ValueError("incomplete_arrival_window")
            window=[abs(p) for t,p in zip(times,trace) if start<=t<=end]
            if not window or max(window)<=0:raise ValueError("missing_signal")
            peaks.append(max(window))
        theory=(radii[0]/radii[1])**power
        measured=peaks[1]/peaks[0];error=abs(measured/theory-1)*100
        return dict(denied,numeric_pass=error<1.0,quality_level="Q2" if error<1.0 else "Q1",reason="fixed_numeric_diagnostic_only",
                    error_pct=error,measured_ratio=measured,theory_ratio=theory,
                    pml_geometry_passed=True,complete_windows=True)
    except (ValueError,TypeError,KeyError,OverflowError,RecursionError,UnicodeError):
        return denied


SPHERICAL_PROFILE="val1-spherical-v1"
SPHERICAL_CONFIGURATION=dict(domain_N=[72,72,72],domain_dx_m=[0.0005,0.0005,0.0005],
    sound_speed_m_s=1500.0,density_kg_m3=1000.0,pml_size=8,cfl=0.1,t_end_s=25e-6,
    source_frequency_hz=300000.0,burst_cycles=3,source_index=[36,36,36],
    sensor_indices=[[52,36,36],[60,36,36]],sample_origin="after_update")

def validate(document):
    return _validate(document,CONFIGURATION,PROFILE,0.5)

def validate_spherical(document):
    return _validate(document,SPHERICAL_CONFIGURATION,SPHERICAL_PROFILE,1.0)
