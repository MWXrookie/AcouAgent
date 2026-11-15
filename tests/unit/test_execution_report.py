import json

from app.execution import _build_report, _normalize_result_artifacts


def test_warning_only_stderr_is_not_labeled_as_error() -> None:
    result = {
        "exit_code": 0,
        "timed_out": False,
        "duration_ms": 10,
        "total_attempts": 1,
        "stdout": "ok",
        "stderr": "/tmp/main.py:41: UserWarning: Glyph 31283 missing from font(s) DejaVu Sans.\n  plt.tight_layout()",
    }
    report = _build_report(result, "print('ok')")
    assert "运行警告（不影响本次执行）" in report
    assert "### ❌ 错误输出" not in report


def test_real_stderr_remains_an_error() -> None:
    result = {
        "exit_code": 1,
        "timed_out": False,
        "duration_ms": 10,
        "total_attempts": 1,
        "stdout": "",
        "stderr": "Traceback (most recent call last):\nValueError: boom",
    }
    assert "### ❌ 错误输出" in _build_report(result, "raise ValueError")


def test_sensor_json_is_hidden_and_canonical_heatmap_wins() -> None:
    stdout = "\n".join([
        "__ACOU_SENSOR_START__",
        json.dumps({"time": [0, 1], "pressure": [0, 1]}),
        "__ACOU_SENSOR_END__",
        "__ACOU_FIELD_START__",
        json.dumps({"shape": [2, 2], "kind": "field", "max_pressure": 1, "data": [[0, 1], [0.5, 0.2]]}),
        "__ACOU_FIELD_END__",
    ])
    result = {"exit_code": 0, "timed_out": False, "stdout": stdout, "stderr": "", "image_base64": "bad"}
    _normalize_result_artifacts(result)
    assert result["image_source"] == "canonical_max_abs_field"
    assert result["image_base64"] != "bad"

    result.update(duration_ms=10, total_attempts=1)
    report = _build_report(result, "print('ok')")
    assert "传感器时域数据已用于曲线生成" in report
    assert '"pressure": [0, 1]' not in report

def test_confirmed_time_and_frequency_reports_describe_available_contract():
    base=dict(exit_code=0,timed_out=False,duration_ms=1,total_attempts=1,stdout='',stderr='')
    time=_build_report(dict(base,display_kind='confirmed_time_field'),'pass')
    frequency=_build_report(dict(base,display_kind='frequency_field'),'pass')
    assert '是否通过验证及其等级' in time and '是否通过验证及其等级' in frequency
    assert '时间轴/参数不足' not in time and '时间轴/参数不足' not in frequency
    assert '频域求解' in frequency and '已确认' in time
