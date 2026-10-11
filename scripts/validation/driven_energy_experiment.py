"""Repeatable isolated uniform-source calibration through executor only.

Run in the preserved physics image/constraints after host thread limits permit.
This is a forced uniform field, not a propagating point source, general energy
certificate or production trust root. No observer/credentials/services here.
"""
import hashlib
import json
import math

SOURCE_SEMANTICS_SHA256="0bfa06286f3aba0412c87fd90c41cf30ccf48a45c0b584b7873edadbf3175221"
VALIDATOR_VERSION="uniform-driven-energy.v1"


def recipe(cfl=.1):
    if type(cfl) is not float or cfl not in (.1,.05):
        raise ValueError("reviewed_cfl_required")
    return f"""
import json
import hashlib
from pathlib import Path
import jax.numpy as jnp
import jwave as jw
from jaxdf import FourierSeries
from jwave.acoustics.time_varying import TimeWavePropagationSettings
import jwave.acoustics.time_varying as tv
if hashlib.sha256(Path(tv.__file__).read_bytes()).hexdigest()!="{SOURCE_SEMANTICS_SHA256}":
    raise ValueError("source_update_semantics_changed")
size=32
dx=.001
c=1500.
rho=1000.
q=1e6
domain=jw.Domain((size,size),(dx,dx))
medium=jw.Medium(domain,sound_speed=c,density=rho,pml_size=0)
axis=jw.TimeAxis.from_medium(medium,cfl={cfl},t_end=4e-7)
p0=FourierSeries(jnp.ones((size,size,1)),domain)
# Let the pinned solver construct its zero velocity field (u0=None).
# Installed mass-conservation update contributes 2*mass/(c*ndim*dx)
# per density component; pressure sums components and multiplies by c^2.
# Equal dx therefore gives q = 2*c*mass/dx, not q=source amplitude.
mass=q*dx/(2*c)
positions=(tuple(i for i in range(size) for j in range(size)),
           tuple(j for i in range(size) for j in range(size)))
sources=jw.Sources(positions=positions,
 signals=jnp.full((size*size,int(axis.Nt)),mass),dt=axis.dt,domain=domain)
recording=jw.simulate_wave_propagation(medium,axis,p0=p0,
 sources=sources,settings=TimeWavePropagationSettings(smooth_initial=False),
 sensors=lambda p,u,rho:dict(pressure=p,velocity=u))
p_record,u_record=recording['pressure'],recording['velocity']
steps=int(round((5*.1*dx/c)/float(axis.dt)))
pressure=[p0.params[...,0].tolist()]+p_record.params[:steps,...,0].tolist()
velocity=[[[[0. for j in range(size)] for i in range(size)] for k in range(2)]]
velocity+=[[u_record.params[n,...,k].tolist() for k in range(2)] for n in range(steps)]
print(json.dumps(dict(pressure_pa=pressure,velocity_m_s=velocity,
 times_s=[n*float(axis.dt) for n in range(steps+1)],dt_s=float(axis.dt),
 source_semantics_sha256="{SOURCE_SEMANTICS_SHA256}"),allow_nan=False))
""".strip()


def assess_record(code_sha256,stdout,cfl):
    from app.physics_energy_balance import assess_energy_balance
    from app.physics_consistency import FALSE_TRUST,_array
    from scripts.validation.host_forward_signature import unique,reject
    denied=dict(FALSE_TRUST,numeric_pass=False,quality_level="Q0",
        actual_use_authenticated=False,validator_version=VALIDATOR_VERSION,
        reason="uniform_driven_raw_evidence_unavailable")
    try:
        if code_sha256!=hashlib.sha256(recipe(cfl).encode()).hexdigest():
            raise ValueError("recipe_changed")
        if type(stdout) is not str or len(stdout.encode())>1000000:
            raise ValueError("raw_output_budget")
        raw=json.loads(stdout,object_pairs_hook=unique,parse_constant=reject)
        if type(raw) is not dict or set(raw)!={"pressure_pa","velocity_m_s","times_s","dt_s","source_semantics_sha256"}:
            raise ValueError("raw_fields")
        dt=cfl*.001/1500.;steps=5 if cfl==.1 else 10
        times=raw["times_s"];pressure=raw["pressure_pa"];velocity=raw["velocity_m_s"]
        if (raw["source_semantics_sha256"]!=SOURCE_SEMANTICS_SHA256
                or type(raw["dt_s"]) not in (float,int) or not math.isclose(raw["dt_s"],dt,rel_tol=1e-7)
                or type(times) is not list or len(times)!=steps+1
                or any(type(t) not in (int,float) or not math.isclose(t,n*dt,rel_tol=1e-7,abs_tol=0.) for n,t in enumerate(times))
                or type(pressure) is not list or len(pressure)!=len(times)
                or type(velocity) is not list or len(velocity)!=len(times)):
            raise ValueError("physical_sampling")
        trajectory_error=0.
        for t,p,u in zip(times,pressure,velocity):
            shape,values=_array(p)
            if shape!=(32,32) or type(u) is not list or len(u)!=2:raise ValueError("raw_geometry")
            expected=1.+1e6*t
            trajectory_error=max(trajectory_error,max(abs(v/expected-1) for v in values))
            for component in u:
                shape,values=_array(component)
                if shape!=(32,32) or any(abs(v)>1e-12 for v in values):
                    raise ValueError("nonuniform_staggered_velocity_unsupported")
        zero=[[0.]*32 for _ in range(32)]
        rate=[[1e6]*32 for _ in range(32)]
        faces={}
        for key in ("0-","0+","1-","1+"):
            frames=[]
            for p in pressure:
                edge=p[0] if key=="0-" else p[-1] if key=="0+" else [row[0 if key=="1-" else -1] for row in p]
                frames.append(dict(pressure_pa=edge,outward_velocity_m_s=[0.]*32))
            faces[key]=frames
        budget=assess_energy_balance(times_s=times,pressure_pa=pressure,velocity_m_s=velocity,
            pressure_rate_pa_s=[rate]*len(times),acceleration_m_s2=[[zero,zero]]*len(times),
            boundary_faces=faces,spacing_m=[.001,.001],density_kg_m3=1000.,sound_speed_m_s=1500.,
            model="uniform_lossless_stationary_linear",sampling="collocated_cell_volume_and_face_area")
        passed=budget["passed"] is True and trajectory_error<.01
        return dict(denied,numeric_pass=passed,quality_level="Q2" if passed else "Q1",
            trajectory_relative_error=trajectory_error,energy_balance=budget,
            stdout_sha256=hashlib.sha256(stdout.encode()).hexdigest(),
            reason="uniform_source_calibration_only_not_authenticated")
    except (ValueError,TypeError,KeyError,OverflowError,RecursionError,UnicodeError):
        return denied


def assess_pair(records):
    """Both raw fixed trajectories and their 2:1 temporal refinement must pass.

    This is a prerequisite diagnostic, not authenticated source/physical
    acceptance. No sample, failed case or caller metric can be omitted.
    """
    from app.physics_consistency import FALSE_TRUST,_array,_difference
    from scripts.validation.host_forward_signature import unique,reject
    denied=dict(FALSE_TRUST,numeric_pass=False,quality_level="Q0",
        actual_use_authenticated=False,refinement_authenticated=False,
        validator_version=VALIDATOR_VERSION,reason="incomplete_uniform_source_pair")
    try:
        if type(records) is not list or len(records)!=2:
            raise ValueError("complete_pair_required")
        raw={};checked={}
        for record in records:
            if (type(record) is not dict or set(record)!={"cfl","code_sha256","stdout","stderr","exit_code","timed_out"}
                    or type(record["cfl"]) is not float or record["cfl"] not in (.1,.05)
                    or record["cfl"] in raw or type(record["exit_code"]) is not int
                    or record["exit_code"]!=0 or record["timed_out"] is not False or record["stderr"]!=""):
                raise ValueError("raw_pair_identity_or_execution")
            case=record["cfl"]
            checked[case]=assess_record(record["code_sha256"],record["stdout"],case)
            if not checked[case]["numeric_pass"]:raise ValueError("individual_trajectory_failed")
            raw[case]=json.loads(record["stdout"],object_pairs_hook=unique,parse_constant=reject)
        coarse=raw[.1];fine=raw[.05]
        # assess_record already checks the complete reviewed physical times,
        # shapes, source mapping and zero particle velocity at every instant.
        pressure_error=max(_difference(_array(fine["pressure_pa"][2*i])[1],
            _array(p)[1]) for i,p in enumerate(coarse["pressure_pa"]))
        coarse_energy=checked[.1]["energy_balance"]["energy_samples"]
        fine_energy=checked[.05]["energy_balance"]["energy_samples"][::2]
        energy_error=_difference(fine_energy,coarse_energy)
        coarse_work=checked[.1]["energy_balance"]["source_work_intervals"]
        fine_work=checked[.05]["energy_balance"]["source_work_intervals"]
        paired_work=[math.fsum(fine_work[i:i+2]) for i in range(0,len(fine_work),2)]
        work_error=_difference(paired_work,coarse_work)
        errors=dict(pressure=pressure_error,energy=energy_error,source_work=work_error)
        passed=all(value<.01 for value in errors.values())
        return dict(denied,numeric_pass=passed,quality_level="Q2" if passed else "Q1",
            relative_errors=errors,individual_checks={str(case):result for case,result in checked.items()},
            common_sample_count=len(coarse["times_s"]),fine_intervals_per_coarse=2,
            reason="uniform_source_pair_diagnostic_not_authenticated")
    except (ValueError,TypeError,KeyError,OverflowError,RecursionError,UnicodeError):
        return denied


def main():
    import sys
    sys.path.insert(0,"/app")
    sys.path.insert(0,"/workspace")
    import executor
    records=[]
    for cfl in (.1,.05):
        code=recipe(cfl)
        result=executor.execute(dict(code=code,timeout_seconds=30))
        record=dict(cfl=cfl,code_sha256=hashlib.sha256(code.encode()).hexdigest(),
            stdout=result.get("stdout",""),stderr=result.get("stderr",""),
            exit_code=result.get("exit_code"),timed_out=result.get("timed_out"))
        print(json.dumps(dict(record=record)),flush=True)
        if record["exit_code"]!=0 or record["timed_out"] is not False:
            raise RuntimeError("uniform_source_execution_failed")
        checked=assess_record(record["code_sha256"],record["stdout"],cfl)
        print(json.dumps(dict(assessment=checked)),flush=True)
        records.append(record)
    paired=assess_pair(records)
    print(json.dumps(dict(pair_assessment=paired)),flush=True)
    return 0 if paired["numeric_pass"] else 1


if __name__=="__main__":
    raise SystemExit(main())
