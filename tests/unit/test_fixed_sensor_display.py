"""Sensor-only fixed evidence must not be rejected as, or disguised as, a field."""
import copy
import json

import pytest

from app import analysis
from tests.unit.test_plane_wave_evidence import evidence
from tests.unit.test_interface_waveform import raw
from scripts.validation import interface_waveform


def document(case):
    if case == "plane":
        return evidence()
    value = raw()
    data = value["raw_evidence"]
    data["profile"] = interface_waveform.WIDE_PROFILE
    data["configuration"] = copy.deepcopy(interface_waveform.WIDE_CONFIGURATION)
    data["pressure_left"], data["pressure_right"] = interface_waveform.analytic_traces(
        data["time"], data["configuration"])
    return value


@pytest.mark.parametrize("case", ["plane", "interface"])
def test_numeric_sensor_result_has_real_waveform_and_no_spatial_claim(case):
    value = document(case)
    before = copy.deepcopy(value)
    result = analysis._fixed_sensor_result(json.dumps(value), "", 0, {"run_id": "actual-run"})
    assert result["verdict"] == "normal" and result["quality_level"] == "Q2"
    assert result["field_shape"] == [] and result["heatmap_png_base64"] is None
    assert result["result_kind"] == result["waveform_kind"] == "sensor_time"
    assert result["run_id"] == "actual-run" and result["units"] == "Pa"
    assert result["max_pressure"] > result["rms_pressure"] > 0
    assert result["waveform_png_base64"].startswith("iVBOR")
    assert "不是二维压力场" in result["summary"] and value == before


@pytest.mark.parametrize("case", ["plane", "interface"])
@pytest.mark.parametrize("fault", ["zero", "nonfinite", "wrong_units", "exit", "stderr", "timeout"])
def test_invalid_trace_cannot_hide_behind_a_pass_claim(case, fault):
    value = document(case)
    value["quality_level"] = "Q4"
    value["numeric_pass"] = True
    data = value["raw_evidence"]
    key = "pressure_x2" if case == "plane" else "pressure_right"
    if fault == "zero": data[key] = [0.] * len(data[key])
    if fault == "nonfinite": data[key][0] = float("nan")
    if fault == "wrong_units": data["units"]["pressure"] = "kPa"
    result = analysis._fixed_sensor_result(json.dumps(value), "error" if fault == "stderr" else "",
        1 if fault == "exit" else 0, {"timed_out": fault == "timeout"})
    assert result is None
