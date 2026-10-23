"""Server-controlled Helmholtz construction and exact external reference policies.

No database registration, dispatch, admission, or user selected reference path API.
"""
from __future__ import annotations

import copy
import difflib
import hashlib
import os
import json
import math
from pathlib import Path
import re
import stat

import numpy as np

from app.skill_spec import prepare_skill_spec
from app.skill_result_assets import _load

ADAPTER_VERSION = "helmholtz-controlled.v1"
VALIDATOR_VERSION = "kwave-magnitude-linf.v1"
REFERENCES = {
    "B01": "8f100773df80927a42ee2f3ad85e0e873b175ce0df5c1281926f63b6ebed8b2e",
    "B02": "fb23aa6aaedeae811a8b9be57c183a65b2add4d52932d2da2f08c598cb440676",
    "B03": "86c1dbc705ef16d8c03e86f9c5023af5b24fb957f52faa8aa8412a9bde372c49",
}
LIMITS = {"B01": .01, "B02": .01, "B03": .03}
RESULT = {"kind": "steady_complex_pressure", "units": "numerical_source_normalization",
          "axis_order": ["x", "y"], "grid_shape": [128, 128], "full_complex": True, "downsample": 1}


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def source_sha256():
    # Normalize checkout line endings: reviewed source identity is portable.
    return hashlib.sha256(Path(__file__).read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def _code(p):
    # Every literal below is from validated physical inputs; never a reference.
    material = []
    for key, local in (("sound_speed_m_s", "c"), ("density_kg_m3", "rho")):
        regions = [r for r in p["material_regions"] if key in r]
        if regions:
            material.append(f"{local}_grid = jnp.full(N, {p[key]!r})")
            for r in regions:
                a, b = r["start"], r["stop"]
                material.append(f"{local}_grid = {local}_grid.at[{a[0]}:{b[0]}, {a[1]}:{b[1]}].set({r[key]!r})")
            material.append(f"{local} = jw.FourierSeries({local}_grid, domain)")
    region_code = "\n".join(material)
    x, y = p["source_index"]
    omega = 2 * math.pi * p["frequency_hz"]
    return f'''import base64, io, json, zlib
import numpy as np
import jax.numpy as jnp
import jwave as jw
from jwave.acoustics import helmholtz_solver
N = {tuple(p["grid_shape"])!r}
domain = jw.Domain(N, {tuple(p["dx_m"])!r})
c, rho = {p["sound_speed_m_s"]!r}, {p["density_kg_m3"]!r}
{region_code}
medium = jw.Medium(domain, sound_speed=c, density=rho, attenuation={p["background_attenuation_db_mhz2_cm"]!r}, pml_size={p["pml_size"]!r})
grid = jnp.zeros(N, dtype=jnp.complex64).at[{x}, {y}].set({p["helmholtz_source_coefficient"]!r})
src = jw.FourierSeries(grid, domain)
u = helmholtz_solver(medium, {omega!r}, src, method="gmres", tol=1e-5)
field = np.asarray(u.on_grid).reshape(N).astype(np.complex64)
assert np.isfinite(field).all()
buf = io.BytesIO()
np.save(buf, field, allow_pickle=False)
blob = base64.b64encode(zlib.compress(buf.getvalue())).decode("ascii")
print("__SKILL_PROBE_START__" + json.dumps({{"quantity": "complex_pressure", "npy_zlib_base64": blob}}) + "__SKILL_PROBE_END__")
print("__ACOU_FIELD_START__")
print(json.dumps({{"data": np.abs(field).tolist(), "shape": list(N), "kind": "steady_state_amplitude", "downsample": 1}}))
print("__ACOU_FIELD_END__")
'''


def build_controlled_spec(raw):
    original = prepare_skill_spec(raw)
    spec = copy.deepcopy(original["spec"])
    code = _code(spec["parameters"])
    before = spec["code_template"]
    spec["code_template"] = code
    derived = prepare_skill_spec(_json(spec))
    return {**derived, "code_template": code,
            "source_content_sha256": original["content_sha256"],
            "derived_content_sha256": derived["content_sha256"],
            "adapter_id": ADAPTER_VERSION, "adapter_sha256": source_sha256(),
            "construction_sha256": hashlib.sha256(_json({"case_fingerprint": derived["case_fingerprint"],
                "code_sha256": derived["code_sha256"], "adapter_sha256": source_sha256()}).encode()).hexdigest(),
            "changed": before != code,
            "diff": "".join(difflib.unified_diff(before.splitlines(True), code.splitlines(True),
                                                fromfile="submitted", tofile="controlled"))}


def _case_parameters(case):
    p = {"dimensions": 2, "solver_mode": "frequency_domain", "medium_type": "homogeneous_water" if case == "B01" else "heterogeneous",
         "sound_speed_m_s": 1500., "density_kg_m3": 1000., "grid_shape": [128, 128], "dx_m": [.001, .001],
         "frequency_hz": (1500000. if case != "B03" else 1000000.) / (2 * math.pi),
         "pml_size": 16, "source_index": [32, 64 if case == "B03" else 32],
         "source_type": "continuous_sinusoid", "source_geometry": "point", "helmholtz_source_coefficient": 1.,
         "background_attenuation_db_mhz2_cm": 0., "material_regions": [], "boundary_condition": "absorbing_pml"}
    if case == "B02":
        p["material_regions"] = [{"geometry": "rectangle", "start": [50, 32], "stop": [90, 100], "sound_speed_m_s": 2300.}]
    if case == "B03":
        p["material_regions"] = [{"geometry": "half_space", "start": [64, 0], "stop": [128, 128], "density_kg_m3": 2000.}]
    return p


def match_profile(preparedspec):
    # Reparse instead of trusting a caller-supplied case fingerprint or normalized dict.
    spec = preparedspec.get("spec", preparedspec)
    actual = prepare_skill_spec(_json(spec))
    for case in REFERENCES:
        target = copy.deepcopy(actual["spec"])
        target.update(parameters=_case_parameters(case), result_spec=RESULT,
                      scenario_type={"B01": "point_source_2d", "B02": "heterogeneous_2d", "B03": "interface_2d"}[case])
        if actual["case_fingerprint"] == prepare_skill_spec(_json(target))["case_fingerprint"]:
            return case
    return None


def load_reference(case, path):
    # Operator-only: caller selects an approved server path, never request data.
    expected = REFERENCES[case]
    p = Path(path)
    if p.is_symlink():
        raise ValueError("reference_symlink")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0)
    with os.fdopen(os.open(p, flags), "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError("reference_not_regular")
        raw = stream.read(128 * 128 * 16 + 4097)
    if len(raw) > 128 * 128 * 16 + 4096 or hashlib.sha256(raw).hexdigest() != expected:
        raise ValueError("reference_sha256_mismatch")
    return _load(raw, RESULT)


def compare(case, actual, reference):
    for field in (actual, reference):
        if (not isinstance(field, np.ndarray) or field.shape != (128, 128)
                or field.dtype not in (np.dtype("complex64"), np.dtype("complex128")) or not np.isfinite(field).all()):
            raise ValueError("full_finite_complex_field_required")
    denominator = float(np.abs(reference).max())
    if denominator <= 0:
        raise ValueError("zero_reference")
    error = float(np.abs(np.abs(actual) - np.abs(reference))[16:112, 16:112].max() / denominator)
    return {"case": case, "relative_magnitude_linf": error, "numerator_pml_crop": 16,
            "denominator": "full_reference_max", "threshold_strict": LIMITS[case],
            "numeric_pass": error < LIMITS[case], "phase_compared": False,
            "actual_peak": float(np.abs(actual).max()), "reference_peak": denominator,
            "grants_skill_trust": False}


def policy(case, runtime_sha):
    if re.fullmatch(r"[0-9a-f]{64}", runtime_sha) is None:
        raise ValueError("invalid_runtime_sha256")
    value = {"profile_id": f"external-helmholtz-{case.lower()}.v1.{runtime_sha}",
             "reference_profile_id": f"external-helmholtz-{case.lower()}.v1", "case": case,
             "reference_sha256": REFERENCES[case], "validator_id": VALIDATOR_VERSION,
             "validator_sha256": source_sha256(), "adapter_id": ADAPTER_VERSION,
             "adapter_sha256": source_sha256(), "runtime_sha256": runtime_sha,
             "library_versions": {"jwave": "0.2.1", "jax": "0.4.38", "jaxlib": "0.4.38",
                                  "jaxdf": "0.2.8", "numpy": "1.26.4"},
             "parameters": _case_parameters(case), "result_spec": copy.deepcopy(RESULT),
             "threshold_strict": LIMITS[case], "denominator": "full_reference_max",
             "numerator_pml_crop": 16, "phase_compared": False}
    value["policy_sha256"] = hashlib.sha256(_json(value).encode()).hexdigest()
    return value
