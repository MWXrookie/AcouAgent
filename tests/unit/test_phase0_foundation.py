import sqlite3
import json

import pytest

from app import dashboard
from app.network_security import UnsafeOutboundUrl, validate_public_https_url
from app.physics_gate import assess_quality


def test_run_id_links_execution_analysis_and_llm_usage(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "history.db"
    monkeypatch.setattr(dashboard, "DB_PATH", str(db_path))
    dashboard.init_db()
    run_id = "run-phase0-test"
    assert dashboard.record_execution("test", "print(1)", 0, False, 1, "ok", "", run_id=run_id) == run_id
    assert dashboard.record_analysis_event(0, "normal", 1.0, 0.5, [2, 2], True, "ok", None, None, "", "", run_id=run_id) == run_id
    assert dashboard.record_llm_usage("test", {"prompt_tokens": 1}, 0.0, run_id=run_id) == run_id
    assert dashboard.find_run_id_for_output("ok", 0) == run_id
    db = sqlite3.connect(db_path)
    assert db.execute("SELECT run_id FROM executions").fetchone()[0] == run_id
    assert db.execute("SELECT run_id FROM analysis_events").fetchone()[0] == run_id
    assert db.execute("SELECT run_id FROM llm_usage").fetchone()[0] == run_id
    db.close()


def test_quality_levels_do_not_overclaim_physical_correctness() -> None:
    normal = {"verdict": "normal", "has_signal": True, "max_pressure": 1.0,
              "rms_pressure": 0.5, "field_shape": [128, 128]}
    assert assess_quality(exit_code=0, timed_out=False, analysis=normal)["quality_level"] == "Q1"
    params = '{"sound_speed":1500,"source_frequency":500000,"domain_N":[128,128],"domain_dx":[0.00025,0.00025],"pml_size":10,"source_index":[64,64],"sensor_index":[80,64]}'
    assert assess_quality(exit_code=0, timed_out=False, analysis=normal, params_json=params)["quality_level"] == "Q2"
    in_pml = params.replace("[64,64]", "[5,64]", 1)
    assert assess_quality(exit_code=0, timed_out=False, analysis=normal, params_json=in_pml)["quality_level"] == "Q1"
    assert assess_quality(exit_code=1, timed_out=False, analysis=normal)["quality_level"] == "Q0"


@pytest.mark.parametrize("change", [
    {"pml_size": None},
    {"pml_size": -1},
    {"pml_size": 64},
    {"domain_N": [128, 0]},
    {"domain_N": [128, 128.5]},
    {"domain_dx": [0.00025, float("nan")]},
    {"source_index": [5.5, 64]},
    {"sensor_index": [True, 64]},
])
def test_quality_rejects_invalid_parameter_evidence(change) -> None:
    analysis = {"verdict": "normal", "has_signal": True, "max_pressure": 1.0,
                "rms_pressure": 0.5, "field_shape": [128, 128]}
    params = {"sound_speed": 1500, "source_frequency": 500000,
              "domain_N": [128, 128], "domain_dx": [0.00025, 0.00025],
              "pml_size": 10, "source_index": [64, 64], "sensor_index": [80, 64]}
    params.update(change)
    result = assess_quality(exit_code=0, timed_out=False, analysis=analysis,
                            params_json=json.dumps(params))
    assert result["quality_level"] == "Q1"


@pytest.mark.parametrize("change", [
    {"max_pressure": float("nan")},
    {"rms_pressure": float("inf")},
    {"rms_pressure": 2.0},
    {"field_shape": [128, 0]},
    {"field_shape": [64, 64]},
])
def test_quality_rejects_invalid_field_evidence(change) -> None:
    analysis = {"verdict": "normal", "has_signal": True, "max_pressure": 1.0,
                "rms_pressure": 0.5, "field_shape": [128, 128]}
    analysis.update(change)
    params = {"sound_speed": 1500, "source_frequency": 500000,
              "domain_N": [128, 128], "domain_dx": 0.00025, "pml_size": 10}
    result = assess_quality(exit_code=0, timed_out=False, analysis=analysis,
                            params_json=json.dumps(params))
    assert result["quality_level"] == ("Q1" if change == {"field_shape": [64, 64]} else "Q0")


def test_3d_probe_at_pml_boundary_does_not_reach_q2() -> None:
    analysis = {"verdict": "normal", "has_signal": True, "max_pressure": 0.01,
                "rms_pressure": 0.002, "field_shape": [72, 72, 72]}
    params = {"sound_speed": 1500, "source_frequency": 300000,
              "domain_N": [72, 72, 72], "domain_dx": 0.0005,
              "cfl": 0.1, "pml_size": 8,
              "source_index": [36, 36, 36], "sensor_index": [63, 36, 36]}
    inside = assess_quality(exit_code=0, timed_out=False, analysis=analysis,
                            params_json=json.dumps(params))
    assert inside["quality_level"] == "Q2"

    # The physical region is [8, 64) in every dimension. Index 64 is PML.
    params["sensor_index"] = [64, 36, 36]
    at_pml = assess_quality(exit_code=0, timed_out=False, analysis=analysis,
                            params_json=json.dumps(params))
    assert at_pml["quality_level"] == "Q1"
    assert not next(check for check in at_pml["physics_checks"]
                    if check["check_id"] == "pml_geometry")["passed"]


def test_byok_rejects_private_or_non_https_endpoints(monkeypatch) -> None:
    with pytest.raises(UnsafeOutboundUrl):
        validate_public_https_url("http://api.example.com")
    with pytest.raises(UnsafeOutboundUrl):
        validate_public_https_url("https://127.0.0.1")
    monkeypatch.setattr(
        "app.network_security.socket.getaddrinfo",
        lambda *_args, **_kwargs: [(None, None, None, None, ("10.0.0.7", 443))],
    )
    with pytest.raises(UnsafeOutboundUrl):
        validate_public_https_url("https://model.example.com")


def test_byok_accepts_only_public_port_443(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.network_security.socket.getaddrinfo",
        lambda *_args, **_kwargs: [(None, None, None, None, ("8.8.8.8", 443))],
    )
    assert validate_public_https_url("https://model.example.com/") == "https://model.example.com"
    with pytest.raises(UnsafeOutboundUrl):
        validate_public_https_url("https://model.example.com:8443")
