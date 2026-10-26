import uuid

import pytest

from scripts.validation import first_simulation_acceptance as acceptance


def test_evaluate_records_only_sanitized_success(monkeypatch):
    run_id = str(uuid.uuid4())
    responses = iter((
        {"orchestration_backend": "local", "default_profile": "fast",
         "profiles": [{"id": "fast", "label": "Fast"}]},
        {"workflow_status": "succeeded", "orchestration_backend": "local",
         "run_id": run_id, "quality_level": "Q2", "model": "acoustic-fast",
         "request_id": "request-1", "workflow_run_id": "workflow-1",
         "report": "large report must not enter receipt", "code": "secret-like-code"},
        {"execution": {"id": 7, "run_id": run_id, "exit_code": 0,
                       "timed_out": False, "code": "do not copy"}},
    ))
    monkeypatch.setattr(acceptance, "_request_json", lambda *args, **kwargs: next(responses))

    receipt = acceptance.evaluate("http://gateway:8001", None, "simulate", 10)

    assert receipt["accepted"] is True
    assert receipt["model_profile"] == "fast"
    assert receipt["quality_level"] == "Q2"
    assert receipt["run_id"] == run_id
    assert receipt["execution"] == {"id": 7, "exit_code": 0, "timed_out": False}
    assert "report" not in receipt and "code" not in receipt
    assert "code" not in receipt["execution"]


@pytest.mark.parametrize(
    ("profile_payload", "result_payload", "expected"),
    [
        ({"orchestration_backend": "dify", "default_profile": "fast",
          "profiles": [{"id": "fast"}]}, None, "local_backend_not_active"),
        ({"orchestration_backend": "local", "default_profile": "missing", "profiles": []},
         None, "model_profile_unavailable"),
        ({"orchestration_backend": "local", "default_profile": "fast",
          "profiles": [{"id": "fast"}]},
         {"workflow_status": "error", "error_code": "model_transport_failed"},
         "workflow_not_succeeded:model_transport_failed"),
    ],
)
def test_evaluate_fails_closed(monkeypatch, profile_payload, result_payload, expected):
    payloads = [profile_payload]
    if result_payload is not None:
        payloads.append(result_payload)
    responses = iter(payloads)
    monkeypatch.setattr(acceptance, "_request_json", lambda *args, **kwargs: next(responses))

    with pytest.raises(acceptance.AcceptanceError, match=expected):
        acceptance.evaluate("http://gateway:8001", None, "simulate", 10)


def test_request_url_rejects_non_http_scheme():
    with pytest.raises(acceptance.AcceptanceError, match="invalid_base_url"):
        acceptance._url("file:///tmp/service", "/chat")


def test_evaluate_rejects_timed_out_execution(monkeypatch):
    run_id = str(uuid.uuid4())
    responses = iter((
        {"orchestration_backend": "local", "default_profile": "fast",
         "profiles": [{"id": "fast"}]},
        {"workflow_status": "succeeded", "orchestration_backend": "local",
         "run_id": run_id, "quality_level": "Q2"},
        {"execution": {"id": 8, "run_id": run_id, "exit_code": 0,
                       "timed_out": 1}},
    ))
    monkeypatch.setattr(acceptance, "_request_json", lambda *args, **kwargs: next(responses))

    with pytest.raises(acceptance.AcceptanceError, match="execution_not_successful"):
        acceptance.evaluate("http://gateway:8001", None, "simulate", 10)
