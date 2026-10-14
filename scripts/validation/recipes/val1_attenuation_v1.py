"""Controlled frequency-domain absorption benchmark, raw complex probes only."""
import json
import jax.numpy as jnp
import jwave as jw
from jwave.acoustics import helmholtz_solver
domain=jw.Domain((160,160),(0.0005,0.0005))
medium=jw.Medium(domain,sound_speed=1500.0,density=1000.0,attenuation=1.0,pml_size=20)
src=jw.FourierSeries(jnp.zeros((160,160),dtype=jnp.complex64).at[80,80].set(1.0),domain)
field=jnp.asarray(helmholtz_solver(medium,2*jnp.pi*1e6,src,method="gmres").on_grid).reshape(160,160)
probes=[field[104,80],field[128,80]]
print(json.dumps(dict(raw_evidence=dict(profile="val1-attenuation-v1",
    configuration=dict(domain_N=[160,160],domain_dx_m=[0.0005,0.0005],
        sound_speed_m_s=1500.0,density_kg_m3=1000.0,pml_size=20,
        source_frequency_hz=1000000.0,attenuation_db_mhz2_cm=1.0,attenuation_power=2.0,
        solver_mode="frequency_domain",solver_method="gmres",source_index=[80,80],
        sensor_indices=[[104,80],[128,80]]),
    units=dict(pressure="Pa",attenuation="dB/(MHz^2 cm)"),
    pressure_complex=[[float(jnp.real(p)),float(jnp.imag(p))] for p in probes])),allow_nan=False))
