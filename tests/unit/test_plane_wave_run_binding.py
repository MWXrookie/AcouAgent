import ast
import copy
import hashlib
import json
import sqlite3
from pathlib import Path

import pytest
from app import dashboard, execution
from scripts.validation import plane_wave_run_binding as binding
from tests.unit.test_plane_wave_evidence import evidence

ROOT = Path(__file__).resolve().parents[2]
RECIPE = (binding.DIRECTORY / "recipes/val1_plane_wave_v2.py").read_text().strip()


@pytest.fixture
def history(tmp_path, monkeypatch):
    path = tmp_path / "history.db"
    monkeypatch.setattr(dashboard, "DB_PATH", str(path))
    dashboard.init_db()
    dashboard.record_execution("run_jwave_code", RECIPE, 0, False, 1,
                               json.dumps(evidence()), "", run_id="controlled")
    return path


def alter(path, **changes):
    with sqlite3.connect(path) as db:
        for key, value in changes.items():
            db.execute(f"UPDATE executions SET {key}=?", (value,))


def test_exact_contract_passes_prerequisite_without_granting_trust(history):
    before = history.read_bytes()
    receipt = binding.inspect_run(history, "controlled")
    assert receipt["code_contract_matched"] and receipt["numeric_pass"]
    assert not receipt["trusted_run"] and not receipt["parameters_bound"]
    assert receipt["reason"] == "runtime_identity_not_bound"
    assert "quality_level" not in receipt
    assert history.read_bytes() == before


def test_recipe_matches_reviewed_baseline_and_existing_gateway_cleaner():
    assert len(RECIPE.encode()) < 20_000
    assert execution._clean_code(RECIPE) == RECIPE
    recipe_ast = ast.parse(RECIPE)
    baseline_ast = ast.parse((ROOT / "executor/validation_baseline.py").read_text())
    for node in recipe_ast.body:
        if isinstance(node, ast.FunctionDef):
            original = next(n for n in baseline_ast.body if isinstance(n, ast.FunctionDef) and n.name == node.name)
            assert ast.dump(node) == ast.dump(original)
    assert binding.digest(RECIPE) == binding.RECIPE_SHA256


@pytest.mark.parametrize("fault", ["print_only", "changed_parameter", "extra_statement", "wrong_stored_hash", "legacy", "timeout", "exit", "missing_code"])
def test_analytic_forgery_and_wrong_code_cannot_pass(history, fault):
    if fault in ("print_only", "changed_parameter", "extra_statement"):
        code = "print('forged perfect analytic waveform')" if fault == "print_only" else RECIPE.replace("Nx, Ny = 384, 384", "Nx, Ny = 384, 256") if fault == "changed_parameter" else RECIPE + "\nprint('extra')"
        assert code != RECIPE
        alter(history, code=code, code_sha256=binding.digest(code))
    elif fault == "wrong_stored_hash":
        alter(history, code_sha256="0"*64)
    elif fault == "legacy":
        alter(history, record_version=None)
    elif fault == "timeout":
        alter(history, timed_out=1)
    elif fault == "exit":
        alter(history, exit_code=1)
    else:
        alter(history, code="")
    receipt = binding.inspect_run(history, "controlled")
    assert not receipt["code_contract_matched"] and not receipt["trusted_run"]


@pytest.mark.parametrize("fault", ["profile", "config", "missing", "zero", "bool", "duplicate_key", "nan_summary", "suffix", "oversize"])
def test_profile_config_and_numeric_receipts_cannot_override_failure(history, fault):
    data = copy.deepcopy(evidence())
    data.update(quality_level="Q4", trusted_run=True, numeric_pass=True)
    if fault == "profile":
        data["raw_evidence"]["profile"] = "val1-plane-wave-v1"
    elif fault == "config":
        data["raw_evidence"]["configuration"]["pml_size"] = 0
    elif fault == "missing":
        del data["raw_evidence"]
    elif fault == "zero":
        data["raw_evidence"]["pressure_x2"] = [0]*3400
    elif fault == "bool":
        data["raw_evidence"]["configuration"]["initial_pressure_pa"] = True
    output = json.dumps(data)
    if fault == "duplicate_key":
        output = '{"raw_evidence":null,' + output[1:]
    elif fault == "nan_summary":
        output = '{"unused":NaN,' + output[1:]
    elif fault == "suffix":
        output += " PASS"
    elif fault == "oversize":
        output = " "*1_000_001
    alter(history, stdout=output)
    receipt = binding.inspect_run(history, "controlled")
    assert not receipt["numeric_pass"] and not receipt["trusted_run"]


def test_unknown_duplicate_run_and_missing_database_fail_closed(history, tmp_path):
    assert not binding.inspect_run(history, "unknown")["code_contract_matched"]
    dashboard.record_execution("run_jwave_code", RECIPE, 0, False, 1,
                               json.dumps(evidence()), "", run_id="controlled")
    assert not binding.inspect_run(history, "controlled")["code_contract_matched"]
    nonexistent = tmp_path / "nonexistent.db"
    assert not binding.inspect_run(nonexistent, "controlled")["code_contract_matched"]
    assert not nonexistent.exists()


@pytest.mark.parametrize("asset", ["recipe", "validator"])
def test_unreviewed_local_asset_fails_closed(history, tmp_path, monkeypatch, asset):
    (tmp_path / "recipes").mkdir()
    recipe = RECIPE + ("\n# unreviewed change" if asset == "recipe" else "")
    (tmp_path / "recipes/val1_plane_wave_v2.py").write_text(recipe)
    validator = (binding.DIRECTORY / "plane_wave_evidence.py").read_bytes()
    (tmp_path / "plane_wave_evidence.py").write_bytes(validator + (b"\n# changed" if asset == "validator" else b""))
    monkeypatch.setattr(binding, "DIRECTORY", tmp_path)
    assert binding.inspect_run(history, "controlled")["reason"] == "unreviewed_recipe_or_validator"
