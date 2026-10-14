import math
import pytest
from app.physics_timing import assess_timing


def check(**changes):
    values = dict(dt_s=5e-8, sample_count=31, distance_m=.005,
                  sound_speed_m_s=1500, frequency_hz=500000)
    values.update(changes)
    return assess_timing(**values)


def test_reproduced_short_run_is_not_received_steady_state():
    result = check()
    assert result["status"] == "insufficient"
    assert result["reason"] == "ends_before_geometric_arrival"
    assert result["end_time_s"] == pytest.approx(1.5e-6)
    assert result["geometric_arrival_s"] == pytest.approx(.005 / 1500)
    assert result["observed_source_cycles"] == pytest.approx(.75)


def test_long_run_does_not_prove_steady_state_or_trust():
    result = check(sample_count=400)
    assert result["status"] == "unverified"
    assert result["steady_state_verified"] is False
    assert result["trust_verified"] is False


def test_arrival_boundary_and_one_sample():
    assert check(dt_s=1e-6, sample_count=6, distance_m=.006)["reason"] == "less_than_one_period_after_arrival"
    assert check(sample_count=1)["end_time_s"] == 0
    assert check(dt_s=1e-6, sample_count=7, distance_m=.006)["status"] == "unverified"


@pytest.mark.parametrize("field,value", [
    ("dt_s", None), ("dt_s", True), ("dt_s", 0), ("dt_s", math.inf),
    ("frequency_hz", math.nan), ("frequency_hz", -1),
    ("distance_m", -.01), ("sound_speed_m_s", "1500"),
    ("sample_count", 31.0), ("sample_count", True), ("sample_count", 0),
    ("sample_count", 10000001), ("dt_s", 1e308),
])
def test_missing_invalid_and_overflow_are_unknown(field, value):
    assert check(**{field: value})["status"] == "unknown"

from app.physics_timing import assess_sensor_time_axis


def test_actual_axis_preserves_exported_end_time():
    axis = [i * 5e-8 for i in range(31)]
    result = assess_sensor_time_axis(axis)
    assert result["end_time_s"] == axis[-1]
    assert result["sample_count"] == 31
    assert result["status"] == "unknown"
    assert result["steady_state_verified"] is False


@pytest.mark.parametrize("axis", [None, [], [0], [1, 2], [0, 0],
                                  [0, 2, 1], [0, 1, 3], [0, True],
                                  [0, float("nan")], [0, float("inf")]])
def test_bad_axes_are_not_timing_evidence(axis):
    assert assess_sensor_time_axis(axis)["reason"] == "missing_or_invalid_time_axis"


def test_analysis_exposes_unknown_timing_without_changing_verdict(monkeypatch):
    from app import analysis
    monkeypatch.setattr(analysis, "_extract_sensor", lambda _: {"time": [0, 5e-8, 1e-7], "pressure": [0, .1, .2]})
    monkeypatch.setattr(analysis, "_sensor_waveform_base64", lambda _: None)
    result = analysis._analyze_impl("")
    assert result["timing_diagnostic"]["sample_count"] == 3
    assert result["timing_diagnostic"]["status"] == "unknown"
    assert result["verdict"] == "abnormal"

from copy import deepcopy
from app.physics_timing import assess_sensor_timing


def sample_sensor():
    return {"time": [i * 5e-8 for i in range(31)],
            "timing_metadata": {"version": 1, "time_unit": "s", "medium": "uniform",
                "sound_speed_m_s": 1500, "source_frequency_hz": 500000,
                "source_position_m": [.016, .016], "sensor_position_m": [.021, .016],
                "source_start_s": 0, "source_kind": "continuous_sinusoid"}}


def test_optional_metadata_short_run_is_untrusted_insufficient():
    sensor = sample_sensor()
    result = assess_sensor_timing(sensor)
    assert result["status"] == "insufficient"
    assert result["metadata_authenticated"] is False
    assert result["trust_verified"] is False
    assert result["geometric_arrival_s"] == pytest.approx(.005 / 1500)


@pytest.mark.parametrize("field,value", [
    ("version", True), ("version", 2), ("time_unit", "us"), ("medium", "layered"),
    ("source_start_s", 1e-6), ("sound_speed_m_s", True),
    ("source_frequency_hz", float("nan")), ("source_position_m", [0]),
    ("sensor_position_m", [0, 0, 0]), ("sensor_position_m", [0, float("inf")]),
])
def test_metadata_invalid_is_unknown(field, value):
    sensor = sample_sensor()
    sensor["timing_metadata"][field] = value
    assert assess_sensor_timing(sensor)["status"] == "unknown"


def test_metadata_extra_trust_claim_is_rejected():
    sensor = sample_sensor()
    sensor["timing_metadata"]["authenticated"] = True
    assert assess_sensor_timing(sensor)["status"] == "unknown"


def test_sensor_extraction_keeps_optional_metadata():
    import json
    from app.analysis import _extract_sensor
    sensor = sample_sensor()
    sensor["pressure"] = [0] * len(sensor["time"])
    stdout = "__ACOU_SENSOR_START__" + json.dumps(sensor) + "__ACOU_SENSOR_END__"
    assert _extract_sensor(stdout)["timing_metadata"] == sensor["timing_metadata"]

def test_oversized_json_integer_cannot_crash_diagnostic():
    sensor = sample_sensor()
    sensor["timing_metadata"]["sound_speed_m_s"] = 10 ** 1000
    assert assess_sensor_timing(sensor)["status"] == "unknown"
    assert check(dt_s=10 ** 1000)["status"] == "unknown"


def test_v2_post_update_samples_use_actual_end_without_zero_origin_guess():
    sensor=sample_sensor()
    dt=5e-8
    sensor["time"]=[(i+1)*dt for i in range(31)]
    sensor["timing_metadata"].update(version=2,sample_start_s=dt,sample_origin="jwave_0_2_1_after_update")
    result=assess_sensor_timing(sensor)
    assert result["status"]=="insufficient"
    assert result["end_time_s"]==sensor["time"][-1]
    assert result["end_time_s"]==pytest.approx(1.55e-6)
    assert result["metadata_authenticated"] is False
    assert result["trust_verified"] is False

@pytest.mark.parametrize("change",[dict(sample_start_s=0),dict(sample_start_s=True),
                                  dict(sample_origin="other_solver"),dict(sample_start_s=1e-7)])
def test_v2_origin_drift_is_unknown(change):
    sensor=sample_sensor();sensor["time"]=[(i+1)*5e-8 for i in range(31)]
    sensor["timing_metadata"].update(version=2,sample_start_s=5e-8,sample_origin="jwave_0_2_1_after_update")
    sensor["timing_metadata"].update(change)
    assert assess_sensor_timing(sensor)["status"]=="unknown"

def test_late_short_observation_cannot_count_unobserved_source_cycles():
    result=assess_timing(dt_s=1e-9,sample_count=2,distance_m=.005,
                        sound_speed_m_s=1500,frequency_hz=500000,sample_start_s=1)
    assert result["status"]=="insufficient"
    assert result["observed_source_cycles"]<.001
