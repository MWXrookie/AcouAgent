"""Physical identity, controlled construction, and independent scoring boundaries."""
import ast
import copy
import hashlib
import io
import json
from pathlib import Path

import numpy as np
import pytest

from app.skill_general_profiles import (build_controlled_spec, match_profile, compare,
                                       load_reference, policy, REFERENCES)


def example(name="manual-skill.json"):
    return json.loads((Path(__file__).resolve().parents[2] / "docs/skills-next/examples" / name).read_text(encoding="utf-8"))


@pytest.mark.parametrize("name,case", [("manual-skill.json", "B01"), ("b02-manual-skill.json", "B02"), ("b03-manual-skill.json", "B03")])
def test_exact_profile_and_visible_derivation(name, case):
    spec = example(name)
    derived = build_controlled_spec(json.dumps(spec))
    assert match_profile(derived) == case
    assert derived["changed"] and "--- submitted" in derived["diff"]
    assert derived["source_content_sha256"] != derived["derived_content_sha256"]
    assert spec["code_template"].startswith("# Draft")
    assert not build_controlled_spec(json.dumps(derived["spec"]))["changed"]
    ast.parse(derived["code_template"])
    assert 'method="gmres", tol=1e-5' in derived["code_template"]
    for text in ("reference", ".npy", "load(", REFERENCES[case]):
        assert text not in derived["code_template"]


@pytest.mark.parametrize("key,value", [("source_index", [33,32]), ("helmholtz_source_coefficient", 2),
    ("frequency_hz", 200000), ("pml_size", 15), ("sound_speed_m_s", 1600),
    ("background_attenuation_db_mhz2_cm", .1)])
def test_nonfixed_valid_input_builds_but_never_inherits_reference(key,value):
    spec=example(); spec["parameters"][key]=value
    derived=build_controlled_spec(json.dumps(spec))
    assert match_profile(derived) is None
    assert derived["case_fingerprint"] != build_controlled_spec(json.dumps(example()))["case_fingerprint"]
    ast.parse(derived["code_template"])


def test_material_bounds_and_values_used_in_code():
    spec=example("b02-manual-skill.json")
    spec["parameters"]["material_regions"][0].update(start=[51,33],sound_speed_m_s=2200)
    result=build_controlled_spec(json.dumps(spec))
    assert "[51:90, 33:100].set(2200.0)" in result["code_template"]
    assert match_profile(result) is None


def test_original_crop_denominator_phase_and_strict_limit():
    ref=np.ones((128,128),dtype=np.complex128); ref[0,0]=100
    actual=ref.copy(); actual[50,50]=2
    assert compare("B01",actual,ref)["relative_magnitude_linf"] == .01
    assert not compare("B01",actual,ref)["numeric_pass"]
    actual[50,50]=1.5
    assert compare("B01",actual,ref)["numeric_pass"]
    assert compare("B01",ref*1j,ref)["numeric_pass"]
    assert not compare("B01",ref*1j,ref)["phase_compared"]


@pytest.mark.parametrize("bad", [np.zeros((128,128)), np.zeros((96,96),dtype=complex), np.full((128,128),np.nan+0j)])
def test_structural_failures_never_score(bad):
    with pytest.raises(ValueError): compare("B01",bad,np.ones((128,128),dtype=complex))


def test_reference_actual_bytes_must_match_frozen_hash(tmp_path):
    p=tmp_path/"reference.npy"; np.save(p,np.ones((128,128),dtype=complex),allow_pickle=False)
    with pytest.raises(ValueError,match="sha256"): load_reference("B01",p)
    p.write_bytes(b"x"*300000)
    with pytest.raises(ValueError): load_reference("B01",p)


def test_policy_binds_runtime_reference_validator_and_adapter():
    a=policy("B01","a"*64); b=policy("B01","b"*64)
    assert a["policy_sha256"] != b["policy_sha256"]
    assert a["reference_sha256"]==REFERENCES["B01"]
    assert len(a["adapter_sha256"])==len(a["validator_sha256"])==64
    with pytest.raises(ValueError): policy("B01","bad")
