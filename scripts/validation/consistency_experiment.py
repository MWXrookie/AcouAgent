"""Small isolated jwave refinement benchmark; no production or learning.

Run only in the preserved executor image under the documented resource limits.
Four executions use executor.execute (each <=30s). This is a source-free,
uniform, periodic Gaussian initial pressure experiment, not a VAL-1 replacement.
"""
import hashlib
import json
import sys

def recipe(size=64,cfl=0.1,equivalent_units=False):
    speed="1.5 * 1000.0" if equivalent_units else "1500.0"
    density="1.0 * 1000.0" if equivalent_units else "1000.0"
    return f"""
import json
import jax.numpy as jnp
import jwave as jw
from jaxdf import FourierSeries
from jwave.acoustics.time_varying import TimeWavePropagationSettings
size={size}
dx=0.032/size
speed={speed}
density={density}
domain=jw.Domain((size,size),(dx,dx))
medium=jw.Medium(domain,sound_speed=speed,density=density,pml_size=0)
axis=jw.TimeAxis.from_medium(medium,cfl={cfl},t_end=2.2e-6)
x=jnp.arange(size)*dx
profile=jnp.exp(-0.5*((x-0.012)/0.002)**2)
p0=FourierSeries(jnp.broadcast_to(profile[:,None],(size,size)),domain)
settings=TimeWavePropagationSettings(smooth_initial=False)
p_record,u_record=jw.simulate_wave_propagation(medium,axis,p0=p0,settings=settings,
    sensors=lambda p,u,rho:((p,u),None))
pressure=p_record.params
# jwave 0.2.1 scan emits AFTER updating p, so sample k is at (k+1)*dt.
# Velocity is at the half step: average adjacent samples for the p time.
index=int(round(2e-6/float(axis.dt)))-1
if not 0<=index<pressure.shape[0]-1:
    raise ValueError("requested physical sample unavailable")
energy_data={{}}
if size==64 and {cfl}==0.1 and {equivalent_units!r} is False:
    velocity=0.5*(u_record.params[index]+u_record.params[index+1])
    energy_data=dict(initial_pressure=p0.params[...,0].tolist(),
                     final_velocity=[velocity[...,i].tolist() for i in range(2)])
print(json.dumps(dict(pressure=pressure[index,:,:,0].tolist(),
 sample_time_s=(index+1)*float(axis.dt),output_index=index,
 pressure_sample_origin="after_first_update",energy_data=energy_data,
 dt_s=float(axis.dt),dx_m=[dx,dx],parameters=dict(domain_N=[size,size],dx_m=dx,
 sound_speed_m_s=speed,density_kg_m3=density,pml_size=0,cfl={cfl},t_end_s=2.2e-6)),
 allow_nan=False))
""".strip()

def main():
    sys.path.insert(0,"/app")
    sys.path.insert(0,"/workspace")
    import executor
    from app import physics_consistency as gate
    records={}
    for name,size,cfl,equivalent in [("base",64,.1,False),("grid",128,.1,False),
                                    ("time",64,.05,False),("equivalent",64,.1,True)]:
        code=recipe(size,cfl,equivalent)
        result=executor.execute(dict(code=code,timeout_seconds=30))
        record=dict(case=name,code_sha256=hashlib.sha256(code.encode()).hexdigest(),
                    exit_code=result.get("exit_code"),timed_out=result.get("timed_out"),
                    duration_ms=result.get("duration_ms"),stdout=result.get("stdout",""),
                    stderr=result.get("stderr",""))
        # No environment, image configuration, tokens or private material.
        print(json.dumps(dict(record=record)),flush=True)
        if record["exit_code"]!=0 or record["timed_out"]:
            raise RuntimeError("isolated_consistency_execution_failed")
        records[name]=record
    from app.controlled_consistency import assess
    bundle=assess({name:dict(code_sha256=record["code_sha256"],stdout=record["stdout"])
        for name,record in records.items()})
    checks=bundle["checks"]
    print(json.dumps(dict(summary=dict(checks=checks,
        energy_actual_measurement=True,
        energy_scope="source_free_uniform_per_unit_depth_velocity_time_centered",scope="isolated_common_node_diagnostic_only",
        trusted_run=False,runtime_approved=False,run_bound=False,parameters_bound=False,
        learning_enabled=False))),flush=True)
    return 0 if bundle["numeric_pass"] else 1

if __name__=="__main__":
    raise SystemExit(main())
