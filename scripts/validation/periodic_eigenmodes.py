"""Closed periodic acoustic eigenmode benchmarks; raw SI values, independent analytic gate."""
import math
import json

MEDIA=(("water",1500.0,1000.0,"水"),("soft_tissue",1540.0,1050.0,"软组织参数"),("air",343.0,1.225,"空气"))
TASKS=(
 ("axial",[(1,0,1.0)],"轴向驻波"),
 ("oblique",[(1,1,1.0)],"45度斜向驻波"),
 ("crossed",[(1,0,0.5),(0,1,0.5)],"正交双波叠加"),
 ("harmonic",[(1,0,0.7),(3,0,0.3)],"基频与三次谐波叠加"),
 ("beating",[(2,1,0.5),(3,1,0.5)],"双斜向模态拍频"),
)
SCOPES=tuple("managed-local-periodic-"+medium+"-"+task+".v1" for medium,_,_,_ in MEDIA for task,_,_ in TASKS)
def configuration(scope):
    if scope not in SCOPES:raise ValueError("unreviewed_periodic_profile")
    for medium,c,rho,label in MEDIA:
        for task,modes,title in TASKS:
            if scope=="managed-local-periodic-"+medium+"-"+task+".v1":
                return dict(domain_N=[64,64],domain_dx_m=[0.0005,0.0005],sound_speed_m_s=c,
                    density_kg_m3=rho,pml_size=0,cfl=0.05,t_end_s=0.032/c,
                    initial_pressure_pa=1.0,initial_velocity="zero",smooth_initial=False,
                    boundary_condition="periodic",mode_components=modes,
                    sensor_indices=[[7,11],[19,27],[31,43]],snapshot_indices=[0,320,639])
    raise ValueError("unreviewed_periodic_profile")
def title(scope):
    for medium,_,_,label in MEDIA:
        for task,_,name in TASKS:
            if scope=="managed-local-periodic-"+medium+"-"+task+".v1":return label+"："+name
    raise ValueError("unreviewed_periodic_profile")
def pressure(cfg,x,y,t):
    length=cfg["domain_N"][0]*cfg["domain_dx_m"][0]
    return sum(a*math.cos(2*math.pi*(mx*x+my*y)/cfg["domain_N"][0])*
        math.cos(2*math.pi*cfg["sound_speed_m_s"]*math.hypot(mx,my)*t/length)
        for mx,my,a in cfg["mode_components"])
def _number(v):
    if type(v) not in (int,float) or not math.isfinite(v):raise ValueError("invalid_numeric_sample")
    return v
def validate(result):
    out=dict(numeric_pass=False,trusted_run=False,parameters_bound=False,
        runtime_approved=False,run_bound=False,validator_version="periodic-eigenmode.v1",tolerance_relative=0.01)
    try:
        if type(result) is not dict or set(result)!={"raw_evidence"}:raise ValueError("raw_fields")
        raw=result["raw_evidence"]
        if type(raw) is not dict or set(raw)!={"profile","configuration","units","time","traces","snapshots"}:raise ValueError("raw_fields")
        cfg=configuration(raw["profile"])
        if json.dumps(raw["configuration"],sort_keys=True,allow_nan=False)!=json.dumps(cfg,sort_keys=True,allow_nan=False):raise ValueError("configuration_changed")
        if raw["units"]!={"time":"s","pressure":"Pa"}:raise ValueError("units")
        ts=raw["time"];traces=raw["traces"];snapshots=raw["snapshots"]
        # Fixed TimeAxis: dt=cfl*dx/c; entire one fundamental period, no skipped traces.
        dt=cfg["cfl"]*cfg["domain_dx_m"][0]/cfg["sound_speed_m_s"]
        count=math.ceil(cfg["t_end_s"]/dt)
        if type(ts) is not list or len(ts)!=count or len(ts)>1400:raise ValueError("time_count")
        if any(abs(_number(t)-(i+1)*dt)>dt*0.001 for i,t in enumerate(ts)):raise ValueError("time_axis")
        if type(traces) is not list or len(traces)!=3:raise ValueError("probe_count")
        errors=[]
        for xy,values in zip(cfg["sensor_indices"],traces):
            if type(values) is not list or len(values)!=count:raise ValueError("trace_length")
            refs=[pressure(cfg,*xy,t) for t in ts]
            diffs=[_number(v)-r for v,r in zip(values,refs)]
            errors.extend([max(abs(d) for d in diffs),math.sqrt(sum(d*d for d in diffs)/sum(r*r for r in refs))])
        if type(snapshots) is not list or len(snapshots)!=3:raise ValueError("snapshot_count")
        for index,values in zip(cfg["snapshot_indices"],snapshots):
            if type(values) is not list or len(values)!=4096:raise ValueError("complete_field_required")
            refs=[pressure(cfg,x,y,ts[index]) for x in range(64) for y in range(64)]
            diffs=[_number(v)-r for v,r in zip(values,refs)]
            errors.extend([max(abs(d) for d in diffs),math.sqrt(sum(d*d for d in diffs)/(4096*sum(a*a for _,_,a in cfg["mode_components"])/2))])
        out.update(numeric_pass=all(e<0.01 for e in errors),relative_errors=errors,
            maximum_relative_error=max(errors),complete_snapshot_fields=True,
            reason="numeric_checks_passed" if all(e<0.01 for e in errors) else "physics_mismatch")
    except (ValueError,TypeError,KeyError,IndexError,OverflowError,ZeroDivisionError) as exc:out["reason"]=str(exc)
    return out
def recipe(scope):
    cfg=configuration(scope)
    return """import json
import jax.numpy as jnp
import jwave as jw
from jaxdf import FourierSeries
from jwave.acoustics.time_varying import TimeWavePropagationSettings
CFG = """+repr(cfg)+"""
domain=jw.Domain(tuple(CFG["domain_N"]),tuple(CFG["domain_dx_m"]))
medium=jw.Medium(domain,sound_speed=CFG["sound_speed_m_s"],density=CFG["density_kg_m3"],pml_size=0)
axis=jw.TimeAxis.from_medium(medium,cfl=CFG["cfl"],t_end=CFG["t_end_s"])
x=jnp.arange(64,dtype=jnp.float32)[:,None]
y=jnp.arange(64,dtype=jnp.float32)[None,:]
initial=sum(a*jnp.cos(2*jnp.pi*(mx*x+my*y)/64) for mx,my,a in CFG["mode_components"])
field=jw.simulate_wave_propagation(medium,axis,p0=FourierSeries(initial,domain),
    settings=TimeWavePropagationSettings(smooth_initial=False)).params
times=axis.to_array()+axis.dt
raw=dict(profile="""+repr(scope)+""",configuration=CFG,units={"time":"s","pressure":"Pa"},
 time=[float(t) for t in times],
 traces=[[float(v) for v in field[:,i,j,0]] for i,j in CFG["sensor_indices"]],
 snapshots=[[float(v) for v in field[k,:,:,0].reshape(-1)] for k in CFG["snapshot_indices"]])
print(json.dumps({"raw_evidence":raw},allow_nan=False))
"""
