"""Controlled 2D burst spreading; probes strictly inside the physical PML region."""
import json
import jax.numpy as jnp
import jwave as jw
from jwave.acoustics.time_varying import TimeWavePropagationSettings
N=256
dx=0.0005
speed=1500.0
frequency=200000.0
domain=jw.Domain((N,N),(dx,dx))
medium=jw.Medium(domain,sound_speed=speed,density=1000.0,pml_size=20)
axis=jw.TimeAxis.from_medium(medium,cfl=0.1,t_end=60e-6)
signal=jw.signal_processing.tone_burst(1.0/axis.dt,frequency,3)
signal=jnp.concatenate([signal,jnp.zeros(int(axis.Nt)-len(signal))])
sources=jw.Sources((jnp.array([128]),jnp.array([128])),signal[None,:],axis.dt,domain)
field=jw.simulate_wave_propagation(medium,axis,sources=sources,
    settings=TimeWavePropagationSettings(smooth_initial=False)).params
# jwave 0.2.1 emits pressure after its update, not at the initial state.
time=(jnp.arange(field.shape[0])+1)*axis.dt
print(json.dumps(dict(raw_evidence=dict(profile="val1-cylindrical-v1",
    configuration=dict(domain_N=[N,N],domain_dx_m=[dx,dx],sound_speed_m_s=speed,
        density_kg_m3=1000.0,pml_size=20,cfl=0.1,t_end_s=60e-6,
        source_frequency_hz=frequency,burst_cycles=3,source_index=[128,128],
        sensor_indices=[[192,128],[224,128]],sample_origin="after_update"),
    units=dict(time="s",pressure="Pa"),dt_s=float(axis.dt),
    time=[float(t) for t in time],
    pressure_r1=[float(p) for p in field[:,192,128,0]],
    pressure_r2=[float(p) for p in field[:,224,128,0]])),allow_nan=False))
