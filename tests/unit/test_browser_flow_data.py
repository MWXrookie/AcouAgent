"""Data-contract checks found by the browser flow audit."""
import asyncio

from app import cache_store, llm


def test_cache_list_contains_entries_outside_top_and_recent(tmp_path, monkeypatch):
    monkeypatch.setattr(cache_store, "_CACHE_DB", tmp_path / "cache.db")
    for index in range(27):
        cache_store._update_cache(f"AttributeError|object{index}|t", "use to_array()", True)
    stats = cache_store.get_cache_stats()
    assert stats["total_entries"] == len(stats["entries"]) == 27
    assert len({row["signature"] for row in stats["entries"]}) == 27
    assert stats["total_hits"] == stats["total_successes"] == 27
    assert stats["entries_truncated"] is False


def test_retry_reports_real_cache_lookup_result(tmp_path, monkeypatch):
    monkeypatch.setattr(cache_store, "_CACHE_DB", tmp_path / "cache.db")
    signature = "AttributeError|TimeAxis|t"
    result = {"exit_code": 1, "stderr": "AttributeError: 'TimeAxis' object has no attribute 't'", "stdout": ""}
    prompts = []
    async def complete(profile, task, messages, **kwargs):
        prompts.append(messages[0]["content"])
        return {"content": "import json"}
    monkeypatch.setattr(llm.model_client, "complete", complete)
    assert asyncio.run(llm.fix_code("test", "old", result))["cache_hit"] is False
    cache_store._update_cache(signature, "use to_array() for the cached hint", True)
    fixed = asyncio.run(llm.fix_code("test", "old", result))
    assert fixed["cache_hit"] is True
    assert "use to_array() for the cached hint" in prompts[-1]
    assert fixed["error_signature"] == signature

def test_browser_numeric_roundtrip_keeps_fixed_skill_fingerprint():
    import json
    from app.skill_local_catalog import SCOPES, specification
    from app.skill_store import normalize_parameters, skill_fingerprint
    def js_numbers(value):
        if isinstance(value, list):
            return [js_numbers(v) for v in value]
        if isinstance(value, dict):
            return {k:js_numbers(v) for k,v in value.items()}
        return int(value) if type(value) is float and value.is_integer() else value
    for scope in SCOPES:
        spec = specification(scope)
        original = spec["parameters"]
        roundtripped = normalize_parameters(js_numbers(original))
        assert skill_fingerprint(spec["scene"], original) == skill_fingerprint(spec["scene"], roundtripped)
        assert json.dumps(original, sort_keys=True) == json.dumps(roundtripped, sort_keys=True)


def test_copied_skill_request_separates_scenario_metadata():
    import json
    from app.local_workflow import _explicit_modal_parameters, LocalWorkflowError
    import pytest
    params = {"source_type": "modal_initial_pressure", "scenario_type": "initial_pressure_2d",
              "initial_pressure_pa": 1}
    assert _explicit_modal_parameters("仿真 " + json.dumps(params)) == {
        "source_type": "modal_initial_pressure", "initial_pressure_pa": 1}
    params["scenario_type"] = "point_source_2d"
    with pytest.raises(LocalWorkflowError, match="declared_scenario_mismatch"):
        _explicit_modal_parameters(json.dumps(params))


def test_dashboard_rerun_records_own_receipt_and_analysis(monkeypatch):
    import json
    import httpx
    from starlette.applications import Starlette
    from starlette.routing import Route
    from app import portal
    class Registry:
        def __init__(self): self.routes = []
        def custom_route(self, path, methods):
            def decorate(fn):
                self.routes.append(Route(path, fn, methods=methods))
                return fn
            return decorate
    monkeypatch.setattr(portal.config, "PORTAL_ADMIN_TOKEN", "test-rerun-admin")
    monkeypatch.setattr(portal, "record_admin_event", lambda *args, **kwargs: None)
    monkeypatch.setattr(portal, "get_execution", lambda id: {"code": "import json"})
    result = {"exit_code": 0, "timed_out": False, "duration_ms": 1,
              "stdout": "raw field", "stderr": "", "execution_evidence": {"matched": True}}
    monkeypatch.setattr(portal.execution, "_execute_code", lambda *args: dict(result))
    monkeypatch.setattr(portal.execution, "_shrink_field_in_stdout", lambda text: "shrunken")
    monkeypatch.setattr(portal.execution, "_normalize_result_artifacts", lambda result: None)
    recorded, analyzed = [], []
    monkeypatch.setattr(portal, "record_execution", lambda **kwargs: recorded.append(kwargs))
    def analyze(stdout, stderr, exit_code, params, run_id):
        analyzed.append((stdout, params, run_id))
        return {"verdict": "normal", "quality_level": "Q1", "max_pressure": 1}
    monkeypatch.setattr(portal.analysis, "analyze_and_record", analyze)
    registry = Registry(); portal.register(registry)
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=Starlette(routes=registry.routes)),
                                     base_url="http://isolated") as client:
            response = await client.post("/dashboard/api/rerun", json={"id": 1}, headers={"X-Admin-Token": "test-rerun-admin"})
            assert response.status_code == 200
            return response.json()
    response = asyncio.run(run())
    assert response["result"]["analysis"]["quality_level"] == "Q1"
    assert recorded[0]["execution_evidence"] == {"matched": True}
    assert analyzed == [("raw field", "{}", recorded[0]["run_id"])]
    assert recorded[0]["stdout"] == "shrunken"
