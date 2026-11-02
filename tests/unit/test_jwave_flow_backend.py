import asyncio
import json

import httpx

from app.adapters import JwaveFlowCoreAdapter
from app.contracts import ChatRequest
from cores.jwave_flow import adapter as adapter_module
from cores.jwave_flow import client as jwc

REAL_CLIENT = httpx.AsyncClient


def _run(
    monkeypatch,
    frames,
    *,
    status=200,
    content_type="application/x-ndjson",
    health_payload=None,
    model_config=None,
):
    calls = []

    def handler(request):
        calls.append(request)
        if request.method == "GET" and request.url.path == "/health":
            return httpx.Response(
                200,
                json=health_payload
                or {
                    "status": "ok",
                    "service": "jwave-flow-agent-bridge",
                    "api_version": "v1",
                    "model_config_mode": "managed",
                    "capabilities": {
                        "workflow": True,
                        "knowledge_base_management": False,
                        "model_configuration": True,
                        "history": False,
                        "memory": False,
                        "sessions": False,
                    },
                },
            )
        body = "".join(json.dumps(frame, ensure_ascii=False) + "\n" for frame in frames)
        return httpx.Response(
            status,
            content=body.encode("utf-8"),
            headers={"content-type": content_type},
        )

    monkeypatch.setattr(jwc, "BASE_URL", "http://jwave-flow.test")
    monkeypatch.setattr(jwc, "BRIDGE_TOKEN", "c" * 32)
    monkeypatch.setattr(
        jwc.httpx,
        "AsyncClient",
        lambda **kwargs: REAL_CLIENT(transport=httpx.MockTransport(handler), **kwargs),
    )

    model_config = model_config or {
        "base_url": "https://model.example/v1",
        "model": "managed-model",
        "api_key": "model-secret",
        "temperature": 0.3,
        "timeout_seconds": 120,
    }

    async def collect():
        return [
            event
            async for event in jwc.jwave_flow_events(
                "仿真 2 MHz 点声源",
                model_config=model_config,
            )
        ]

    return asyncio.run(collect()), calls


def test_jwave_flow_maps_node_and_complete_to_stable_events(monkeypatch):
    events, calls = _run(
        monkeypatch,
        [
            {
                "type": "node",
                "node": "generate",
                "state": {
                    "trace": ["⑤ 代码生成 完成"],
                    "kb_docs": [{"private": "do-not-forward"}],
                },
            },
            {
                "type": "complete",
                "state": {
                    "success": True,
                    "verified": True,
                    "iterations": 2,
                    "elapsed": 1.5,
                    "text": "print('ok')",
                    "stdout": "pressure max=1.0",
                    "stderr": "",
                    "selfcheck": {"abs_max": 1.0},
                    "outputs": ["/tmp/field.png"],
                    "trace": ["⑧ 输出 完成"],
                },
            },
        ],
    )

    assert [event["event"] for event in events].count("result") == 1
    assert events[-1]["event"] == "result"
    assert events[-1]["workflow_status"] == "succeeded"
    assert events[-1]["orchestration_backend"] == "jwave_flow"
    assert events[-1]["verified"] is True
    assert "print('ok')" in events[-1]["report"]
    assert "pressure max=1.0" in events[-1]["report"]
    assert events[-1]["outputs"] == ["/tmp/field.png"]
    assert any(event.get("node") == "generate" for event in events)
    assert "do-not-forward" not in json.dumps(events)
    run_calls = [call for call in calls if call.method == "POST"]
    assert len(run_calls) == 1
    assert run_calls[0].url.path == "/v1/run"
    assert run_calls[0].headers["x-jwave-flow-bridge-token"] == "c" * 32
    assert json.loads(run_calls[0].content)["query"] == "仿真 2 MHz 点声源"
    assert json.loads(run_calls[0].content)["model_config"]["model"] == "managed-model"


def test_jwave_flow_upstream_error_is_sanitized(monkeypatch):
    events, _ = _run(
        monkeypatch,
        [{"type": "error", "error": "secret traceback and api-key"}],
    )

    assert events[-1]["event"] == "result"
    assert events[-1]["workflow_status"] == "error"
    assert events[-1]["error_code"] == "jwave_flow_failed"
    assert "secret" not in json.dumps(events)
    assert "api-key" not in json.dumps(events)


def test_jwave_flow_http_and_stream_errors_are_terminal(monkeypatch):
    http_events, _ = _run(monkeypatch, [], status=503)
    assert http_events[-1]["error_code"] == "jwave_flow_http_503"

    bad_events, _ = _run(monkeypatch, [], content_type="application/json")
    assert bad_events[-1]["error_code"] == "jwave_flow_invalid_stream"

    incomplete_events, _ = _run(monkeypatch, [])
    assert incomplete_events[-1]["error_code"] == "jwave_flow_incomplete"


def test_jwave_flow_rejects_a_general_frontend_or_writable_core(monkeypatch):
    events, calls = _run(
        monkeypatch,
        [],
        health_payload={
            "status": "ok",
            "service": "jwave-frontend",
            "api_version": "v1",
            "capabilities": {"workflow": True, "knowledge_base_management": True},
        },
    )
    assert events[-1]["error_code"] == "jwave_flow_invalid_bridge"
    assert not any(call.method == "POST" for call in calls)


def test_jwave_flow_managed_profile_resolves_before_upstream_call(monkeypatch):
    called = False

    def handler(_request):
        nonlocal called
        called = True
        return httpx.Response(500)

    monkeypatch.setattr(
        jwc.httpx,
        "AsyncClient",
        lambda **kwargs: REAL_CLIENT(transport=httpx.MockTransport(handler), **kwargs),
    )

    async def collect():
        request = ChatRequest(message="仿真", model_profile="byok-test")
        return [event async for event in JwaveFlowCoreAdapter.events(request)]

    events = asyncio.run(collect())
    assert called is False
    assert events[-1].event == "result"
    assert events[-1].data["error_code"] == "byok_session_missing_or_expired"
    assert events[-1].data["request_id"]
    assert events[-1].data["workflow_run_id"]


def test_jwave_flow_self_mode_rejects_gateway_model_profile(monkeypatch):
    monkeypatch.setattr("app.config.MODEL_CONFIG_MODE", "self")
    called = False

    def handler(_request):
        nonlocal called
        called = True
        return httpx.Response(500)

    monkeypatch.setattr(
        jwc.httpx,
        "AsyncClient",
        lambda **kwargs: REAL_CLIENT(transport=httpx.MockTransport(handler), **kwargs),
    )

    async def collect():
        request = ChatRequest(message="仿真", model_profile="byok-test")
        return [event async for event in JwaveFlowCoreAdapter.events(request)]

    events = asyncio.run(collect())
    assert called is False
    assert events[-1].data["error_code"] == "capability_not_supported"


def test_jwave_flow_managed_mode_passes_one_request_model_config(monkeypatch):
    captured = {}

    class Profile:
        base_url = "https://model.example/v1"
        model = "managed-model"

        @staticmethod
        def api_key():
            return "secret"

    class Task:
        temperature = 0.25
        timeout_seconds = 90

    monkeypatch.setattr(adapter_module.config, "MODEL_CONFIG_MODE", "managed")
    monkeypatch.setattr(
        adapter_module.model_profiles,
        "resolve",
        lambda _profile, _task: (Profile(), Task()),
    )
    monkeypatch.setattr(adapter_module.model_profiles, "validate_runtime_endpoint", lambda _p: None)

    async def fake_events(query, *, model_config=None):
        captured["query"] = query
        captured["model_config"] = model_config
        yield {
            "event": "result",
            "report": "ok",
            "workflow_status": "succeeded",
            "skill_usage": [],
        }

    monkeypatch.setattr(adapter_module, "jwave_flow_events", fake_events)

    async def collect():
        return [
            event
            async for event in JwaveFlowCoreAdapter.events(
                ChatRequest(message="仿真", model_profile="fast")
            )
        ]

    events = asyncio.run(collect())
    assert events[-1].event == "result"
    assert "secret" not in json.dumps([event.to_dict() for event in events])
    assert captured["model_config"] == {
        "base_url": "https://model.example/v1",
        "model": "managed-model",
        "api_key": "secret",
        "temperature": 0.25,
        "timeout_seconds": 90,
    }
