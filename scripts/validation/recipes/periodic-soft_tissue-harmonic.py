import json
import jax.numpy as jnp
import jwave as jw
from jaxdf import FourierSeries
from jwave.acoustics.time_varying import TimeWavePropagationSettings
CFG = {'domain_N': [64, 64], 'domain_dx_m': [0.0005, 0.0005], 'sound_speed_m_s': 1540.0, 'density_kg_m3': 1050.0, 'pml_size': 0, 'cfl': 0.05, 't_end_s': 2.077922077922078e-05, 'initial_pressure_pa': 1.0, 'initial_velocity': 'zero', 'smooth_initial': False, 'boundary_condition': 'periodic', 'mode_components': [(1, 0, 0.7), (3, 0, 0.3)], 'sensor_indices': [[7, 11], [19, 27], [31, 43]], 'snapshot_indices': [0, 320, 639]}
domain=jw.Domain(tuple(CFG["domain_N"]),tuple(CFG["domain_dx_m"]))
medium=jw.Medium(domain,sound_speed=CFG["sound_speed_m_s"],density=CFG["density_kg_m3"],pml_size=0)
axis=jw.TimeAxis.from_medium(medium,cfl=CFG["cfl"],t_end=CFG["t_end_s"])
x=jnp.arange(64,dtype=jnp.float32)[:,None]
y=jnp.arange(64,dtype=jnp.float32)[None,:]
initial=sum(a*jnp.cos(2*jnp.pi*(mx*x+my*y)/64) for mx,my,a in CFG["mode_components"])
field=jw.simulate_wave_propagation(medium,axis,p0=FourierSeries(initial,domain),
    settings=TimeWavePropagationSettings(smooth_initial=False)).params
times=axis.to_array()+axis.dt
raw=dict(profile='managed-local-periodic-soft_tissue-harmonic.v1',configuration=CFG,units={"time":"s","pressure":"Pa"},
 time=[float(t) for t in times],
 traces=[[float(v) for v in field[:,i,j,0]] for i,j in CFG["sensor_indices"]],
 snapshots=[[float(v) for v in field[k,:,:,0].reshape(-1)] for k in CFG["snapshot_indices"]])
print(json.dumps({"raw_evidence":raw},allow_nan=False))
