import json
import uuid

import pytest

from scripts.validation import model_matrix_acceptance as matrix


def _tasks_document():
    document, tasks_sha256 = matrix.load_tasks("docs/validation/stage1/tasks.json")
    return document, tasks_sha256


def test_frozen_tasks_contract_is_loaded():
    document, tasks_sha256 = _tasks_document()
    assert len(document["tasks"]) == 30
    assert len(tasks_sha256) == 64


def test_model_availability_evidence_requires_actual_booleans(tmp_path):
    target = tmp_path / "records.jsonl"
    target.write_text(json.dumps({"model_review_available": "false"}) + "\n")
    with pytest.raises(matrix.MatrixError, match="record_model_availability_invalid"):
        matrix.load_records(target)


def test_local_task_keeps_only_compact_evidence(monkeypatch):
    document, tasks_sha256 = _tasks_document()
    task = document["tasks"][0]
    run_id = str(uuid.uuid4())
    responses = iter((
        {
            "workflow_status": "succeeded",
            "run_id": run_id,
            "workflow_run_id": "workflow-1",
            "quality_level": "Q4",
            "model": "model-a",
            "model_review_available": False,
            "model_explanation_available": True,
            "skill_usage": [{"stage": "reference_delivered", "skill_id": "safe"}],
            "report": "must not persist",
            "code": "must not persist",
        },
        {
            "execution": {
                "id": 7,
                "run_id": run_id,
                "exit_code": 0,
                "timed_out": False,
                "code": "must not persist",
                "stdout": "must not persist",
            }
        },
    ))
    monkeypatch.setattr(matrix.first_uat, "_request_json", lambda *args, **kwargs: next(responses))

    row = matrix.run_local_task(
        "http://gateway:8001", "profile-a", "local-a", task, tasks_sha256, 30
    )

    assert row["deterministic_gate_pass"] is True
    assert row["reference_delivered"] is True
    assert row["run_id"] == run_id
    assert row["model_review_available"] is False and row["model_explanation_available"] is True
    encoded = json.dumps(row)
    assert "must not persist" not in encoded
    assert task["prompt"] not in encoded


def test_local_missing_task_fails_closed_without_reference(monkeypatch):
    document, tasks_sha256 = _tasks_document()
    task = next(item for item in document["tasks"] if item["kind"] == "missing")
    monkeypatch.setattr(
        matrix.first_uat,
        "_request_json",
        lambda *args, **kwargs: {
            "workflow_status": "error",
            "error_code": "simulation_parameters_invalid",
            "skill_usage": [],
        },
    )
    row = matrix.run_local_task(
        "http://gateway:8001", "profile-a", "local-a", task, tasks_sha256, 30
    )
    assert row["outcome"] == "failed"
    assert row["reference_delivered"] is False
    assert row["execution_pass"] is False


def test_dify_normalization_discards_raw_payload_fields():
    document, tasks_sha256 = _tasks_document()
    task = document["tasks"][0]
    row = matrix.normalize_dify_record(
        {
            "outcome": "completed",
            "verdict": "normal",
            "independent_numeric_pass": True,
            "reference_delivered": True,
            "duration_ms": 12.0,
            "run_id": "run-1",
            "workflow_run_id": "workflow-1",
            "response": "must not persist",
            "code": "must not persist",
            "trace_authority": {"raw": "must not persist"},
        },
        "dify",
        task,
        tasks_sha256,
    )
    encoded = json.dumps(row)
    assert row["deterministic_gate_pass"] is True
    assert "must not persist" not in encoded
    assert task["prompt"] not in encoded


def test_dify_wall_deadline_stops_pinged_workflow(monkeypatch):
    document, tasks_sha256 = _tasks_document()
    task = document["tasks"][0]

    class Response:
        headers = {"content-type": "text/event-stream"}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def raise_for_status(self):
            return None

        def iter_lines(self):
            yield 'data: {"event":"ping","task_id":"dify-task-1"}'
            yield ""
            yield 'data: {"event":"ping","task_id":"dify-task-1"}'

    class StopResponse:
        def raise_for_status(self):
            return None

    class Client:
        def __init__(self):
            self.stop_calls = 0

        def stream(self, *_args, **_kwargs):
            return Response()

        def post(self, *_args, **_kwargs):
            self.stop_calls += 1
            return StopResponse()

    times = iter((0.0, 0.2, 0.4, 1.1, 1.2))
    monkeypatch.setattr(matrix.time, "monotonic", lambda: next(times))
    client = Client()
    row = matrix.run_dify_task(
        client,
        "http://nginx/v1",
        "secret-not-persisted",
        "dify",
        task,
        tasks_sha256,
        1.0,
    )
    assert row["outcome"] == "timeout"
    assert row["error_code"] == "timeout"
    assert row["deterministic_gate_pass"] is False
    assert client.stop_calls == 1


def test_validate_local_profile_requires_configured_profile_and_preflight(monkeypatch):
    responses = iter((
        {
            "orchestration_backend": "local",
            "profiles": [{"id": "profile-a", "configured": True, "model": "model-a"}],
        },
        {"ok": True, "status": "basic_compatible"},
    ))
    monkeypatch.setattr(matrix.first_uat, "_request_json", lambda *args, **kwargs: next(responses))
    result = matrix.validate_local_profile("http://gateway:8001", "profile-a")
    assert result == {
        "profile_id": "profile-a",
        "model": "model-a",
        "preflight_status": "basic_compatible",
    }


def test_validate_local_profile_accepts_session_without_persisting_replayable_id(monkeypatch):
    responses = iter((
        {"orchestration_backend": "local", "profiles": []},
        {
            "ok": True,
            "status": "basic_compatible",
            "profile_id": "byok-session:0123456789abcdef",
            "model": "user-model-a",
        },
    ))
    calls = []

    def request(method, url, payload, timeout):
        calls.append((method, url, payload, timeout))
        return next(responses)

    monkeypatch.setattr(matrix.first_uat, "_request_json", request)
    replayable = "byok-0123456789abcdef0123456789abcdef"
    result = matrix.validate_local_profile("http://gateway:8001", replayable)

    assert calls[1][2] == {"profile_id": replayable}
    assert result == {
        "profile_id": "byok-session:0123456789abcdef",
        "model": "user-model-a",
        "preflight_status": "basic_compatible",
    }
    assert replayable not in json.dumps(result)


def test_validate_local_profile_rejects_replayable_session_identity(monkeypatch):
    responses = iter((
        {"orchestration_backend": "local", "profiles": []},
        {
            "ok": True,
            "status": "basic_compatible",
            "profile_id": "byok-replayable",
            "model": "user-model-a",
        },
    ))
    monkeypatch.setattr(matrix.first_uat, "_request_json", lambda *args, **kwargs: next(responses))

    with pytest.raises(matrix.MatrixError, match="model_preflight_identity_invalid"):
        matrix.validate_local_profile("http://gateway:8001", "byok-secret-handle")


def test_local_task_records_only_session_audit_identity(monkeypatch):
    document, tasks_sha256 = _tasks_document()
    task = next(item for item in document["tasks"] if item["kind"] == "missing")
    requests = []

    def request(method, url, payload, timeout):
        requests.append(payload)
        return {
            "workflow_status": "error",
            "error_code": "simulation_parameters_invalid",
            "skill_usage": [],
        }

    monkeypatch.setattr(matrix.first_uat, "_request_json", request)
    replayable = "byok-secret-handle"
    row = matrix.run_local_task(
        "http://gateway:8001", replayable, "local-a", task, tasks_sha256, 30,
        record_profile_id="byok-session:0123456789abcdef",
    )

    assert requests[0]["model_profile"] == replayable
    assert row["profile_id"] == "byok-session:0123456789abcdef"
    assert replayable not in json.dumps(row)

    with pytest.raises(matrix.MatrixError, match="byok_audit_identity_required"):
        matrix.run_local_task(
            "http://gateway:8001", replayable, "local-a", task, tasks_sha256, 30,
        )


def _complete_records(document, tasks_sha256, lanes):
    rows = []
    for lane in lanes:
        for task in document["tasks"]:
            positive = task["expected_reference"]
            missing = task["kind"] == "missing"
            rows.append({
                "schema": matrix.SCHEMA,
                "lane_id": lane,
                "backend": "dify" if lane == "dify" else "local",
                "profile_id": lane if lane != "dify" else None,
                "model": lane,
                "model_identity": lane,
                "revision": "a" * 40 if lane != "dify" else "b" * 64,
                "tasks_sha256": tasks_sha256,
                "task_id": task["task_id"],
                "task_kind": task["kind"],
                "expected_behavior": task["expected_behavior"],
                "prompt_sha256": matrix._sha_text(task["prompt"]),
                "outcome": "failed" if missing else "completed",
                "workflow_status": "error" if missing else "succeeded",
                "duration_ms": 10.0,
                "run_id": str(uuid.uuid4()),
                "workflow_run_id": str(uuid.uuid4()),
                "quality_level": "Q4" if positive else None,
                "execution_pass": not missing,
                "deterministic_gate_pass": positive,
                "reference_delivered": positive,
                "error_code": "parameters_missing" if missing else None,
            })
    return rows


def test_matrix_evaluator_accepts_complete_matching_lanes():
    document, tasks_sha256 = _tasks_document()
    lanes = ["local-a", "local-b", "dify"]
    report = matrix.evaluate_matrix(
        document, tasks_sha256, _complete_records(document, tasks_sha256, lanes), lanes
    )
    assert report["complete"] is True
    assert report["targets_met"] is True
    assert report["formal_acceptance"] is False
    assert report["cross_lane_mismatches"] == []


def test_matrix_evaluator_reports_missing_and_negative_reference():
    document, tasks_sha256 = _tasks_document()
    lanes = ["local-a", "local-b"]
    rows = _complete_records(document, tasks_sha256, lanes)
    rows.pop()
    outside = next(
        row for row in rows if row["lane_id"] == "local-b" and row["task_kind"] == "outside"
    )
    outside["reference_delivered"] = True
    report = matrix.evaluate_matrix(document, tasks_sha256, rows, lanes)
    assert report["complete"] is False
    assert report["targets_met"] is False
    assert report["lane_metrics"]["local-b"]["negative_reference_count"] == 1


def test_records_reject_duplicate_lane_task(tmp_path):
    path = tmp_path / "records.jsonl"
    row = {"lane_id": "local-a", "task_id": "exact-01"}
    path.write_text(json.dumps(row) + "\n" + json.dumps(row) + "\n", encoding="utf-8")
    with pytest.raises(matrix.MatrixError, match="duplicate_record"):
        matrix.load_records(path)


def test_records_reject_forbidden_payload_fields(tmp_path):
    path = tmp_path / "records.jsonl"
    path.write_text(
        json.dumps({
            "lane_id": "local-a",
            "task_id": "exact-01",
            "report": "must not persist",
        }) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(matrix.MatrixError, match="record_contains_forbidden_field"):
        matrix.load_records(path)


def test_records_reject_unknown_or_nested_fields(tmp_path):
    path = tmp_path / "records.jsonl"
    path.write_text(
        json.dumps({
            "lane_id": "local-a",
            "task_id": "exact-01",
            "metadata": {"safe": True},
        }) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(matrix.MatrixError, match="record_contains_unknown_field"):
        matrix.load_records(path)
