from app.execution import _build_report, _timing_report_note
import json


def sensor_stdout(samples=31):
    payload = {"time": [i * 5e-8 for i in range(samples)], "pressure": [0] * samples,
        "timing_metadata": {"version": 1, "time_unit": "s", "medium": "uniform",
            "sound_speed_m_s": 1500, "source_frequency_hz": 500000,
            "source_position_m": [0, 0], "sensor_position_m": [.005, 0],
            "source_start_s": 0, "source_kind": "continuous_sinusoid"}}
    return "__ACOU_SENSOR_START__" + json.dumps(payload) + "__ACOU_SENSOR_END__"


def test_report_short_run_distinguishes_execution_and_physics():
    report = _build_report({"exit_code": 0, "stdout": sensor_stdout()}, "")
    assert "仿真正常完成" in report
    assert "物理验证说明" in report and "早于声波到达" in report
    assert "未认证" in report
    assert '"timing_metadata"' not in report


def test_long_window_is_not_reported_as_steady():
    note = _timing_report_note(sensor_stdout(400))
    assert "尚未检查收敛" in note
    assert "不能确认稳态" in note


def test_report_does_not_echo_caller_diagnostic():
    result = {"exit_code": 0, "stdout": "", "timing_diagnostic": {
        "status": "verified", "reason": "<script>secret token</script>"}}
    report = _build_report(result, "")
    assert "不足以判定" in report
    assert "secret token" not in report
    assert len(_timing_report_note("")) < 200


def test_failure_and_timeout_do_not_claim_timing():
    for result in ({"exit_code": 1}, {"exit_code": 0, "timed_out": True}):
        assert "物理验证说明" not in _build_report(result, "")

def test_oversized_stdout_skips_timing_parser(monkeypatch):
    from app import analysis
    def forbidden(_):
        raise AssertionError("oversized timing parsing")
    monkeypatch.setattr(analysis, "_extract_sensor", forbidden)
    assert "不足以判定" in _timing_report_note("x" * 400001)
