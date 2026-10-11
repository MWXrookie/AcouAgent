"""Controlled 3D burst spreading; probes strictly inside the physical PML region."""
import json
import jax.numpy as jnp
import jwave as jw
from jwave.acoustics.time_varying import TimeWavePropagationSettings
N=72
dx=0.0005
speed=1500.0
frequency=300000.0
domain=jw.Domain((N,N,N),(dx,dx,dx))
medium=jw.Medium(domain,sound_speed=speed,density=1000.0,pml_size=8)
axis=jw.TimeAxis.from_medium(medium,cfl=0.1,t_end=25e-6)
signal=jw.signal_processing.tone_burst(1.0/axis.dt,frequency,3)
signal=jnp.concatenate([signal,jnp.zeros(int(axis.Nt)-len(signal))])
sources=jw.Sources((jnp.array([36]),jnp.array([36]),jnp.array([36])),signal[None,:],axis.dt,domain)
field=jw.simulate_wave_propagation(medium,axis,sources=sources,
    settings=TimeWavePropagationSettings(smooth_initial=False)).params
time=(jnp.arange(field.shape[0])+1)*axis.dt
print(json.dumps(dict(raw_evidence=dict(profile="val1-spherical-v1",
    configuration=dict(domain_N=[N,N,N],domain_dx_m=[dx,dx,dx],sound_speed_m_s=speed,
        density_kg_m3=1000.0,pml_size=8,cfl=0.1,t_end_s=25e-6,
        source_frequency_hz=frequency,burst_cycles=3,source_index=[36,36,36],
        sensor_indices=[[52,36,36],[60,36,36]],sample_origin="after_update"),
    units=dict(time="s",pressure="Pa"),dt_s=float(axis.dt),
    time=[float(t) for t in time],
    pressure_r1=[float(p) for p in field[:,52,36,36,0]],
    pressure_r2=[float(p) for p in field[:,60,36,36,0]])),allow_nan=False))
