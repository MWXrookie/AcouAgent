"""Recompute a bounded four-experiment source-free consistency bundle.

Input is raw output plus exact reviewed recipe hashes, not producer verdicts.
This does not authenticate runs or authorize Q3. It is deliberately restricted
to the periodic uniform initial-pressure experiment; driven/heterogeneous
energy budgets need separate validators.
"""
import hashlib
import json
import math
from app import physics_consistency as gate
from scripts.validation.consistency_experiment import recipe
from scripts.validation.host_forward_signature import canonical,unique,reject
VALIDATOR_VERSION="controlled-consistency.v2"
PROFILES={"base":(64,.1,False),"grid":(128,.1,False),"time":(64,.05,False),"equivalent":(64,.1,True)}
FIELDS={"pressure","sample_time_s","output_index","pressure_sample_origin","energy_data","dt_s","dx_m","parameters"}

def assess(records):
    denied=dict(gate.FALSE_TRUST,numeric_pass=False,quality_level="Q0",checks=[],
        actual_use_authenticated=False,validator_version=VALIDATOR_VERSION,reason="invalid_controlled_consistency_bundle")
    try:
        if type(records) is not dict or set(records)!=set(PROFILES):raise ValueError("four_experiments_required")
        data={};hashes={}
        for name,(size,cfl,equivalent) in PROFILES.items():
            record=records[name]
            if type(record) is not dict or set(record)!={"code_sha256","stdout"}:
                raise ValueError("raw_experiment_fields")
            stdout=record["stdout"]
            if type(stdout) is not str or len(stdout.encode())>500000:raise ValueError("raw_output_budget")
            if type(record["code_sha256"]) is not str or record["code_sha256"]!=hashlib.sha256(recipe(size,cfl,equivalent).encode()).hexdigest():
                raise ValueError("controlled_recipe_changed")
            raw=json.loads(stdout,object_pairs_hook=unique,parse_constant=reject)
            expected=dict(domain_N=[size,size],dx_m=.032/size,sound_speed_m_s=1500.0,
                density_kg_m3=1000.0,pml_size=0,cfl=cfl,t_end_s=2.2e-6)
            if (type(raw) is not dict or set(raw)!=FIELDS or raw["parameters"]!=expected
                    or raw["pressure_sample_origin"]!="after_first_update"):
                raise ValueError("controlled_configuration")
            # Type validation must precede Python equality (True equals 1).
            params=raw["parameters"]
            if (type(params) is not dict or type(params.get("domain_N")) is not list
                    or any(type(v) is not int for v in params["domain_N"])
                    or type(params.get("pml_size")) is not int
                    or any(type(params.get(k)) not in (int,float) for k in expected if k not in {"domain_N","pml_size"})):
                raise ValueError("configuration_numeric_types")
            dx=.032/size;dt=cfl*dx/1500
            if (type(raw["output_index"]) is not int or raw["output_index"]!=round(2e-6/dt)-1
                    or type(raw["dt_s"]) not in (int,float) or not math.isclose(raw["dt_s"],dt,rel_tol=1e-9)
                    or type(raw["sample_time_s"]) not in (int,float) or not math.isclose(raw["sample_time_s"],2e-6,rel_tol=1e-9)
                    or type(raw["dx_m"]) is not list or len(raw["dx_m"])!=2
                    or any(type(v) not in (int,float) or not math.isclose(v,dx,rel_tol=1e-9) for v in raw["dx_m"])):
                raise ValueError("actual_grid_time_alignment")
            shape,_=gate._array(raw["pressure"])
            if shape!=(size,size):raise ValueError("actual_pressure_shape")
            if name=="base":
                energy=raw["energy_data"]
                if type(energy) is not dict or set(energy)!={"initial_pressure","final_velocity"}:
                    raise ValueError("raw_energy_fields")
                shape,values=gate._array(energy["initial_pressure"])
                if shape!=(size,size):raise ValueError("initial_pressure_geometry")
                analytic=[math.exp(-.5*((i*dx-.012)/.002)**2) for i in range(size) for j in range(size)]
                if max(abs(a-b) for a,b in zip(analytic,values))>=1e-5:
                    raise ValueError("initial_pressure_changed")
            elif raw["energy_data"]!={}:raise ValueError("unexpected_energy_fields")
            data[name]=raw;hashes[name]=hashlib.sha256(stdout.encode()).hexdigest()
        a,b,c,d=[data[k] for k in PROFILES]
        checks=[
            gate.grid_convergence_full_periodic(periodic=True,coarse_pressure=a["pressure"],refined_pressure=b["pressure"],
                coarse_dx_m=a["dx_m"],refined_dx_m=b["dx_m"],coarse_time_s=a["sample_time_s"],refined_time_s=b["sample_time_s"]),
            gate.time_step_convergence(base_pressure=a["pressure"],refined_pressure=c["pressure"],
                base_dt_s=a["dt_s"],refined_dt_s=c["dt_s"],base_time_s=a["sample_time_s"],refined_time_s=c["sample_time_s"]),
            gate.equivalent_parameters(base_pressure=a["pressure"],equivalent_pressure=d["pressure"],
                base_parameters=a["parameters"],equivalent_parameters=d["parameters"],
                base_time_s=a["sample_time_s"],equivalent_time_s=d["sample_time_s"]),
            gate.source_free_energy(initial_pressure=a["energy_data"]["initial_pressure"],final_pressure=a["pressure"],
                initial_velocity=[[[0.0]*64 for _ in range(64)] for _ in range(2)],
                final_velocity=a["energy_data"]["final_velocity"],density_kg_m3=1000,sound_speed_m_s=1500,
                cell_volume_m3=a["dx_m"][0]*a["dx_m"][1],source_free=True)]
        passed=all(check["passed"] is True for check in checks)
        return dict(denied,numeric_pass=passed,quality_level="Q2" if passed else "Q1",checks=checks,
            raw_output_sha256=hashes,reason="controlled_numerical_bundle_only_not_authenticated")
    except (ValueError,TypeError,KeyError,IndexError,OverflowError,RecursionError,UnicodeError):
        return denied


def configuration_for(case):
    """Actual reviewed experiment scope, not caller declared metadata."""
    if type(case) is not str or case not in PROFILES:
        raise ValueError("reviewed_consistency_case_required")
    size,cfl,equivalent=PROFILES[case]
    return dict(domain_N=[size,size],dx_m=[.032/size]*2,
        sound_speed_m_s=1500.,density_kg_m3=1000.,pml_size=0,cfl=cfl,
        t_end_s=2.2e-6,initial_pressure_pa=1.,initial_sigma_m=.002,
        initial_center_x_m=.012,initial_velocity_m_s=0.,
        source_type="gaussian_initial_pressure",source_geometry="planar_x_uniform_y",
        boundary="periodic",smooth_initial=False,alternate_units=equivalent,
        output_pressure_time_s=2e-6,output_sample_origin="after_first_update")


def assess_with_analytic_reference(records):
    """Consistency plus an independent exact-model comparison; still only Q2."""
    result=assess(records)
    if result["numeric_pass"] is not True:return result
    try:
        from app.physics_planar_initial import assess as analytic
        checks=list(result["checks"])
        for case in PROFILES:
            raw=json.loads(records[case]["stdout"],object_pairs_hook=unique,parse_constant=reject)
            check=analytic(pressure=raw["pressure"],sample_time_s=raw["sample_time_s"])
            checks.append(dict(check,case=case))
        passed=all(check["passed"] is True for check in checks)
        return dict(result,numeric_pass=passed,quality_level="Q2" if passed else "Q1",checks=checks,
            validator_version="controlled-analytic.v1",
            reason="consistency_and_reviewed_planar_analytic_diagnostic_not_authenticated")
    except (ValueError,TypeError,KeyError,IndexError,OverflowError,RecursionError,UnicodeError):
        return dict(result,numeric_pass=False,quality_level="Q0",reason="analytic_evidence_unavailable")
