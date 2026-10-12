"""Portable extraction adapter: explicit units/scenario, no execution or trust."""
import json
import math

SCENARIOS={"point_source_2d","point_source_3d","plane_wave_2d","initial_pressure_2d",
           "interface_2d","attenuation_helmholtz_2d","heterogeneous_2d"}
ALLOWED={"sound_speed","density","source_frequency","domain_N","domain_dx","cfl",
         "pml_size","t_end","source_index","sensor_index","solver_mode","medium_type",
         "source_kind","initial_pressure_pa","initial_sigma_m","initial_velocity",
         "source_geometry","sensor_indices","smooth_initial"}
ALIASES={"sound_speed":"sound_speed_m_s","density":"density_kg_m3",
         "source_frequency":"frequency_hz","domain_N":"grid_shape","domain_dx":"dx_m","t_end":"t_end_s","source_kind":"source_type"}

def _unique(pairs):
    result={}
    for key,value in pairs:
        if key in result:raise ValueError("duplicate_key")
        result[key]=value
    return result

def _invalid(value):
    raise ValueError("nonfinite_value")

PERIODIC_PARAMETERS={'managed-local-periodic-water-axial.v1': {'grid_shape': [64, 64], 'dimensions': 2, 'dx_m': [0.0005, 0.0005], 'sound_speed_m_s': 1500.0, 'density_kg_m3': 1000.0, 'pml_size': 0, 'cfl': 0.05, 't_end_s': 2.1333333333333335e-05, 'boundary_condition': 'periodic', 'initial_pressure_pa': 1.0, 'initial_velocity': 'zero', 'smooth_initial': False, 'source_type': 'modal_initial_pressure', 'source_geometry': 'managed-local-periodic-water-axial.v1', 'modal_amplitudes_pa': [1.0], 'modal_wavenumber_rad_per_m': [[196.34954084936206, 0.0]], 'sensor_indices': [[7, 11], [19, 27], [31, 43]], 'solver_mode': 'time_domain', 'medium_type': 'homogeneous_lossless'}, 'managed-local-periodic-water-oblique.v1': {'grid_shape': [64, 64], 'dimensions': 2, 'dx_m': [0.0005, 0.0005], 'sound_speed_m_s': 1500.0, 'density_kg_m3': 1000.0, 'pml_size': 0, 'cfl': 0.05, 't_end_s': 2.1333333333333335e-05, 'boundary_condition': 'periodic', 'initial_pressure_pa': 1.0, 'initial_velocity': 'zero', 'smooth_initial': False, 'source_type': 'modal_initial_pressure', 'source_geometry': 'managed-local-periodic-water-oblique.v1', 'modal_amplitudes_pa': [1.0], 'modal_wavenumber_rad_per_m': [[196.34954084936206, 196.34954084936206]], 'sensor_indices': [[7, 11], [19, 27], [31, 43]], 'solver_mode': 'time_domain', 'medium_type': 'homogeneous_lossless'}, 'managed-local-periodic-water-crossed.v1': {'grid_shape': [64, 64], 'dimensions': 2, 'dx_m': [0.0005, 0.0005], 'sound_speed_m_s': 1500.0, 'density_kg_m3': 1000.0, 'pml_size': 0, 'cfl': 0.05, 't_end_s': 2.1333333333333335e-05, 'boundary_condition': 'periodic', 'initial_pressure_pa': 1.0, 'initial_velocity': 'zero', 'smooth_initial': False, 'source_type': 'modal_initial_pressure', 'source_geometry': 'managed-local-periodic-water-crossed.v1', 'modal_amplitudes_pa': [0.5, 0.5], 'modal_wavenumber_rad_per_m': [[196.34954084936206, 0.0], [0.0, 196.34954084936206]], 'sensor_indices': [[7, 11], [19, 27], [31, 43]], 'solver_mode': 'time_domain', 'medium_type': 'homogeneous_lossless'}, 'managed-local-periodic-water-harmonic.v1': {'grid_shape': [64, 64], 'dimensions': 2, 'dx_m': [0.0005, 0.0005], 'sound_speed_m_s': 1500.0, 'density_kg_m3': 1000.0, 'pml_size': 0, 'cfl': 0.05, 't_end_s': 2.1333333333333335e-05, 'boundary_condition': 'periodic', 'initial_pressure_pa': 1.0, 'initial_velocity': 'zero', 'smooth_initial': False, 'source_type': 'modal_initial_pressure', 'source_geometry': 'managed-local-periodic-water-harmonic.v1', 'modal_amplitudes_pa': [0.7, 0.3], 'modal_wavenumber_rad_per_m': [[196.34954084936206, 0.0], [589.0486225480862, 0.0]], 'sensor_indices': [[7, 11], [19, 27], [31, 43]], 'solver_mode': 'time_domain', 'medium_type': 'homogeneous_lossless'}, 'managed-local-periodic-water-beating.v1': {'grid_shape': [64, 64], 'dimensions': 2, 'dx_m': [0.0005, 0.0005], 'sound_speed_m_s': 1500.0, 'density_kg_m3': 1000.0, 'pml_size': 0, 'cfl': 0.05, 't_end_s': 2.1333333333333335e-05, 'boundary_condition': 'periodic', 'initial_pressure_pa': 1.0, 'initial_velocity': 'zero', 'smooth_initial': False, 'source_type': 'modal_initial_pressure', 'source_geometry': 'managed-local-periodic-water-beating.v1', 'modal_amplitudes_pa': [0.5, 0.5], 'modal_wavenumber_rad_per_m': [[392.6990816987241, 196.34954084936206], [589.0486225480862, 196.34954084936206]], 'sensor_indices': [[7, 11], [19, 27], [31, 43]], 'solver_mode': 'time_domain', 'medium_type': 'homogeneous_lossless'}, 'managed-local-periodic-soft_tissue-axial.v1': {'grid_shape': [64, 64], 'dimensions': 2, 'dx_m': [0.0005, 0.0005], 'sound_speed_m_s': 1540.0, 'density_kg_m3': 1050.0, 'pml_size': 0, 'cfl': 0.05, 't_end_s': 2.077922077922078e-05, 'boundary_condition': 'periodic', 'initial_pressure_pa': 1.0, 'initial_velocity': 'zero', 'smooth_initial': False, 'source_type': 'modal_initial_pressure', 'source_geometry': 'managed-local-periodic-soft_tissue-axial.v1', 'modal_amplitudes_pa': [1.0], 'modal_wavenumber_rad_per_m': [[196.34954084936206, 0.0]], 'sensor_indices': [[7, 11], [19, 27], [31, 43]], 'solver_mode': 'time_domain', 'medium_type': 'homogeneous_lossless'}, 'managed-local-periodic-soft_tissue-oblique.v1': {'grid_shape': [64, 64], 'dimensions': 2, 'dx_m': [0.0005, 0.0005], 'sound_speed_m_s': 1540.0, 'density_kg_m3': 1050.0, 'pml_size': 0, 'cfl': 0.05, 't_end_s': 2.077922077922078e-05, 'boundary_condition': 'periodic', 'initial_pressure_pa': 1.0, 'initial_velocity': 'zero', 'smooth_initial': False, 'source_type': 'modal_initial_pressure', 'source_geometry': 'managed-local-periodic-soft_tissue-oblique.v1', 'modal_amplitudes_pa': [1.0], 'modal_wavenumber_rad_per_m': [[196.34954084936206, 196.34954084936206]], 'sensor_indices': [[7, 11], [19, 27], [31, 43]], 'solver_mode': 'time_domain', 'medium_type': 'homogeneous_lossless'}, 'managed-local-periodic-soft_tissue-crossed.v1': {'grid_shape': [64, 64], 'dimensions': 2, 'dx_m': [0.0005, 0.0005], 'sound_speed_m_s': 1540.0, 'density_kg_m3': 1050.0, 'pml_size': 0, 'cfl': 0.05, 't_end_s': 2.077922077922078e-05, 'boundary_condition': 'periodic', 'initial_pressure_pa': 1.0, 'initial_velocity': 'zero', 'smooth_initial': False, 'source_type': 'modal_initial_pressure', 'source_geometry': 'managed-local-periodic-soft_tissue-crossed.v1', 'modal_amplitudes_pa': [0.5, 0.5], 'modal_wavenumber_rad_per_m': [[196.34954084936206, 0.0], [0.0, 196.34954084936206]], 'sensor_indices': [[7, 11], [19, 27], [31, 43]], 'solver_mode': 'time_domain', 'medium_type': 'homogeneous_lossless'}, 'managed-local-periodic-soft_tissue-harmonic.v1': {'grid_shape': [64, 64], 'dimensions': 2, 'dx_m': [0.0005, 0.0005], 'sound_speed_m_s': 1540.0, 'density_kg_m3': 1050.0, 'pml_size': 0, 'cfl': 0.05, 't_end_s': 2.077922077922078e-05, 'boundary_condition': 'periodic', 'initial_pressure_pa': 1.0, 'initial_velocity': 'zero', 'smooth_initial': False, 'source_type': 'modal_initial_pressure', 'source_geometry': 'managed-local-periodic-soft_tissue-harmonic.v1', 'modal_amplitudes_pa': [0.7, 0.3], 'modal_wavenumber_rad_per_m': [[196.34954084936206, 0.0], [589.0486225480862, 0.0]], 'sensor_indices': [[7, 11], [19, 27], [31, 43]], 'solver_mode': 'time_domain', 'medium_type': 'homogeneous_lossless'}, 'managed-local-periodic-soft_tissue-beating.v1': {'grid_shape': [64, 64], 'dimensions': 2, 'dx_m': [0.0005, 0.0005], 'sound_speed_m_s': 1540.0, 'density_kg_m3': 1050.0, 'pml_size': 0, 'cfl': 0.05, 't_end_s': 2.077922077922078e-05, 'boundary_condition': 'periodic', 'initial_pressure_pa': 1.0, 'initial_velocity': 'zero', 'smooth_initial': False, 'source_type': 'modal_initial_pressure', 'source_geometry': 'managed-local-periodic-soft_tissue-beating.v1', 'modal_amplitudes_pa': [0.5, 0.5], 'modal_wavenumber_rad_per_m': [[392.6990816987241, 196.34954084936206], [589.0486225480862, 196.34954084936206]], 'sensor_indices': [[7, 11], [19, 27], [31, 43]], 'solver_mode': 'time_domain', 'medium_type': 'homogeneous_lossless'}, 'managed-local-periodic-air-axial.v1': {'grid_shape': [64, 64], 'dimensions': 2, 'dx_m': [0.0005, 0.0005], 'sound_speed_m_s': 343.0, 'density_kg_m3': 1.225, 'pml_size': 0, 'cfl': 0.05, 't_end_s': 9.329446064139942e-05, 'boundary_condition': 'periodic', 'initial_pressure_pa': 1.0, 'initial_velocity': 'zero', 'smooth_initial': False, 'source_type': 'modal_initial_pressure', 'source_geometry': 'managed-local-periodic-air-axial.v1', 'modal_amplitudes_pa': [1.0], 'modal_wavenumber_rad_per_m': [[196.34954084936206, 0.0]], 'sensor_indices': [[7, 11], [19, 27], [31, 43]], 'solver_mode': 'time_domain', 'medium_type': 'homogeneous_lossless'}, 'managed-local-periodic-air-oblique.v1': {'grid_shape': [64, 64], 'dimensions': 2, 'dx_m': [0.0005, 0.0005], 'sound_speed_m_s': 343.0, 'density_kg_m3': 1.225, 'pml_size': 0, 'cfl': 0.05, 't_end_s': 9.329446064139942e-05, 'boundary_condition': 'periodic', 'initial_pressure_pa': 1.0, 'initial_velocity': 'zero', 'smooth_initial': False, 'source_type': 'modal_initial_pressure', 'source_geometry': 'managed-local-periodic-air-oblique.v1', 'modal_amplitudes_pa': [1.0], 'modal_wavenumber_rad_per_m': [[196.34954084936206, 196.34954084936206]], 'sensor_indices': [[7, 11], [19, 27], [31, 43]], 'solver_mode': 'time_domain', 'medium_type': 'homogeneous_lossless'}, 'managed-local-periodic-air-crossed.v1': {'grid_shape': [64, 64], 'dimensions': 2, 'dx_m': [0.0005, 0.0005], 'sound_speed_m_s': 343.0, 'density_kg_m3': 1.225, 'pml_size': 0, 'cfl': 0.05, 't_end_s': 9.329446064139942e-05, 'boundary_condition': 'periodic', 'initial_pressure_pa': 1.0, 'initial_velocity': 'zero', 'smooth_initial': False, 'source_type': 'modal_initial_pressure', 'source_geometry': 'managed-local-periodic-air-crossed.v1', 'modal_amplitudes_pa': [0.5, 0.5], 'modal_wavenumber_rad_per_m': [[196.34954084936206, 0.0], [0.0, 196.34954084936206]], 'sensor_indices': [[7, 11], [19, 27], [31, 43]], 'solver_mode': 'time_domain', 'medium_type': 'homogeneous_lossless'}, 'managed-local-periodic-air-harmonic.v1': {'grid_shape': [64, 64], 'dimensions': 2, 'dx_m': [0.0005, 0.0005], 'sound_speed_m_s': 343.0, 'density_kg_m3': 1.225, 'pml_size': 0, 'cfl': 0.05, 't_end_s': 9.329446064139942e-05, 'boundary_condition': 'periodic', 'initial_pressure_pa': 1.0, 'initial_velocity': 'zero', 'smooth_initial': False, 'source_type': 'modal_initial_pressure', 'source_geometry': 'managed-local-periodic-air-harmonic.v1', 'modal_amplitudes_pa': [0.7, 0.3], 'modal_wavenumber_rad_per_m': [[196.34954084936206, 0.0], [589.0486225480862, 0.0]], 'sensor_indices': [[7, 11], [19, 27], [31, 43]], 'solver_mode': 'time_domain', 'medium_type': 'homogeneous_lossless'}, 'managed-local-periodic-air-beating.v1': {'grid_shape': [64, 64], 'dimensions': 2, 'dx_m': [0.0005, 0.0005], 'sound_speed_m_s': 343.0, 'density_kg_m3': 1.225, 'pml_size': 0, 'cfl': 0.05, 't_end_s': 9.329446064139942e-05, 'boundary_condition': 'periodic', 'initial_pressure_pa': 1.0, 'initial_velocity': 'zero', 'smooth_initial': False, 'source_type': 'modal_initial_pressure', 'source_geometry': 'managed-local-periodic-air-beating.v1', 'modal_amplitudes_pa': [0.5, 0.5], 'modal_wavenumber_rad_per_m': [[392.6990816987241, 196.34954084936206], [589.0486225480862, 196.34954084936206]], 'sensor_indices': [[7, 11], [19, 27], [31, 43]], 'solver_mode': 'time_domain', 'medium_type': 'homogeneous_lossless'}}

def main(parameters_json: str, scenario_type: str="") -> dict:
    """No scenario guessing or prose parsing; invalid extraction bypasses learning."""
    closed=dict(ready=False,scenario_type="",candidate_parameters_json="{}",
                retrieval_parameters_json="{}",reason="invalid_skill_parameters")
    try:
        if type(parameters_json) is not str or len(parameters_json.encode())>4096:
            return closed
        params=json.loads(parameters_json,object_pairs_hook=_unique,parse_constant=_invalid)
        if type(params) is not dict:return closed
        description=params.pop("description",None)
        if description is not None and (type(description) is not str or len(description.encode())>512):
            return closed
        embedded=params.pop("scenario_type",None)
        if embedded is not None:
            if scenario_type and scenario_type!=embedded:return closed
            scenario_type=embedded
        periodic=PERIODIC_PARAMETERS.get(params.get("source_geometry"))
        if periodic is not None:
            if scenario_type!="initial_pressure_2d" or json.dumps(params,sort_keys=True,allow_nan=False)!=json.dumps(periodic,sort_keys=True,allow_nan=False):return closed
            text=json.dumps(periodic,sort_keys=True,separators=(",",":"),allow_nan=False)
            return dict(ready=True,scenario_type=scenario_type,candidate_parameters_json=text,retrieval_parameters_json=text,reason="declared_exact_periodic_parameters")
        if type(scenario_type) is not str or scenario_type not in SCENARIOS or not set(params)<=ALLOWED:
            return closed
        initial = scenario_type=="initial_pressure_2d"
        required={"sound_speed","domain_N","domain_dx","pml_size","solver_mode","medium_type"}
        required |= {"source_kind","initial_pressure_pa","initial_sigma_m","initial_velocity"} if initial else {"source_frequency"}
        if initial and (params.get("source_kind")!="gaussian_initial_pressure" or params.get("initial_velocity")!="zero"
                        or "source_frequency" in params):
            return closed
        if "source_kind" in params and params["source_kind"] not in {"continuous_sinusoid","gaussian_initial_pressure"}:
            return closed
        if not initial and params.get("source_kind")=="gaussian_initial_pressure":
            return closed
        if not required<=set(params):return closed
        if params["solver_mode"] not in {"time_domain","frequency_domain"}:
            return closed
        if params["medium_type"] not in {"water","homogeneous_water","homogeneous","heterogeneous","tissue","air"}:
            return closed
        shape=params["domain_N"]
        if (type(shape) is not list or len(shape)!=(3 if scenario_type.endswith("_3d") else 2)
                or any(type(v) is not int or v<32 for v in shape)):
            return closed
        for key in ("sound_speed","source_frequency","density","cfl","t_end","initial_pressure_pa","initial_sigma_m"):
            if key in params:
                value=params[key]
                if type(value) not in (int,float) or not math.isfinite(value) or value<=0:
                    return closed
        dx=params["domain_dx"]
        dx=dx if type(dx) is list else [dx]
        if len(dx) not in (1,len(shape)) or any(type(v) not in (int,float) or not math.isfinite(v) or v<=0 for v in dx):
            return closed
        pml=params["pml_size"]
        if type(pml) is not int or pml<0 or any(2*pml>=n for n in shape):return closed
        for key in ("source_index","sensor_index"):
            if key in params and (type(params[key]) is not list or len(params[key])!=len(shape)
                    or any(type(v) is not int or not pml<=v<n-pml for v,n in zip(params[key],shape))):
                return closed
        if "source_geometry" in params and params["source_geometry"] not in {"planar_x_uniform_y","radial_gaussian","point"}:
            return closed
        if "smooth_initial" in params and type(params["smooth_initial"]) is not bool:return closed
        if "sensor_indices" in params:
            positions=params["sensor_indices"]
            if (type(positions) is not list or not 1<=len(positions)<=3
                    or any(type(position) is not list or len(position)!=len(shape)
                        or any(type(v) is not int or not pml<=v<n-pml for v,n in zip(position,shape))
                        for position in positions)):return closed
        normalized={ALIASES.get(k,k):v for k,v in params.items()}
        normalized["dimensions"]=len(shape)
        # JSON numbers compare canonically in the server normalizer; use its
        # numeric aliases as floats so both request paths have identical hashes.
        for key in ("sound_speed_m_s","frequency_hz"):
            if key in normalized:normalized[key]=float(normalized[key])
        normalized["dx_m"]=[float(v) for v in dx] if type(params["domain_dx"]) is list else float(dx[0])
        canonical=lambda value:json.dumps(value,sort_keys=True,separators=(",",":"),allow_nan=False)
        return dict(ready=True,scenario_type=scenario_type,candidate_parameters_json=canonical(params),
                    retrieval_parameters_json=canonical(normalized),reason="declared_parameters_only")
    except (ValueError,TypeError,OverflowError,RecursionError,UnicodeError):
        return closed
