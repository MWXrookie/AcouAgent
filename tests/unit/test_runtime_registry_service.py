import json
import sqlite3
import pytest
from app import runtime_registry_service as service
from tests.unit.test_runtime_approval_contract import package


@pytest.fixture
def fixed(tmp_path, monkeypatch):
    manifest, data = package(tmp_path)
    data["approval_id"] = service.APPROVAL_ID
    data["scope"] = "production"
    manifest.write_text(json.dumps(data))
    monkeypatch.setattr(service, "DATABASE", tmp_path / "registry.db")
    monkeypatch.setattr(service, "PACKAGE_ROOT", tmp_path)
    monkeypatch.setattr(service.config, "PORTAL_ADMIN_TOKEN", "test-only-token-" * 3)
    return manifest, data


def observed(data):
    return [data[k] for k in ("runtime_sha256", "image_id", "recipe_sha256", "validator_sha256")]


@pytest.mark.parametrize("token", ["", "wrong", None, True])
def test_unauthorized_no_db_or_write(fixed, token):
    with pytest.raises(PermissionError): service.register_candidate(token, "test")
    assert not service.DATABASE.exists()


def test_fixed_lifecycle_retirement_no_cache(fixed):
    _, data = fixed
    token = service.config.PORTAL_ADMIN_TOKEN
    service.register_candidate(token, "fixture")
    assert not service.inspect_eligibility(*observed(data))["contract_eligible"]
    service.change_status(token, 1, "approved", "fixture review")
    result = service.inspect_eligibility(*observed(data))
    assert result["contract_eligible"] and not result["runtime_approved"] and not result["trusted_run"]
    service.change_status(token, 2, "retired", "fixture withdrawal")
    assert not service.inspect_eligibility(*observed(data))["contract_eligible"]
    with sqlite3.connect(service.DATABASE) as c:
        actors = c.execute("SELECT DISTINCT actor FROM runtime_audit").fetchall()
        assert actors == [("portal-admin",)]


@pytest.mark.parametrize("index", range(4))
def test_drift_rejected(fixed, index):
    _, data = fixed
    token = service.config.PORTAL_ADMIN_TOKEN
    service.register_candidate(token, "test")
    service.change_status(token, 1, "approved", "test")
    values = observed(data)
    values[index] = "drift"
    assert not service.inspect_eligibility(*values)["contract_eligible"]


def test_package_changed_and_retire_without_file(fixed):
    manifest, data = fixed
    token = service.config.PORTAL_ADMIN_TOKEN
    service.register_candidate(token, "test")
    data["runtime_sha256"] = "f" * 64
    manifest.write_text(json.dumps(data))
    with pytest.raises(ValueError): service.change_status(token, 1, "approved", "test")
    manifest.unlink()
    service.change_status(token, 1, "retired", "withdraw missing evidence")
    assert not service.inspect_eligibility(*observed(data))["contract_eligible"]


def test_empty_server_token_rejected(fixed, monkeypatch):
    monkeypatch.setattr(service.config, "PORTAL_ADMIN_TOKEN", "")
    with pytest.raises(PermissionError): service.register_candidate("", "test")


def test_missing_registry_does_not_create(fixed):
    _, data = fixed
    assert not service.inspect_eligibility(*observed(data))["contract_eligible"]
    assert not service.DATABASE.exists()


@pytest.mark.parametrize("mode", ["normal", "retired", "package_changed", "runtime", "receipt"])
def test_dispatch_completion_recheck(fixed, mode):
    manifest, data = fixed
    token = service.config.PORTAL_ADMIN_TOKEN
    service.register_candidate(token, "fixture")
    service.change_status(token, 1, "approved", "fixture")
    before = service.capture_dispatch_contract()
    evidence = {"matched": mode != "receipt",
                "receipt": {"runtime_sha256": data["runtime_sha256"]}}
    if mode == "retired":
        service.change_status(token, 2, "retired", "during run")
    if mode == "package_changed":
        data["runtime_sha256"] = "f" * 64
        manifest.write_text(json.dumps(data))
    if mode == "runtime":
        evidence["receipt"]["runtime_sha256"] = "f" * 64
    result = service.recheck_dispatch_contract(before, evidence)
    assert result["approval_window_matched"] == (mode == "normal")
    assert not result["runtime_approved"] and not result["trusted_run"] and not result["run_bound"]
    assert result["dispatch_revision"] == 2


def test_dispatch_missing_registry_no_creation(fixed):
    assert not service.capture_dispatch_contract()["contract_eligible"]
    assert not service.DATABASE.exists()


@pytest.mark.parametrize("retire", [False, True])
def test_gateway_dispatch_uses_server_snapshot_and_rechecks_after_http(fixed, monkeypatch, retire):
    from app import execution
    from tests.unit.test_executor_evidence import envelope
    manifest, data = fixed
    _, receipt = envelope()
    data["runtime_sha256"] = receipt["runtime_sha256"]
    manifest.write_text(json.dumps(data))
    token = service.config.PORTAL_ADMIN_TOKEN
    service.register_candidate(token, "fixture")
    service.change_status(token, 1, "approved", "fixture")
    def post(url, headers, json, timeout):
        if retire:
            service.change_status(token, 2, "retired", "in flight")
        result, receipt = envelope(code=json["code"], nonce=json["request_nonce"])
        result["executor_receipt"] = receipt
        # An executor response cannot self-approve the gateway-created evidence.
        result["execution_evidence"] = {"approval_usage": {"runtime_approved": True}}
        class Response:
            def raise_for_status(self): pass
            def json(self): return result
        return Response()
    monkeypatch.setattr(execution.httpx, "post", post)
    evidence = execution._execute_code("print('ok')", 15)["execution_evidence"]
    usage = evidence["approval_usage"]
    assert evidence["matched"]
    assert usage["approval_window_matched"] == (not retire)
    assert not usage["trusted_run"] and not usage["runtime_approved"] and not usage["run_bound"]
    if retire:
        assert usage["reason"] == "not_currently_approved"


@pytest.mark.parametrize("evidence", [
    None,
    {},
    {"matched": True},
    {"matched": True, "receipt": {}},
    {"matched": True, "receipt": {"runtime_sha256": "z" * 64}},
    {"matched": True, "receipt": None},
])
def test_dispatch_recheck_denies_malformed_evidence_without_raising(fixed, evidence):
    """Dispatch-path safety: bad evidence must deny, never crash the caller."""
    manifest, data = fixed
    token = service.config.PORTAL_ADMIN_TOKEN
    service.register_candidate(token, "fixture")
    service.change_status(token, 1, "approved", "fixture")
    before = service.capture_dispatch_contract()
    result = service.recheck_dispatch_contract(before, evidence)
    assert result["approval_window_matched"] is False
    assert not result["trusted_run"]
    assert not result["runtime_approved"]
    assert not result["run_bound"]
    assert result["reason"] in {"invalid_dispatch_evidence",
                                "executor_receipt_unmatched",
                                "executor_runtime_mismatch"}
