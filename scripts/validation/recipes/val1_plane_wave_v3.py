"""Unapproved diagnostic profile with explicit after-update pressure timestamps."""
import json
import jax.numpy as jnp
import jwave as jw
from jaxdf import FourierSeries
from jwave.acoustics.time_varying import TimeWavePropagationSettings
domain=jw.Domain((384,384),(0.00025,0.00025))
medium=jw.Medium(domain,sound_speed=1500.0,density=1000.0,pml_size=20)
axis=jw.TimeAxis.from_medium(medium,cfl=0.1,t_end=34e-6)
x=jnp.arange(384,dtype=jnp.float32)
initial=FourierSeries(jnp.broadcast_to(jnp.exp(-((x-160)**2)/(2*12**2))[:,None],(384,384)),domain)
field=jw.simulate_wave_propagation(medium,axis,p0=initial,
    settings=TimeWavePropagationSettings(smooth_initial=False)).params
t=(jnp.arange(field.shape[0])+1)*axis.dt
print(json.dumps(dict(raw_evidence=dict(profile="val1-plane-wave-v3",
    configuration=dict(domain_N=[384,384],domain_dx_m=[0.00025,0.00025],
        sound_speed_m_s=1500.0,density_kg_m3=1000.0,pml_size=20,cfl=0.1,t_end_s=34e-6,
        initial_pressure_pa=1.0,initial_velocity="zero",source_x_index=160,sigma_grid=12,
        sensor_indices=[[224,192],[304,192]],smooth_initial=False,sample_origin="after_update"),
    units=dict(time="s",pressure="Pa"),dt_s=float(axis.dt),time=[float(v) for v in t],
    pressure_x1=[float(v) for v in field[:,224,192,0]],
    pressure_x2=[float(v) for v in field[:,304,192,0]])),allow_nan=False))
