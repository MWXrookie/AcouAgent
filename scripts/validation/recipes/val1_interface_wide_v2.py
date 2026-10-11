"""Controlled wider transverse interface experiment, raw traces; no acceptance grant."""
import json
import jax.numpy as jnp
import jwave as jw
from jaxdf import FourierSeries
from jwave.acoustics.time_varying import TimeWavePropagationSettings
Nx,Ny=384,320
dy=0.0005
dx=0.00025
xs=jnp.arange(Nx,dtype=jnp.float32)
domain=jw.Domain((Nx,Ny),(dx,dy))
speed=jnp.broadcast_to(jnp.where(xs<192,1500.0,2500.0)[:,None],(Nx,Ny))
density=jnp.broadcast_to(jnp.where(xs<192,1000.0,2000.0)[:,None],(Nx,Ny))
medium=jw.Medium(domain,sound_speed=speed,density=density,pml_size=20)
axis=jw.TimeAxis.from_medium(medium,cfl=0.1,t_end=24e-6)
p0=FourierSeries(jnp.broadcast_to(jnp.exp(-((xs-128)**2)/(2*10**2))[:,None],(Nx,Ny)),domain)
field=jw.simulate_wave_propagation(medium,axis,p0=p0,
    settings=TimeWavePropagationSettings(smooth_initial=False)).params
times=(jnp.arange(field.shape[0])+1)*axis.dt
print(json.dumps(dict(raw_evidence=dict(profile="val1-interface-wide-v2",
    configuration=dict(domain_N=[Nx,Ny],domain_dx_m=[dx,dy],sound_speed_m_s=[1500.0,2500.0],
        density_kg_m3=[1000.0,2000.0],interface_x_index=192,pml_size=20,cfl=0.1,t_end_s=24e-6,
        initial_pressure_pa=1.0,initial_velocity="zero",source_x_index=128,sigma_grid=10,
        sensor_indices=[[160,Ny//2],[224,Ny//2]],smooth_initial=False,sample_origin="after_update"),
    units=dict(time="s",pressure="Pa"),dt_s=float(axis.dt),
    time=[float(t) for t in times],
    pressure_left=[float(p) for p in field[:,160,Ny//2,0]],
    pressure_right=[float(p) for p in field[:,224,Ny//2,0]])),allow_nan=False))
