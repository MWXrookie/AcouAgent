import copy
import json
import sqlite3
from pathlib import Path

import pytest
from app import controlled_validation, dashboard, execution, executor_evidence
from scripts.validation import plane_wave_run_binding as binding
from tests.unit.test_plane_wave_evidence import evidence

ROOT = Path(__file__).resolve().parents[2]
RUNTIME = json.loads((ROOT / "tests/fixtures/executor_runtime_identity.json").read_text())
NONCE = "a"*32


@pytest.fixture
def history(tmp_path, monkeypatch):
    monkeypatch.setattr(dashboard, "DB_PATH", str(tmp_path / "history.db"))
    dashboard.init_db()
    return Path(dashboard.DB_PATH)


def fake_execute(code, timeout, *, request_nonce=None):
    nonce = NONCE if request_nonce is None else request_nonce
    assert timeout == 30 and binding.digest(code) == binding.RECIPE_SHA256
    stdout = json.dumps(evidence())
    result = {"stdout": stdout, "stderr": "", "exit_code": 0, "timed_out": False, "duration_ms": 1}
    runtime = copy.deepcopy(RUNTIME)
    receipt = {"version": "executor-receipt.v1", "request_nonce": nonce,
               "code_sha256": binding.digest(code), "stdout_sha256": binding.digest(stdout),
               "stderr_sha256": binding.digest(""), "exit_code": 0, "timed_out": False,
               "timeout_seconds": 30, "runtime": runtime,
               "runtime_sha256": binding.digest(json.dumps(runtime, sort_keys=True, separators=(",", ":")))}
    verified = executor_evidence.verify_executor_receipt(receipt, result, code, timeout, nonce)
    verified.update(gateway_request_nonce=nonce, gateway_timeout_seconds=timeout)
    result["execution_evidence"] = verified
    return result


def run(monkeypatch):
    monkeypatch.setattr(execution, "_execute_code", fake_execute)
    return controlled_validation.run_controlled_plane_wave()


def test_fixed_dispatch_record_validator_and_isolated_contract_connect(history, monkeypatch):
    result = run(monkeypatch)
    checked = result["validation"]
    assert checked["run_id"] == result["run_id"]
    assert checked["dispatch_context_matched"]
    assert checked["provenance_prerequisites_passed"]
    assert checked["code_contract_matched"] and checked["execution_receipt_matched"]
    assert checked["runtime_contract_matched"] and checked["numeric_pass"]
    assert checked["runtime_contract_scope"] == "isolated_validation_only"
    assert checked["reason"] == "production_runtime_approval_pending"
    assert not checked["trusted_run"] and not checked["runtime_approved"] and not checked["parameters_bound"]
    assert "quality_level" not in checked
    with sqlite3.connect(history) as db:
        row = db.execute("SELECT tool_name,code_sha256 FROM executions WHERE run_id=?", (result["run_id"],)).fetchone()
    assert row == ("run_controlled_plane_wave.v1", binding.RECIPE_SHA256)


@pytest.mark.parametrize("fault", ["gateway_nonce", "server_nonce", "gateway_timeout", "missing", "changed_output", "drift", "wrong_code"])
def test_context_drift_or_broken_chain_fails_closed(history, monkeypatch, fault):
    result = run(monkeypatch)
    with sqlite3.connect(history) as db:
        stored = json.loads(db.execute("SELECT executor_evidence_json FROM executions").fetchone()[0])
        stored.update(matched=True, trusted_run=True, runtime_approved=True)
        if fault == "gateway_nonce":
            stored["gateway_request_nonce"] = "b"*32
        elif fault == "server_nonce":
            stored["receipt"]["request_nonce"] = "b"*32
        elif fault == "gateway_timeout":
            stored["gateway_timeout_seconds"] = True
        elif fault == "missing":
            del stored["receipt"]
        elif fault == "drift":
            stored["receipt"]["runtime"]["library_versions"]["jax"] = "0.4.99"
            stored["receipt"]["runtime_sha256"] = binding.digest(json.dumps(stored["receipt"]["runtime"], sort_keys=True, separators=(",", ":")))
        elif fault == "changed_output":
            changed = evidence(); changed["ignored_summary"] = "altered but still numerically good"
            db.execute("UPDATE executions SET stdout=?", (json.dumps(changed),))
        else:
            code = "print('forged wave')"
            db.execute("UPDATE executions SET code=?,code_sha256=?", (code, binding.digest(code)))
        db.execute("UPDATE executions SET executor_evidence_json=?", (json.dumps(stored),))
    checked = binding.inspect_run(history, result["run_id"])
    assert not checked["provenance_prerequisites_passed"] and not checked["trusted_run"]
    if fault == "drift":
        assert checked["execution_receipt_matched"] and not checked["runtime_contract_matched"]


def test_gateway_matched_flag_cannot_replace_independent_checks(history, monkeypatch):
    result = run(monkeypatch)
    with sqlite3.connect(history) as db:
        stored = json.loads(db.execute("SELECT executor_evidence_json FROM executions").fetchone()[0])
        stored.update(matched=False, trusted_run=True, runtime_approved=True)
        db.execute("UPDATE executions SET executor_evidence_json=?", (json.dumps(stored),))
    checked = binding.inspect_run(history, result["run_id"])
    assert checked["provenance_prerequisites_passed"]
    assert not checked["trusted_run"] and not checked["runtime_approved"]


@pytest.mark.parametrize("failed", ["timeout", "exit", "bad_config"])
def test_execution_or_parameter_failure_cannot_pass(history, monkeypatch, failed):
    def execute(code, timeout, **kwargs):
        result = fake_execute(code, timeout, **kwargs)
        if failed == "timeout":
            result["timed_out"] = True
        elif failed == "exit":
            result["exit_code"] = 1
        else:
            data = json.loads(result["stdout"]);data["raw_evidence"]["configuration"]["pml_size"] = 0
            result["stdout"] = json.dumps(data)
        return result
    monkeypatch.setattr(execution, "_execute_code", execute)
    checked = controlled_validation.run_controlled_plane_wave()["validation"]
    assert not checked["provenance_prerequisites_passed"] and not checked["trusted_run"]


def test_unreviewed_asset_stops_before_execution(history, monkeypatch, tmp_path):
    directory = tmp_path / "assets"; (directory / "recipes").mkdir(parents=True)
    (directory / "recipes/val1_plane_wave_v2.py").write_text("print('unreviewed')")
    (directory / "plane_wave_evidence.py").write_text("# unreviewed")
    monkeypatch.setattr(binding, "DIRECTORY", directory)
    monkeypatch.setattr(execution, "_execute_code", lambda *args: pytest.fail("execution must not happen"))
    with pytest.raises(ValueError, match="unreviewed"):
        controlled_validation.run_controlled_plane_wave()


def test_database_read_failure_never_returns_a_trust_claim(history, monkeypatch):
    result = run(monkeypatch)
    assert not binding.inspect_run(history.parent / "missing.db", result["run_id"])["provenance_prerequisites_passed"]

def test_configuration_drift_stops_before_dispatch_record(history, monkeypatch):
    from scripts.validation import plane_wave_evidence
    from app import controlled_dispatch
    changed = dict(plane_wave_evidence.EXPECTED_CONFIGURATION, cfl=.2)
    monkeypatch.setattr(plane_wave_evidence, "EXPECTED_CONFIGURATION", changed)
    monkeypatch.setattr(controlled_dispatch, "record", lambda *args: pytest.fail("must not persist dispatch"))
    monkeypatch.setattr(execution, "_execute_code", lambda *args, **kwargs: pytest.fail("must not execute"))
    with pytest.raises(ValueError, match="configuration_mismatch"):
        controlled_validation.run_controlled_plane_wave()
