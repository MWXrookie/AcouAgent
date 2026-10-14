import base64
import json

from app.analysis import _analyze_impl, _extract_sensor


def _stdout_with_field_and_sensor() -> str:
    return "\n".join(
        [
            "__ACOU_SENSOR_START__",
            json.dumps({
                "time": [0.0, 1e-6, 2e-6],
                "pressure": [0.0, 0.25, -0.5],
                "sensor_index": [84, 64],
                "sensor_position_m": [0.021, 0.016],
            }),
            "__ACOU_SENSOR_END__",
            "__ACOU_FIELD_START__",
            json.dumps({
                "shape": [2, 2],
                "kind": "field",
                "max_pressure": 0.5,
                "data": [[0.0, 0.2], [0.3, 0.5]],
            }),
            "__ACOU_FIELD_END__",
        ]
    )


def test_sensor_time_series_drives_waveform() -> None:
    stdout = _stdout_with_field_and_sensor()
    sensor = _extract_sensor(stdout)
    assert sensor is not None
    assert sensor["sensor_index"] == [84, 64]

    result = _analyze_impl(stdout, exit_code=0)
    assert result["verdict"] == "normal"
    assert result["waveform_kind"] == "sensor_time"
    assert result["sensor_samples"] == 3
    assert result["sensor_peak_pressure"] == 0.5
    assert base64.b64decode(result["waveform_png_base64"]).startswith(b"\x89PNG")


def test_legacy_sensor_markers_remain_supported() -> None:
    stdout = _stdout_with_field_and_sensor().replace("__ACOU_SENSOR", "__SENSOR_DATA")
    assert _extract_sensor(stdout)["pressure"] == [0.0, 0.25, -0.5]


def test_field_analysis_quality_levels_follow_parsed_evidence() -> None:
    params = json.dumps({
        "sound_speed": 1500, "source_frequency": 500000,
        "domain_N": [128, 128], "domain_dx": 0.00025,
        "pml_size": 10, "source_index": [64, 64], "sensor_index": [84, 64],
    })

    def analyze(data: list[list[float]], shape: list[int]) -> dict:
        payload = {"shape": shape, "downsample": 64, "kind": "field", "data": data}
        stdout = f"__ACOU_FIELD_START__\n{json.dumps(payload)}\n__ACOU_FIELD_END__"
        return _analyze_impl(stdout, exit_code=0, params_json=params)

    normal = analyze([[0.0, 0.2], [0.3, 0.5]], [128, 128])
    assert normal["verdict"] == "normal"
    assert normal["quality_level"] == "Q2"

    mismatched = analyze([[0.0, 0.2], [0.3, 0.5]], [64, 64])
    assert mismatched["verdict"] == "normal"
    assert mismatched["quality_level"] == "Q1"
    assert not next(check for check in mismatched["physics_checks"]
                    if check["check_id"] == "parameter_constraints")["passed"]

    zero = analyze([[0.0, 0.0], [0.0, 0.0]], [128, 128])
    assert zero["verdict"] == "zero_field"
    assert zero["quality_level"] == "Q0"
