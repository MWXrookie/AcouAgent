import hashlib
import sqlite3

import pytest

from app import dashboard, analysis, tools, config, execution, llm


@pytest.fixture
def history(tmp_path, monkeypatch):
    monkeypatch.setattr(dashboard, "DB_PATH", str(tmp_path / "history.db"))
    dashboard.init_db()
    return tmp_path / "history.db"


def record(code="print('ok')", run_id="run-source", stdout="ok", stderr="", timed_out=False):
    dashboard.record_execution("run_jwave_code", code, 0, timed_out, 1, stdout, stderr, run_id=run_id)


def test_unique_record_binds_code_and_output_but_not_physics(history):
    record()
    result = dashboard.resolve_execution_source("ok", "", 0)
    assert result["matched"]
    assert result["run_id"] == "run-source"
    assert result["code_sha256"] == hashlib.sha256(b"print('ok')").hexdigest()
    assert not result["parameters_bound"] and not result["physics_validated"]


@pytest.mark.parametrize("stdout, stderr, exit_code, run_id", [
    ("forged", "", 0, "run-source"), ("ok", "forged", 0, "run-source"),
    ("ok", "", 1, "run-source"), ("ok", "", 0, "unknown"),
])
def test_forged_or_missing_execution_fails_closed(history, stdout, stderr, exit_code, run_id):
    record()
    assert not dashboard.resolve_execution_source(stdout, stderr, exit_code, run_id)["matched"]


def test_duplicate_run_and_duplicate_output_are_ambiguous(history):
    record()
    record(code="print('different')")
    assert not dashboard.resolve_execution_source("ok", "", 0, "run-source")["matched"]
    assert dashboard.find_run_id_for_output("ok", 0) is None
    assert not dashboard.resolve_execution_source("ok", "", 0)["matched"]


def test_tampered_code_and_legacy_row_not_promoted(history):
    record()
    with sqlite3.connect(history) as db:
        db.execute("UPDATE executions SET code='tampered'")
    assert not dashboard.resolve_execution_source("ok", "", 0)["matched"]
    with sqlite3.connect(history) as db:
        db.execute("UPDATE executions SET record_version=NULL,code_sha256=NULL")
    dashboard.init_db()
    assert not dashboard.resolve_execution_source("ok", "", 0)["matched"]


def test_unavailable_store_fails_closed(history, monkeypatch):
    monkeypatch.setattr(dashboard, "DB_PATH", "/missing-directory/history.db")
    assert not dashboard.resolve_execution_source("ok", "", 0)["matched"]


class Registry:
    def __init__(self):
        self.functions = {}
    def tool(self, func):
        self.functions[func.__name__] = func
        return func


@pytest.mark.parametrize("final_exit", [0, 1])
def test_retry_records_actual_final_code_without_model_call(history, monkeypatch, final_exit):
    registry = Registry()
    tools.register(registry)
    monkeypatch.setenv("MODEL_BASE_URL", "https://models.example.test/v1")
    monkeypatch.setenv("MODEL_NAME", "test-model")
    monkeypatch.setenv("MODEL_API_KEY", "test-placeholder")
    results = iter([dict(exit_code=1, timed_out=False, stdout="bad", stderr="error"),
                    dict(exit_code=final_exit, timed_out=False, stdout="ok", stderr="")])
    monkeypatch.setattr(execution, "_execute_code", lambda *_: next(results))
    monkeypatch.setattr(llm, "_llm_fix_code", lambda *a, **kw: "print('fixed')")
    result = registry.functions["run_jwave_code_with_retry"]("print('original')", max_retries=1)
    row = dashboard.get_executions()[0]
    assert row["code"] == result["final_code"] == "print('fixed')"
    assert dashboard.resolve_execution_source("ok", "", final_exit, result["run_id"])["matched"]


def test_analysis_event_keeps_resolved_run_id_and_timeout(history):
    registry = Registry()
    analysis.register(registry)
    stdout = '__ACOU_FIELD_START__\n{"shape":[2,2],"data":[[0,1],[0,1]]}\n__ACOU_FIELD_END__'
    record(stdout=stdout, timed_out=True)
    result = registry.functions["analyze_simulation_result"](stdout)
    assert result["run_id"] == "run-source"
    assert result["quality_level"] == "Q0"
    assert dashboard.get_analysis_events()[0]["run_id"] == "run-source"


def test_latest_analysis_summary_is_bounded_and_display_safe(history):
    dashboard.record_analysis_event(
        0, "normal", 1.25, 0.4, [64, 64], True, "first", None, None, "", "",
        run_id="run-source",
    )
    dashboard.record_analysis_event(
        0, "normal", 1.5, 0.5, [32, 32], True, "latest", None, None, "", "",
        run_id="run-source",
    )
    got = dashboard.get_latest_analysis_by_run_ids(["run-source", "run-source"])
    assert got["run-source"] == {
        "run_id": "run-source",
        "verdict": "normal",
        "max_pressure": 1.5,
        "rms_pressure": 0.5,
        "field_shape": [32, 32],
        "has_signal": True,
        "summary": "latest",
    }
    updates = dashboard.get_analysis_updates(since_id=1)
    assert updates == [{
        "run_id": "run-source",
        "verdict": "normal",
        "max_pressure": 1.5,
        "rms_pressure": 0.5,
        "field_shape": [32, 32],
        "has_signal": True,
        "summary": "latest",
        "analysis_id": 2,
    }]


def test_nonfinite_analysis_metrics_are_json_safe(history):
    dashboard.record_analysis_event(
        0, "abnormal", float("inf"), float("nan"), [32, 32], False,
        "diverged", None, None, "", "", run_id="run-diverged",
    )

    latest = dashboard.get_latest_analysis_by_run_ids(["run-diverged"])["run-diverged"]
    update = dashboard.get_analysis_updates()[0]
    assert latest["max_pressure"] is None and latest["rms_pressure"] is None
    assert update["max_pressure"] is None and update["rms_pressure"] is None


def test_execution_code_limit_counts_utf8_bytes(history):
    registry = Registry()
    tools.register(registry)
    with pytest.raises(ValueError, match="20000 UTF-8 bytes"):
        registry.functions["run_jwave_code"]("#" + "声" * 7_000)


def test_forged_analysis_does_not_attach_to_requested_execution(history):
    registry = Registry()
    analysis.register(registry)
    record()
    stdout = '__ACOU_FIELD_START__\n{"shape":[2,2],"data":[[0,2],[0,2]]}\n__ACOU_FIELD_END__'
    result = registry.functions["analyze_simulation_result"](stdout, run_id="run-source")
    assert result["execution_source"]["matched"] is False
    assert result["run_id"] is None
    assert dashboard.get_analysis_events()[0]["run_id"] != "run-source"


def test_implicit_output_match_also_rejects_duplicate_run_id(history):
    record(stdout="first")
    record(stdout="second")
    assert not dashboard.resolve_execution_source("first", "", 0)["matched"]
