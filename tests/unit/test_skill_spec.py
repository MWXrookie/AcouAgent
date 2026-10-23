"""Authoring identity and hostile-input properties; no solver or trusted intake."""
import copy
import hashlib
import json
from pathlib import Path

import pytest

from app.skill_spec import prepare_skill_spec, parse_skill_spec
from app.skill_store import normalize_parameters, skill_fingerprint

EXAMPLES = Path(__file__).resolve().parents[2] / "docs/skills-next/examples"


def sample(name="manual-skill.json"):
    return json.loads((EXAMPLES / name).read_text(encoding="utf-8"))


def prepare(spec):
    return prepare_skill_spec(json.dumps(spec, ensure_ascii=False, allow_nan=False))


@pytest.mark.parametrize("name", ["manual-skill.json", "b02-manual-skill.json", "b03-manual-skill.json"])
def test_physical_numeric_spellings_have_same_identity(name):
    spec = sample(name)
    changed = copy.deepcopy(spec)
    for key in ("sound_speed_m_s", "density_kg_m3", "helmholtz_source_coefficient", "background_attenuation_db_mhz2_cm"):
        changed["parameters"][key] = float(changed["parameters"][key])
    for region in changed["parameters"]["material_regions"]:
        for key in ("sound_speed_m_s", "density_kg_m3"):
            if key in region:
                region[key] = float(region[key])
    a, b = prepare(spec), prepare(changed)
    assert a["case_fingerprint"] == b["case_fingerprint"]
    assert a["content_sha256"] == b["content_sha256"]
    assert a["normalized_parameters"] == b["normalized_parameters"]


def test_physics_code_and_editorial_identity_are_separate():
    spec = sample()
    baseline = prepare(spec)
    for key, value in (("name", "改标题"), ("code_template", "raise RuntimeError('another draft')\n")):
        changed = copy.deepcopy(spec)
        changed[key] = value
        result = prepare(changed)
        assert result["case_fingerprint"] == baseline["case_fingerprint"]
        assert result["content_sha256"] != baseline["content_sha256"]
        assert (result["code_sha256"] != baseline["code_sha256"]) == (key == "code_template")
    for key, value in (("source_index", [33, 32]), ("helmholtz_source_coefficient", 2)):
        changed = copy.deepcopy(spec)
        changed["parameters"][key] = value
        assert prepare(changed)["case_fingerprint"] != baseline["case_fingerprint"]


def test_region_geometry_changes_physical_identity():
    spec = sample("b02-manual-skill.json")
    changed = copy.deepcopy(spec)
    changed["parameters"]["material_regions"][0]["start"][0] += 1
    assert prepare(spec)["case_fingerprint"] != prepare(changed)["case_fingerprint"]


@pytest.mark.parametrize("key,value", [("source_index", [True, 32]), ("source_index", [32.0, 32]),
    ("source_index", [15, 32]), ("source_index", [112, 32]), ("grid_shape", [128.0, 128]),
    ("pml_size", True), ("helmholtz_source_coefficient", 0), ("boundary_condition", "rigid")])
def test_invalid_or_ambiguous_source_contract_rejected(key, value):
    spec = sample()
    spec["parameters"][key] = value
    with pytest.raises(ValueError):
        prepare(spec)


@pytest.mark.parametrize("fault", ["outside", "empty", "overlap", "invalid_halfspace"])
def test_regions_cannot_hide_ambiguous_material_maps(fault):
    spec = sample("b02-manual-skill.json")
    region = spec["parameters"]["material_regions"][0]
    if fault == "outside":
        region["stop"][0] = 129
    elif fault == "empty":
        region["stop"] = region["start"].copy()
    elif fault == "overlap":
        spec["parameters"]["material_regions"].append(copy.deepcopy(region))
    else:
        region["geometry"] = "half_space"
    with pytest.raises(ValueError):
        prepare(spec)


@pytest.mark.parametrize("fault", ["shape", "axis", "downsample", "units", "protected"])
def test_result_and_trust_declarations_cannot_override_contract(fault):
    spec = sample()
    if fault == "protected":
        spec["quality_level"] = "Q4"
    else:
        key, value = {"shape": ("grid_shape", [64, 64]), "axis": ("axis_order", ["y", "x"]),
                      "downsample": ("downsample", 2), "units": ("units", "Pa")}[fault]
        spec["result_spec"][key] = value
    with pytest.raises(ValueError):
        prepare(spec)


@pytest.mark.parametrize("raw", ['{"name":"one","name":"two"}', '{"x":NaN}', '{"x":1e999}',
    '[' * 18 + '0' + ']' * 18, b'\xff', ' ' * 64001],
    ids=["duplicate-key", "nan", "overflow", "deep", "invalid-utf8", "byte-budget"])
def test_raw_parser_rejects_unsafe_json(raw):
    with pytest.raises(ValueError):
        parse_skill_spec(raw)


def test_code_budget_counts_utf8_bytes():
    spec = sample()
    spec["code_template"] = "#" + "中" * 7000 + "\nraise RuntimeError('draft')\n"
    assert len(spec["code_template"]) < 20000
    with pytest.raises(ValueError):
        prepare(spec)


def test_new_authoring_does_not_change_old_seventeen_fingerprints():
    from app import skill_local_catalog as catalog
    baseline = []
    for scope in catalog.SCOPES:
        entry = catalog.specification(scope)
        parameters = normalize_parameters(entry["parameters"])
        payload = json.dumps({"scenario_type": entry["scene"], "parameters": parameters},
                             sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
        expected = hashlib.sha256(payload.encode()).hexdigest()
        baseline.append((entry["scene"], parameters, expected))
        assert skill_fingerprint(entry["scene"], parameters) == expected
    assert len(baseline) == 17
    for name in ("manual-skill.json", "b02-manual-skill.json", "b03-manual-skill.json"):
        prepare(sample(name))
    for scene, parameters, digest in baseline:
        assert skill_fingerprint(scene, parameters) == digest
