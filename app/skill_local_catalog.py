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

PERIODIC_RECIPES={'managed-local-periodic-water-axial.v1': ('periodic-water-axial.py', '1127686cc15179eb22637dc419d831abb83984fb0f607be1493d43f9e6f8d6e0'), 'managed-local-periodic-water-oblique.v1': ('periodic-water-oblique.py', 'd8cc1f074744599c428571909414dfa3ca7a6ae436cf42187f65c7b22b9402f1'), 'managed-local-periodic-water-crossed.v1': ('periodic-water-crossed.py', '29a993deeb3eee35a6ec20f9a6d7ecffad374b3209f540368d5235e14b004ca5'), 'managed-local-periodic-water-harmonic.v1': ('periodic-water-harmonic.py', '8e068c63fbf5e5a69c2834ea961a1fdfa2b994537ab6361b0f9d51b51923dac6'), 'managed-local-periodic-water-beating.v1': ('periodic-water-beating.py', '5e25f010233d59368dacfdad19860a31a3531d7473851fc594a184c46b399d48'), 'managed-local-periodic-soft_tissue-axial.v1': ('periodic-soft_tissue-axial.py', '8f6e54b2efabcc5fe156bf4dcf9e8c1befd0e7d5bd2c3fca2a38c441f0bca824'), 'managed-local-periodic-soft_tissue-oblique.v1': ('periodic-soft_tissue-oblique.py', '5f9fcd2a6ad9d332a0cf9011d84f1a48e9091c54c1fd1b3ddaa07992e52c31a6'), 'managed-local-periodic-soft_tissue-crossed.v1': ('periodic-soft_tissue-crossed.py', '27e6dc1800ffd1f5e67994c4b629781671e217b2d7f223be8aa942d0900242b9'), 'managed-local-periodic-soft_tissue-harmonic.v1': ('periodic-soft_tissue-harmonic.py', '76d117863eb14881df55325157359f31884f15cbc26f2e1912c144c88755560a'), 'managed-local-periodic-soft_tissue-beating.v1': ('periodic-soft_tissue-beating.py', '37ad1b8e38a26330f6554a2e7fd9b2d69721d153992f9ac95a9e8d845efcedc1'), 'managed-local-periodic-air-axial.v1': ('periodic-air-axial.py', 'f6ad4216b55f8ef985a7529b573ed8df60a34be7d306594268a98b34c23517c6'), 'managed-local-periodic-air-oblique.v1': ('periodic-air-oblique.py', 'f89a630216af4a89c84042f0c198a9193184b17f1cb407b2abc2399d2df378ef'), 'managed-local-periodic-air-crossed.v1': ('periodic-air-crossed.py', '5bd6b556745bb6fd44d0ffdfcdba5c183d7acbdb2f7018d62db4eb71fbdcf128'), 'managed-local-periodic-air-harmonic.v1': ('periodic-air-harmonic.py', '94dc79d46bddbb9a5d1e87beb89b8d26052b0f8c4b589a37df2f34f09a6401bc'), 'managed-local-periodic-air-beating.v1': ('periodic-air-beating.py', '0a922df1099ec77f9bad3598a0e72c94ea067fb8d8cf7ccf776da955fcf6c553')}
PERIODIC_VALIDATOR_SHA256='9be4f09e0de1be0e0573ecf71996492130880e3733c1da99c770fa681e314034'
SCOPES=(PLANE,INTERFACE,*PERIODIC_RECIPES)

def specification(scope=PLANE):
    if scope in PERIODIC_RECIPES:
        from scripts.validation import periodic_eigenmodes as g
        cfg=g.configuration(scope);name,digest=PERIODIC_RECIPES[scope]
        parameters=normalize_parameters(dict(domain_N=cfg["domain_N"],dimensions=2,dx_m=cfg["domain_dx_m"],
            sound_speed_m_s=cfg["sound_speed_m_s"],density_kg_m3=cfg["density_kg_m3"],
            pml_size=0,cfl=cfg["cfl"],t_end_s=cfg["t_end_s"],boundary_condition="periodic",
            initial_pressure_pa=1.0,initial_velocity="zero",smooth_initial=False,
            source_type="modal_initial_pressure",source_geometry=scope,
            modal_amplitudes_pa=[a for _,_,a in cfg["mode_components"]],
            modal_wavenumber_rad_per_m=[[2*3.141592653589793*mx/.032,2*3.141592653589793*my/.032] for mx,my,_ in cfg["mode_components"]],
            sensor_indices=cfg["sensor_indices"],solver_mode="time_domain",medium_type="homogeneous_lossless"))
        return dict(scope=scope,scene="initial_pressure_2d",raw_profile=scope,recipe=name,
            code_sha256=digest,validator_sha256=PERIODIC_VALIDATOR_SHA256,
            validator_pins={"periodic_eigenmodes.py":PERIODIC_VALIDATOR_SHA256},parameters=parameters,
            state_text=g.title(scope)+"；二维周期边界，均匀无损、固定初压模态")
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
    if raw["profile"] in PERIODIC_RECIPES:return specification(raw["profile"])
    if raw["profile"]=="val1-plane-wave-v2":return specification(PLANE)
    if raw["profile"]=="val1-interface-wide-v2":return specification(INTERFACE)
    raise ValueError("unreviewed_local_profile")

def matching(scene,parameters):
    for scope in SCOPES:
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
