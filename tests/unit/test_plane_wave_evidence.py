import copy
import math

import pytest

from scripts.validation.plane_wave_evidence import EXPECTED_CONFIGURATION, validate_plane_wave


def evidence():
    times = [i * 1e-8 for i in range(2400)]
    raw = {"profile": "val1-plane-wave-v2", "configuration": copy.deepcopy(EXPECTED_CONFIGURATION), "units": {"time": "s", "pressure": "Pa"}, "time": times}
    for key, offset in (("pressure_x1", 48), ("pressure_x2", 96)):
        arrival = offset * 0.00025 / 1500
        sigma = 12 * 0.00025 / 1500
        raw[key] = [0.5*(math.exp(-0.5*((t-arrival)/sigma)**2)
                         + math.exp(-0.5*((t+arrival)/sigma)**2)) for t in times]
    return {"raw_evidence": raw, "status": "FAIL", "theory": {"peak": 999}, "err_pct": {"peak_x1": 999}}


def test_independent_analytic_waveform_and_untrusted_receipt():
    receipt = validate_plane_wave(evidence())
    assert receipt["numeric_pass"]
    assert receipt["trusted_run"] is False
    assert "quality_level" not in receipt


@pytest.mark.parametrize("fault", ["zero", "nan", "inf", "bool", "constant", "negative", "attenuated", "time_reversed", "time_short", "length", "units", "profile"])
def test_forged_pass_summary_cannot_override_raw_failure(fault):
    data = copy.deepcopy(evidence())
    data.update(status="PASS", quality_level="Q4", err_pct={"peak": 0})
    raw = data["raw_evidence"]
    if fault in ("zero", "constant", "negative", "attenuated"):
        raw["pressure_x2"] = [0 if fault == "zero" else 0.5 if fault == "constant" else -v if fault == "negative" else v*0.9 for v in raw["pressure_x2"]]
    elif fault in ("nan", "inf", "bool"):
        raw["pressure_x1"][0] = {"nan": float("nan"), "inf": float("inf"), "bool": True}[fault]
    elif fault == "time_reversed":
        raw["time"].reverse()
    elif fault == "time_short":
        raw["time"] = raw["time"][:100]
        raw["pressure_x1"] = raw["pressure_x1"][:100]
        raw["pressure_x2"] = raw["pressure_x2"][:100]
    elif fault == "length":
        raw["pressure_x2"].pop()
    elif fault == "units":
        raw["units"]["pressure"] = "kPa"
    elif fault == "profile":
        raw["profile"] = "arbitrary-generated-simulation"
    assert not validate_plane_wave(data)["numeric_pass"]


@pytest.mark.parametrize("data", [{}, {"raw_evidence": None}, {"raw_evidence": {}}])
def test_missing_evidence_fails_closed(data):
    assert not validate_plane_wave(data)["numeric_pass"]


@pytest.mark.parametrize("factor, passed", [(1.0099, True), (1.0101, False)])
def test_one_percent_numeric_threshold(factor, passed):
    data = evidence()
    for key in ("pressure_x1", "pressure_x2"):
        data["raw_evidence"][key] = [v*factor for v in data["raw_evidence"][key]]
    assert validate_plane_wave(data)["numeric_pass"] is passed


@pytest.mark.parametrize("change", [{"domain_N": [384, 256]}, {"pml_size": 0}, {"initial_pressure_pa": True}])
def test_new_profile_requires_exact_pinned_configuration(change):
    data = evidence()
    data["raw_evidence"]["configuration"].update(change)
    assert not validate_plane_wave(data)["numeric_pass"]


def test_new_profile_rejects_missing_configuration():
    data = evidence()
    del data["raw_evidence"]["configuration"]
    assert not validate_plane_wave(data)["numeric_pass"]


def test_legacy_failure_remains_reproducible():
    data = evidence()
    data["raw_evidence"]["profile"] = "val1-plane-wave-v1"
    del data["raw_evidence"]["configuration"]
    data["raw_evidence"]["pressure_x2"] = [-0.05]*len(data["raw_evidence"]["time"])
    assert not validate_plane_wave(data)["numeric_pass"]
