import asyncio
import json
import uuid

from starlette.requests import Request

from app import config, jwave_skill_port, skill_retrieval


class Registry:
    def __init__(self):
        self.handlers = {}

    def custom_route(self, path, methods):
        def decorator(handler):
            self.handlers[path] = handler
            return handler
        return decorator


def request(payload, token="s" * 32):
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/",
        "headers": [(b"x-jwave-flow-skill-token", token.encode("ascii"))],
    }
    sent = False

    async def receive():
        nonlocal sent
        if sent:
            return {"type": "http.disconnect"}
        sent = True
        return {"type": "http.request", "body": raw, "more_body": False}

    return Request(scope, receive)


def handlers(monkeypatch, mode="full"):
    monkeypatch.setattr(config, "JWAVE_FLOW_SKILL_MODE", mode)
    monkeypatch.setattr(config, "JWAVE_FLOW_SKILL_PORT_TOKEN", "s" * 32)
    registry = Registry()
    jwave_skill_port.register(registry)
    return registry.handlers


def body(response):
    return json.loads(response.body)


def payload():
    return {
        "request_id": str(uuid.uuid4()),
        "workflow_run_id": str(uuid.uuid4()),
        "original_context": "query\n\n参数：{}",
        "scenario_type": "point_source_2d",
        "parameters_json": "{\"frequency_hz\":2000000}",
    }


def test_skill_port_auth_and_fallback(monkeypatch):
    routes = handlers(monkeypatch)
    unauthorized = asyncio.run(
        routes["/internal/jwave-flow/skills/resolve"](request(payload(), token="x" * 32))
    )
    assert unauthorized.status_code == 401

    monkeypatch.setattr(
        skill_retrieval,
        "generation_context",
        lambda *args, **kwargs: {
            "context": kwargs["original_context"] if "original_context" in kwargs else args[0],
            "skill_usage": {"skills": [], "fallback": True, "reason": "no_applicable_skill"},
        },
    )
    monkeypatch.setattr(
        skill_retrieval,
        "guidance_context",
        lambda *args, **kwargs: {"available": False, "reason": "no_applicable_guidance"},
    )
    response = asyncio.run(routes["/internal/jwave-flow/skills/resolve"](request(payload())))
    assert response.status_code == 200
    assert body(response)["mode"] == "fallback"


def test_skill_port_selects_and_executes_reference(monkeypatch):
    routes = handlers(monkeypatch, mode="reference")
    resolve_payload = payload()
    decision_id = str(uuid.uuid4())
    intent_id = str(uuid.uuid4())
    monkeypatch.setattr(
        skill_retrieval,
        "generation_context",
        lambda *args, **kwargs: {
            "context": args[0],
            "skill_usage": {
                "skills": [{
                    "skill_id": "skill-1",
                    "version": 2,
                    "quality_level": "Q4",
                    "code": "print('must-not-leak')",
                }],
                "reason": "local_fixed_reference_delivery",
                "decision_id": decision_id,
                "generation_intent_id": intent_id,
            },
        },
    )
    resolved = asyncio.run(
        routes["/internal/jwave-flow/skills/resolve"](request(resolve_payload))
    )
    selected = body(resolved)
    assert selected["mode"] == "reference"
    assert selected["skills"][0]["skill_id"] == "skill-1"
    assert "code" not in selected["skills"][0]
    assert "must-not-leak" not in resolved.body.decode()

    mismatched = asyncio.run(
        routes["/internal/jwave-flow/skills/execute-reference"](
            request({
                "skill_ticket": selected["skill_ticket"],
                "workflow_run_id": str(uuid.uuid4()),
                "original_context": resolve_payload["original_context"],
                "scenario_type": resolve_payload["scenario_type"],
                "parameters_json": resolve_payload["parameters_json"],
            })
        )
    )
    assert mismatched.status_code == 400

    from app import dashboard, skill_local_execution

    run_id = str(uuid.uuid4())
    monkeypatch.setattr(
        skill_local_execution,
        "execute_reference",
        lambda **kwargs: {
            "execution_reuse_verified": True,
            "run_id": run_id,
            "reason": "local_reference_execution_verified",
        },
    )
    monkeypatch.setattr(
        dashboard,
        "get_execution_by_run_id",
        lambda value: {
            "exit_code": 0,
            "stdout": "pressure=1",
            "stderr": "",
            "timed_out": False,
            "duration_ms": 10,
        },
    )
    executed = asyncio.run(
        routes["/internal/jwave-flow/skills/execute-reference"](
            request({
                "skill_ticket": selected["skill_ticket"],
                "workflow_run_id": resolve_payload["workflow_run_id"],
                "original_context": resolve_payload["original_context"],
                "scenario_type": resolve_payload["scenario_type"],
                "parameters_json": resolve_payload["parameters_json"],
            })
        )
    )
    value = body(executed)
    assert value["execution_reuse_verified"] is True
    assert value["run_id"] == run_id
    assert value["result"]["stdout"] == "pressure=1"


def test_skill_port_full_mode_injects_only_guidance(monkeypatch):
    routes = handlers(monkeypatch, mode="full")
    monkeypatch.setattr(
        skill_retrieval,
        "generation_context",
        lambda *args, **kwargs: {
            "context": args[0],
            "skill_usage": {"skills": [], "fallback": True, "reason": "no_applicable_skill"},
        },
    )
    monkeypatch.setattr(
        skill_retrieval,
        "guidance_context",
        lambda *args, **kwargs: {
            "available": True,
            "reason": "same_scenario_guidance",
            "context": "{\"mode\":\"metadata_only\"}",
            "skills": [{"skill_id": "skill-2", "version": 1, "quality_level": "Q3"}],
        },
    )
    response = asyncio.run(
        routes["/internal/jwave-flow/skills/resolve"](request(payload()))
    )
    value = body(response)
    assert value["mode"] == "guided"
    assert value["guidance_context"] == "{\"mode\":\"metadata_only\"}"
    assert value["skill_usage"][0]["stage"] == "guidance_applied"
