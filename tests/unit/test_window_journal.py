import json
import pytest
from scripts.validation.window_journal import WindowJournal


def test_execution_failure_retains_window_before_assertion(tmp_path):
    journal = WindowJournal(tmp_path)
    snapshots = iter([{"id": "before"}, {"id": "after", "oom": False}])
    def failure():
        assert (tmp_path / "command.json").exists()
        assert (tmp_path / "before.json").exists()
        raise RuntimeError("not persisted as potentially sensitive text")
    with pytest.raises(RuntimeError):
        journal.capture({"exact": "command"}, lambda: next(snapshots), failure)
    assert json.loads((tmp_path / "after.json").read_text())["id"] == "after"
    attempt = json.loads((tmp_path / "attempt.json").read_text())
    assert attempt["error_type"] == "RuntimeError" and not attempt["trusted_run"]
    assert "potentially sensitive" not in (tmp_path / "attempt.json").read_text()


def test_nonzero_result_retained_before_caller_assertion(tmp_path):
    journal = WindowJournal(tmp_path)
    result = journal.capture({}, lambda: {"id": "fixed"}, lambda: {"exit_code": 1})
    assert result["exit_code"] == 1
    assert json.loads((tmp_path / "result.json").read_text())["exit_code"] == 1
    assert (tmp_path / "after.json").exists()


def test_missing_after_cannot_report_completed_window(tmp_path):
    journal = WindowJournal(tmp_path)
    count = 0
    def observe():
        nonlocal count
        count += 1
        if count == 2: raise OSError("missing")
        return {}
    with pytest.raises(ValueError, match="after_observation_unavailable"):
        journal.capture({}, observe, lambda: {})
    assert json.loads((tmp_path / "attempt.json").read_text())["after_error_type"] == "OSError"


def test_before_failure_prevents_execution(tmp_path):
    journal = WindowJournal(tmp_path)
    def observe(): raise OSError()
    def execute(): pytest.fail("must not run")
    with pytest.raises(OSError): journal.capture({}, observe, execute)
    assert json.loads((tmp_path / "attempt.json").read_text())["stage"] == "before"


@pytest.mark.parametrize("data", [{"invalid": float("nan")}, {"large": "x" * 2000001}])
def test_invalid_write_preserves_existing_file(tmp_path, data):
    journal = WindowJournal(tmp_path)
    journal.write("result", {"original": True})
    with pytest.raises(ValueError): journal.write("result", data)
    assert json.loads((tmp_path / "result.json").read_text()) == {"original": True}


def test_invalid_evidence_name_rejected(tmp_path):
    with pytest.raises(ValueError): WindowJournal(tmp_path).write("../outside", {})
