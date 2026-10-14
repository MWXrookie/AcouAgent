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
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "test-placeholder")
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
