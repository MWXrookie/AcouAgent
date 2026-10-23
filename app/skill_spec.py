"""Bounded, untrusted SkillSpec ingestion; this module never executes or grades code.

The explicit validator implements the supported subset of the frozen v1 authoring
schema. Schema validity alone does not establish physical correctness. Legacy
skill_store normalization and fingerprints deliberately remain unchanged.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Any

from app.skill_store import SCENARIO_TYPES, normalize_parameters, validate_candidate_content

NORMALIZER_VERSION = "normalizer_v2"
FINGERPRINT_VERSION = "case_fingerprint_v2"
MAX_SPEC_BYTES = 64000
MAX_PARAMETERS_BYTES = 4096
MAX_CODE_BYTES = 20000
MAX_DEPTH = 16


class SkillSpecError(ValueError):
    """Public errors expose a stable code, never submitted content or host paths."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _fail(code: str = "invalid_spec") -> None:
    raise SkillSpecError(code)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _object(value: Any, required: set[str], optional: set[str] | None = None) -> None:
    if type(value) is not dict or not required <= value.keys() or value.keys() - required - (optional or set()):
        _fail("invalid_structure")


def _text(value: Any, maximum: int, nonempty: bool = True) -> None:
    if type(value) is not str or len(value) > maximum or (nonempty and not value.strip()):
        _fail("invalid_string")


def _integer(value: Any, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        _fail("invalid_integer")
    return value


def _number(value: Any, *, positive: bool = False, nonnegative: bool = False) -> float:
    if type(value) not in (int, float):
        _fail("invalid_number")
    try:
        result = float(value)
    except (OverflowError, ValueError):
        _fail("invalid_number")
    if not math.isfinite(result) or (positive and result <= 0) or (nonnegative and result < 0):
        _fail("invalid_number")
    return 0.0 if result == 0 else result


def _indices(value: Any, shape: list[int], *, stop: bool = False) -> list[int]:
    if type(value) is not list or len(value) != len(shape):
        _fail("invalid_dimensions")
    return [_integer(item, 0, size if stop else size - 1) for item, size in zip(value, shape)]


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            _fail("duplicate_key")
        result[key] = value
    return result


def _constant(_value: str) -> None:
    _fail("nonfinite_number")


def _decode(raw: bytes | str) -> dict[str, Any]:
    if type(raw) not in (bytes, str):
        _fail("invalid_input_type")
    try:
        if len(raw) > MAX_SPEC_BYTES:
            _fail("spec_too_large")
        source = raw.decode("utf-8", errors="strict") if type(raw) is bytes else raw
        if len(source.encode("utf-8")) > MAX_SPEC_BYTES:
            _fail("spec_too_large")
        # Bound parser nesting before allocating the JSON object. Braces inside
        # quoted strings, including escaped quotes, do not count as containers.
        depth = 0
        quoted = escaped = False
        for char in source:
            if quoted:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    quoted = False
            elif char == '"':
                quoted = True
            elif char in "[{":
                depth += 1
                if depth > MAX_DEPTH:
                    _fail("nesting_too_deep")
            elif char in "]}":
                depth -= 1
                if depth < 0:
                    _fail("invalid_json")
        value = json.loads(source, object_pairs_hook=_pairs, parse_constant=_constant)

        def check(item: Any) -> None:
            if type(item) is float and not math.isfinite(item):
                _fail("nonfinite_number")
            if type(item) is str:
                item.encode("utf-8", errors="strict")
            elif type(item) is dict:
                for key, child in item.items():
                    check(key)
                    check(child)
            elif type(item) is list:
                for child in item:
                    check(child)
        check(value)
        if type(value) is not dict:
            _fail("invalid_structure")
        return value
    except SkillSpecError:
        raise
    except (UnicodeError, ValueError, RecursionError, OverflowError):
        raise SkillSpecError("invalid_json") from None


def _parameters(value: Any) -> dict[str, Any]:
    required = {
        "dimensions", "solver_mode", "medium_type", "sound_speed_m_s", "density_kg_m3",
        "grid_shape", "dx_m", "pml_size", "frequency_hz", "source_index", "source_type",
        "source_geometry", "boundary_condition", "helmholtz_source_coefficient",
        "background_attenuation_db_mhz2_cm", "material_regions",
    }
    optional = {"sensor_index", "sensor_indices"}
    # Unsupported time/modal/interface shorthand fields are deliberately rejected:
    # the first result contract cannot represent those simulations unambiguously.
    _object(value, required, optional)
    if len(_json(value).encode("utf-8")) > MAX_PARAMETERS_BYTES:
        _fail("parameters_too_large")
    if (type(value["dimensions"]) is not int or value["dimensions"] != 2
            or value["solver_mode"] != "frequency_domain"
            or value["source_type"] != "continuous_sinusoid"
            or value["source_geometry"] != "point"
            or value["boundary_condition"] != "absorbing_pml"):
        _fail("unsupported_contract")
    if value["medium_type"] not in ("water", "homogeneous_water", "homogeneous", "heterogeneous", "tissue", "air"):
        _fail("invalid_medium")
    shape = value["grid_shape"]
    if type(shape) is not list or len(shape) != 2:
        _fail("invalid_dimensions")
    shape = [_integer(item, 32, 1024) for item in shape]
    spacing = value["dx_m"]
    if type(spacing) is list:
        if len(spacing) != 2:
            _fail("invalid_dimensions")
        spacing = [_number(item, positive=True) for item in spacing]
    else:
        spacing = [_number(spacing, positive=True)] * 2
    # Use the unchanged legacy normalizer for fields already in its vocabulary.
    extensions = {"material_regions", "helmholtz_source_coefficient", "background_attenuation_db_mhz2_cm"}
    legacy = {key: item for key, item in value.items() if key not in extensions}
    try:
        normalized = normalize_parameters(legacy)
    except (ValueError, TypeError, OverflowError):
        raise SkillSpecError("invalid_parameters") from None
    normalized.update(grid_shape=shape, dx_m=spacing)
    for key in ("sound_speed_m_s", "density_kg_m3", "frequency_hz"):
        normalized[key] = _number(value[key], positive=True)
    pml = _integer(value["pml_size"], 1, 511)
    if any(2 * pml >= size for size in shape):
        _fail("invalid_pml")
    normalized["pml_size"] = pml
    for key in ("source_index", "sensor_index"):
        if key in value:
            indices = _indices(value[key], shape)
            if any(not pml <= index < size - pml for index, size in zip(indices, shape)):
                _fail("index_in_pml")
            normalized[key] = indices
    if "sensor_indices" in value:
        sensors = value["sensor_indices"]
        if type(sensors) is not list or not 1 <= len(sensors) <= 3:
            _fail("invalid_sensors")
        normalized["sensor_indices"] = [_indices(item, shape) for item in sensors]
        if any(not pml <= index < size - pml for point in normalized["sensor_indices"] for index, size in zip(point, shape)):
            _fail("index_in_pml")
    coefficient = _number(value["helmholtz_source_coefficient"])
    if coefficient == 0:
        _fail("zero_source")
    normalized["helmholtz_source_coefficient"] = coefficient
    normalized["background_attenuation_db_mhz2_cm"] = _number(value["background_attenuation_db_mhz2_cm"], nonnegative=True)
    regions = value["material_regions"]
    if type(regions) is not list or len(regions) > 8:
        _fail("invalid_regions")
    clean = []
    for region in regions:
        _object(region, {"geometry", "start", "stop"}, {"sound_speed_m_s", "density_kg_m3"})
        if region["geometry"] not in ("rectangle", "half_space") or not ({"sound_speed_m_s", "density_kg_m3"} & region.keys()):
            _fail("invalid_region")
        start = _indices(region["start"], shape)
        stop = _indices(region["stop"], shape, stop=True)
        if any(left >= right for left, right in zip(start, stop)):
            _fail("invalid_region_bounds")
        if region["geometry"] == "half_space":
            partial = [axis for axis, size in enumerate(shape) if start[axis] != 0 or stop[axis] != size]
            if len(partial) != 1 or not (start[partial[0]] == 0 or stop[partial[0]] == shape[partial[0]]):
                _fail("invalid_half_space")
        item = {"geometry": region["geometry"], "start": start, "stop": stop}
        for key in ("sound_speed_m_s", "density_kg_m3"):
            if key in region:
                item[key] = _number(region[key], positive=True)
        for other in clean:
            if all(max(start[axis], other["start"][axis]) < min(stop[axis], other["stop"][axis]) for axis in range(2)):
                _fail("overlapping_regions")
        clean.append(item)
    normalized["material_regions"] = sorted(clean, key=_json)
    minimum_speed = min([normalized["sound_speed_m_s"]] + [item.get("sound_speed_m_s", normalized["sound_speed_m_s"]) for item in clean])
    if normalized["frequency_hz"] > minimum_speed / (4 * max(spacing)):
        _fail("insufficient_spatial_resolution")
    if len(_json(normalized).encode("utf-8")) > MAX_PARAMETERS_BYTES:
        _fail("parameters_too_large")
    return normalized


def parse_skill_spec(raw: bytes | str) -> dict[str, Any]:
    """Parse and normalize a draft. Passing validation grants no scientific trust."""
    spec = _decode(raw)
    _object(spec, {"schema_version", "name", "description", "scenario_type", "parameters", "code_template", "validation_request", "result_spec"}, {"author_notes"})
    if spec["schema_version"] != "skill-spec.v1":
        _fail("unsupported_schema")
    _text(spec["name"], 120)
    _text(spec["description"], 2048)
    if "author_notes" in spec:
        _text(spec["author_notes"], 2048, nonempty=False)
    if type(spec["scenario_type"]) is not str or spec["scenario_type"] not in SCENARIO_TYPES:
        _fail("invalid_scenario")
    if spec["scenario_type"] in {"point_source_3d", "plane_wave_2d", "initial_pressure_2d"}:
        _fail("unsupported_contract")
    _text(spec["code_template"], MAX_CODE_BYTES)
    if len(spec["code_template"].encode("utf-8")) > MAX_CODE_BYTES:
        _fail("code_too_large")
    request = spec["validation_request"]
    _object(request, {"policy"}, {"reference_case_id"})
    if request["policy"] != "server_required":
        _fail("invalid_validation_policy")
    if "reference_case_id" in request:
        identifier = request["reference_case_id"]
        if type(identifier) is not str or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", identifier) is None:
            _fail("invalid_reference_claim")
    result = spec["result_spec"]
    _object(result, {"kind", "units", "axis_order", "grid_shape", "full_complex", "downsample"})
    if (result["kind"] != "steady_complex_pressure" or result["units"] != "numerical_source_normalization"
            or result["axis_order"] != ["x", "y"] or result["full_complex"] is not True
            or type(result["downsample"]) is not int or result["downsample"] != 1):
        _fail("invalid_result_contract")
    shape = result["grid_shape"]
    if type(shape) is not list or len(shape) != 2:
        _fail("invalid_result_shape")
    for item in shape:
        _integer(item, 32, 1024)
    spec["parameters"] = _parameters(spec["parameters"])
    if shape != spec["parameters"]["grid_shape"]:
        _fail("result_shape_mismatch")
    try:
        validate_candidate_content(spec["code_template"], {key: item for key, item in spec.items() if key != "code_template"})
    except (ValueError, TypeError, RecursionError, OverflowError):
        raise SkillSpecError("invalid_candidate_content") from None
    return spec


def prepare_skill_spec(raw: bytes | str) -> dict[str, Any]:
    """Return an untrusted draft and separate physical/content/code identities."""
    spec = parse_skill_spec(raw)
    physical = {
        "schema_version": spec["schema_version"], "normalizer_version": NORMALIZER_VERSION,
        "fingerprint_version": FINGERPRINT_VERSION, "scenario_type": spec["scenario_type"],
        "parameters": spec["parameters"], "result_spec": spec["result_spec"],
    }
    def digest(value: Any) -> str:
        return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()
    return {
        "spec": spec, "normalized_parameters": spec["parameters"],
        "case_fingerprint": digest(physical),
        "code_sha256": hashlib.sha256(spec["code_template"].encode("utf-8")).hexdigest(),
        "content_sha256": digest(spec), "normalizer_version": NORMALIZER_VERSION,
        "fingerprint_version": FINGERPRINT_VERSION,
    }
