import io
import json
from pathlib import Path

import pytest

from cores.jwave_flow.bridge import server as bridge


class FakeWorkflow:
    def __init__(self):
        self.closed = False

    def stream(self, query):
        assert query == "仿真 2 MHz 点声源"
        yield {"analyze": {"requirement": "二维点源", "kb_docs": [{"private": "hidden"}]}}
        yield {
            "run_code": {
                "success": True,
                "verified": True,
                "iterations": 1,
                "text": "print('ok')",
                "stdout": "pressure=1.0",
                "stderr": "",
                "trace": ["执行完成"],
            }
        }

    def close(self):
        self.closed = True


class FakeHandler:
    def __init__(self, payload):
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.headers = {"Content-Length": str(len(raw))}
        self.rfile = io.BytesIO(raw)


def test_bridge_health_exposes_model_config_mode(monkeypatch):
    monkeypatch.setenv("MODEL_CONFIG_MODE", "managed")
    payload = bridge.health_payload()
    capabilities = payload["capabilities"]
    assert payload["service"] == "jwave-flow-agent-bridge"
    assert payload["model_config_mode"] == "managed"
    assert capabilities["workflow"] is True
    assert capabilities["knowledge_base_retrieval"] is True
    assert capabilities["knowledge_base_management"] is False
    assert capabilities["model_configuration"] is True
    assert capabilities["history"] is False
    assert capabilities["memory"] is False
    assert capabilities["sessions"] is False


def test_bridge_stream_keeps_only_the_workflow_state():
    workflow = FakeWorkflow()
    events = []
    bridge._run_stream(
        "仿真 2 MHz 点声源",
        events.append,
        workflow_factory=lambda: workflow,
    )
    assert [event["type"] for event in events] == ["node", "node", "complete"]
    assert events[-1]["state"]["success"] is True
    assert events[-1]["state"]["verified"] is True
    assert events[-1]["state"]["text"] == "print('ok')"
    assert events[-1]["state"]["stdout"] == "pressure=1.0"
    assert "kb_docs" not in json.dumps(events)
    assert workflow.closed is True


def test_bridge_request_rejects_model_knowledge_and_session_fields():
    assert bridge._json_body(FakeHandler({"query": "仿真"}), "self") == {"query": "仿真"}
    model_config = {
        "base_url": "https://model.example/v1",
        "model": "managed-model",
        "api_key": "secret",
        "temperature": 0.3,
        "timeout_seconds": 120,
    }
    assert bridge._json_body(
        FakeHandler({"query": "仿真", "model_config": model_config}), "managed"
    )["model_config"]["model"] == "managed-model"
    for payload in (
        {"query": "仿真", "model": "other-model"},
        {"query": "仿真", "knowledge_base": "/tmp/other.md"},
        {"query": "仿真", "session_id": "sess-1"},
        {"query": "仿真", "memory": True},
        {"query": "仿真", "model_config": model_config},
    ):
        with pytest.raises(ValueError, match="unsupported request fields"):
            bridge._json_body(FakeHandler(payload), "self")
    with pytest.raises(ValueError, match="model_config"):
        bridge._json_body(FakeHandler({"query": "仿真"}), "managed")


def test_bridge_configuration_is_explicit_and_env_only(monkeypatch, tmp_path):
    kb = tmp_path / "kb.md"
    kb.write_text("# reviewed knowledge\n", encoding="utf-8")
    monkeypatch.setenv("JWAVE_FLOW_MODEL", "fixed-model")
    monkeypatch.setenv("JWAVE_FLOW_API_KEY", "model-key")
    monkeypatch.setenv("MODEL_CONFIG_MODE", "self")
    monkeypatch.setenv("JWAVE_FLOW_BRIDGE_TOKEN", "c" * 32)
    monkeypatch.setenv("EXECUTOR_SHARED_TOKEN", "d" * 32)
    monkeypatch.setenv("JWAVE_FLOW_KB_PATH", str(kb))
    monkeypatch.setenv("JWAVE_FLOW_EXECUTOR_URL", "http://executor:8010")
    config = bridge.BridgeConfig.from_env()
    assert config.model == "fixed-model"
    assert config.api_key == "model-key"
    assert config.kb_path == kb
    assert config.executor_url == "http://executor:8010"

    monkeypatch.setenv("JWAVE_FLOW_MODEL", "")
    with pytest.raises(RuntimeError, match="JWAVE_FLOW_MODEL"):
        bridge.BridgeConfig.from_env()

    monkeypatch.setenv("MODEL_CONFIG_MODE", "managed")
    managed_config = bridge.BridgeConfig.from_env()
    assert managed_config.model_config_mode == "managed"


def test_bridge_image_copies_no_management_or_frontend_modules():
    dockerfile = (
        Path(__file__).resolve().parents[2] / "deploy" / "jwave-flow-core.Dockerfile"
    ).read_text(encoding="utf-8")
    for forbidden in (
        "jwave_frontend",
        "jwave_flow/tui.py",
        "jwave_flow/history.py",
        "jwave_flow/memory.py",
        "jwave_flow/session_store.py",
        "jwave_flow/panels.py",
    ):
        assert forbidden not in dockerfile
    assert "cores/jwave_flow/workflow" in dockerfile
    assert "cores/jwave_flow/bridge" in dockerfile
    assert "../jwave_codeagent" not in dockerfile
