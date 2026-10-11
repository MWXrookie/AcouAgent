"""Reviewed exact local profile catalog; no caller-extensible registry."""
import hashlib
from pathlib import Path
from app import controlled_dispatch
from app.skill_store import normalize_parameters
from scripts.validation import plane_wave_run_binding as pins
from scripts.validation.interface_waveform import WIDE_CONFIGURATION
PLANE="managed-local-plane-wave.v1"
INTERFACE="managed-local-interface-wide.v1"
INTERFACE_CODE_SHA256="d28fb8bee0b0dfdd98a5833797b1e43cf80214aacf2cf3a1a3c0a553498a4df3"
INTERFACE_PINS={
 "interface_waveform.py":"b9405a15c8a9df871f0094cb2516b31e6a02b0ec1c9d8b0a7ce80f8b57502e4c",
 "interface_evidence.py":"d77a8b146340c48001ea218d8ecce761132502d18c3079bbd74ef9dfdd882971",
 "cylindrical_evidence.py":"30c7e88a5e4f9d55297e5b63b7696c55add8450e3670308ac66d3b4af1ee79ce"
}

def specification(scope=PLANE):
    if scope==PLANE:
        return dict(scope=PLANE,scene="initial_pressure_2d",raw_profile="val1-plane-wave-v2",
            recipe="val1_plane_wave_v2.py",code_sha256=pins.RECIPE_SHA256,
            validator_sha256=pins.VALIDATOR_SHA256,validator_pins={"plane_wave_evidence.py":pins.VALIDATOR_SHA256},
            parameters=controlled_dispatch.fixed_retrieval_parameters(),
            state_text="本机受控执行器；固定均匀水介质高斯初压")
    if scope!=INTERFACE:raise ValueError("unreviewed_local_profile")
    cfg=WIDE_CONFIGURATION
    params=normalize_parameters(dict(domain_N=cfg["domain_N"],dimensions=2,dx_m=cfg["domain_dx_m"],
        sound_speed_m_s=cfg["sound_speed_m_s"][0],density_kg_m3=cfg["density_kg_m3"],
        secondary_sound_speed_m_s=cfg["sound_speed_m_s"][1],
        interface_position_m=(cfg["interface_x_index"]-.5)*cfg["domain_dx_m"][0],
        pml_size=cfg["pml_size"],cfl=cfg["cfl"],t_end_s=cfg["t_end_s"],
        initial_pressure_pa=cfg["initial_pressure_pa"],initial_velocity=cfg["initial_velocity"],
        initial_sigma_m=cfg["sigma_grid"]*cfg["domain_dx_m"][0],
        source_type="gaussian_initial_pressure",source_geometry="planar_x_uniform_y",
        source_index=[cfg["source_x_index"],cfg["domain_N"][1]//2],
        sensor_indices=cfg["sensor_indices"],smooth_initial=cfg["smooth_initial"],
        solver_mode="time_domain",medium_type="lossless_two_halfspaces"))
    return dict(scope=INTERFACE,scene="interface_2d",raw_profile="val1-interface-wide-v2",
        recipe="val1_interface_wide_v2.py",code_sha256=INTERFACE_CODE_SHA256,
        validator_sha256=INTERFACE_PINS["interface_waveform.py"],validator_pins=INTERFACE_PINS,
        parameters=params,state_text="本机受控执行器；固定两均匀半空间法向入射，宽横向域")

def from_raw(value):
    raw=value["raw_evidence"]
    if raw["profile"]=="val1-plane-wave-v2":return specification(PLANE)
    if raw["profile"]=="val1-interface-wide-v2":return specification(INTERFACE)
    raise ValueError("unreviewed_local_profile")

def matching(scene,parameters):
    for scope in (PLANE,INTERFACE):
        spec=specification(scope)
        if spec["scene"]==scene and spec["parameters"]==parameters:return spec
    return None

def recipe(scope=PLANE):
    spec=specification(scope)
    code=(pins.DIRECTORY/"recipes"/spec["recipe"]).read_text().strip()
    if pins.digest(code)!=spec["code_sha256"]:raise ValueError("local_unreviewed_recipe")
    for filename,digest in spec["validator_pins"].items():
        if hashlib.sha256((pins.DIRECTORY/filename).read_bytes()).hexdigest()!=digest:
            raise ValueError("local_unreviewed_validator")
    return code
