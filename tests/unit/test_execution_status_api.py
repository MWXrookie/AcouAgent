"""Bounded per-run facts must not pull unrelated output, images or code."""
import asyncio
import uuid

import httpx
import pytest

from app import portal
from tests.unit.test_portal_model_profiles import _app
from scripts.validation import model_matrix_acceptance as matrix


def request(path, method="GET"):
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=_app()),
                                     base_url="http://isolated") as client:
            return await client.request(method, path)
    return asyncio.run(run())


def test_single_run_response_is_bounded_even_with_large_history(monkeypatch):
    run = str(uuid.uuid4())
    row = dict(id=99, run_id=run, tool_name="actual_execute", exit_code=0, timed_out=False,
               stdout="private-output" * 200000, code="private-code" * 200000,
               image_base64="large-image" * 200000, executor_evidence_json="private-receipt")
    monkeypatch.setattr(portal, "get_execution_by_run_id", lambda value: row if value == run else None)
    def forbidden(*args, **kwargs):
        raise AssertionError("bulk history must not be loaded")
    monkeypatch.setattr(portal, "get_executions", forbidden)
    monkeypatch.setattr(portal, "get_analysis_updates", forbidden)
    response = request("/dashboard/api/executions/by-run/" + run)
    assert response.status_code == 200 and len(response.content) < 512
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {"execution": {k: row[k] for k in
        ("id", "run_id", "tool_name", "exit_code", "timed_out")}}


@pytest.mark.parametrize("run", ["not-a-uuid", "' OR 1=1--", uuid.uuid4().hex])
def test_malformed_run_never_queries_history(monkeypatch, run):
    def forbidden(*args): raise AssertionError("invalid run must be rejected first")
    monkeypatch.setattr(portal, "get_execution_by_run_id", forbidden)
    assert request("/dashboard/api/executions/by-run/" + run).status_code == 400


@pytest.mark.parametrize("row", [None, {"run_id": str(uuid.uuid4())}])
def test_missing_ambiguous_or_other_run_fails_closed(monkeypatch, row):
    monkeypatch.setattr(portal, "get_execution_by_run_id", lambda value: row)
    assert request("/dashboard/api/executions/by-run/" + str(uuid.uuid4())).status_code == 404


def test_status_route_does_not_accept_writes():
    assert request("/dashboard/api/executions/by-run/" + str(uuid.uuid4()), "POST").status_code == 405


def test_matrix_looks_up_only_exact_run_and_rejects_different_run(monkeypatch):
    run = str(uuid.uuid4())
    calls = []
    def fetch(method, url, payload, timeout):
        calls.append(url)
        assert url == "http://gateway/dashboard/api/executions/by-run/" + run
        return {"execution": {"run_id": str(uuid.uuid4()), "exit_code": 0, "timed_out": False}}
    monkeypatch.setattr(matrix.first_uat, "_request_json", fetch)
    monkeypatch.setattr(matrix.time, "sleep", lambda seconds: None)
    assert matrix._execution_for("http://gateway", run) is None
    assert len(calls) == 5
