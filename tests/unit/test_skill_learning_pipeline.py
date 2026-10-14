import copy
import json
import sqlite3
import uuid

from app import config, dashboard, skill_library_service as service
from app import skill_local_profile as local, tools
from app.skill_store import SkillStore
from scripts.validation.host_forward_signature import canonical
from tests.unit.test_executor_evidence import RUNTIME
from tests.unit.test_plane_wave_evidence import evidence


def _record_exact_history(tmp_path, monkeypatch):
    store = SkillStore(tmp_path / "skills.db")
    monkeypatch.setattr(service, "STORE", store)
    monkeypatch.setattr(dashboard, "DB_PATH", str(tmp_path / "history.db"))
    dashboard.init_db()
    for flag in ("SKILL_LIBRARY_ENABLED", "SKILL_RETRIEVAL_ENABLED", "SKILL_INJECTION_ENABLED"):
        monkeypatch.setattr(config, flag, True)
    monkeypatch.setattr(config, "SKILL_MANUAL_INTAKE_ENABLED", False)
    monkeypatch.setattr(config, "SKILL_LEARNING_ENABLED", True)

    code = local._recipe()
    stdout = json.dumps(dict(evidence(), case="1 平面波振幅守恒"), separators=(",", ":"))
    nonce = uuid.uuid4().hex
    runtime = copy.deepcopy(RUNTIME)
    runtime["executor_source_sha256"] = local.EXECUTOR_SOURCE_SHA256
    receipt = dict(
        version="executor-receipt.v1", request_nonce=nonce,
        code_sha256=local.pins.RECIPE_SHA256,
        stdout_sha256=local.pins.digest(stdout), stderr_sha256=local.pins.digest(""),
        exit_code=0, timed_out=False, timeout_seconds=30, runtime=runtime,
        runtime_sha256=local.pins.digest(canonical(runtime).decode()),
    )
    dispatch = dict(
        version="local-dispatch.v1", source_kind="managed_executor_transport",
        request_sha256=local.pins.digest(canonical(
            dict(code=code, timeout_seconds=30, request_nonce=nonce)).decode()),
        code_sha256=local.pins.RECIPE_SHA256, request_nonce=nonce,
        timeout_seconds=30, target_url="http://jwave-executor:8010/execute",
        trust_boundary="gateway_and_internal_executor",
        independent_host_authenticated=False, receipt_correlated=True,
    )
    run_id = str(uuid.uuid4())
    dashboard.record_execution(
        "run_jwave_code_with_retry", code, 0, False, 1, stdout, "", run_id=run_id,
        execution_evidence=dict(receipt=receipt, local_dispatch=dispatch),
    )
    return store, run_id


def test_retry_finalizer_initializes_store_and_admits_exact_q4(tmp_path, monkeypatch):
    store, run_id = _record_exact_history(tmp_path, monkeypatch)
    assert not store.path.exists()
    result = {}
    tools._finalize_skill_learning(result, run_id, None)
    assert result["local_skill_intake"]["admitted"]
    assert result["skill_learning"]["status"] == "active"
    assert result["skill_learning"]["quality_level"] == "Q4"
    with sqlite3.connect(store.path) as db:
        assert db.execute(
            "SELECT status,quality_level,success_count FROM simulation_skills"
        ).fetchone() == ("active", "Q4", 1)


def test_exact_q4_short_circuits_generic_q2(tmp_path, monkeypatch):
    _, run_id = _record_exact_history(tmp_path, monkeypatch)
    monkeypatch.setattr(
        tools.candidate_intake, "complete",
        lambda *_: (_ for _ in ()).throw(AssertionError("Q2 intake must not follow Q4")),
    )
    result = {}
    tools._finalize_skill_learning(result, run_id, {"version": "candidate-request.v1"})
    assert result["skill_learning"]["quality_level"] == "Q4"
    assert "candidate_accumulation" not in result


def test_manual_mode_still_requires_explicit_store_initialization(tmp_path, monkeypatch):
    store, run_id = _record_exact_history(tmp_path, monkeypatch)
    monkeypatch.setattr(config, "SKILL_LEARNING_ENABLED", False)
    monkeypatch.setattr(config, "SKILL_MANUAL_INTAKE_ENABLED", True)
    result = {}
    tools._finalize_skill_learning(result, run_id, None)
    assert not result["local_skill_intake"]["recorded"]
    assert "skill_learning" not in result
    assert not store.path.exists()


def test_all_learning_disabled_adds_no_result_fields(tmp_path, monkeypatch):
    store, run_id = _record_exact_history(tmp_path, monkeypatch)
    monkeypatch.setattr(config, "SKILL_LEARNING_ENABLED", False)
    monkeypatch.setattr(config, "SKILL_MANUAL_INTAKE_ENABLED", False)
    result = {}
    tools._finalize_skill_learning(result, run_id, None)
    assert result == {}
    assert not store.path.exists()
