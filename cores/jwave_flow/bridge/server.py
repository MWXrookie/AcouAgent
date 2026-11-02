"""Private HTTP bridge for the jwave_flow agent workflow.

This process deliberately exposes only ``GET /health`` and ``POST /v1/run``.
Configuration and the knowledge-base path are fixed when the process starts;
there is no endpoint to change models, search/edit the knowledge base, retain a
session, write history or learn from previous runs.
"""
from __future__ import annotations

import hmac
import json
import logging
import os
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

from ..workflow.config import Settings
from ..workflow.executor import BaseExecutor, ExecutionResult
from ..workflow.graph import SimulationWorkflow

MAX_BODY_BYTES = 65_536
MAX_TEXT_CHARS = 200_000
DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8765
DEFAULT_KB_PATH = Path(__file__).resolve().parent.parent / "kb" / "jwave_kb_organized.md"
MODEL_CONFIG_MODES = {"managed", "self"}
PUBLIC_STATE_FIELDS = {
    "requirement",
    "params_json",
    "param_check_ok",
    "param_check_rounds",
    "param_issues",
    "param_warnings",
    "param_constraints",
    "param_check_report",
    "static_issues",
    "static_rounds",
    "static_report",
    "code",
    "code_final",
    "stdout",
    "stderr",
    "last_error",
    "fix_reason",
    "results",
    "outputs",
    "new_files",
    "execution_time",
    "iterations",
    "success",
    "semantic_issues",
    "selfcheck",
    "verified",
    "text",
    "trace",
    "requirement_completed",
    "completion_feedback",
    "completion_rounds",
}
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BridgeConfig:
    model_config_mode: str
    model: str
    api_key: str
    openai_base_url: str | None
    kb_path: Path
    executor_url: str
    executor_token: str
    bridge_token: str
    top_k: int = 4
    exec_timeout: int = 30

    @classmethod
    def from_env(cls) -> "BridgeConfig":
        model = os.environ.get("JWAVE_FLOW_MODEL", "").strip()
        api_key = os.environ.get("JWAVE_FLOW_API_KEY", "").strip()
        model_config_mode = os.environ.get("MODEL_CONFIG_MODE", "managed").strip().lower()
        bridge_token = os.environ.get("JWAVE_FLOW_BRIDGE_TOKEN", "").strip()
        executor_token = os.environ.get("EXECUTOR_SHARED_TOKEN", "").strip()
        kb_path = Path(os.environ.get("JWAVE_FLOW_KB_PATH", str(DEFAULT_KB_PATH))).expanduser()
        executor_url = os.environ.get(
            "JWAVE_FLOW_EXECUTOR_URL", "http://jwave-executor:8010"
        ).rstrip("/")
        base_url = os.environ.get("JWAVE_FLOW_OPENAI_BASE_URL", "").strip() or None
        if model_config_mode not in MODEL_CONFIG_MODES:
            raise RuntimeError("MODEL_CONFIG_MODE must be managed or self")
        if model_config_mode == "self" and (not model or not api_key):
            raise RuntimeError(
                "JWAVE_FLOW_MODEL and JWAVE_FLOW_API_KEY are required "
                "when MODEL_CONFIG_MODE=self"
            )
        if len(bridge_token) < 32 or len(executor_token) < 32:
            raise RuntimeError("bridge and executor tokens must each be at least 32 characters")
        parsed_executor = urlparse(executor_url)
        if (
            parsed_executor.scheme not in {"http", "https"}
            or not parsed_executor.netloc
            or parsed_executor.username
            or parsed_executor.password
            or parsed_executor.query
            or parsed_executor.fragment
        ):
            raise RuntimeError("JWAVE_FLOW_EXECUTOR_URL must be an exact http(s) URL")
        if not kb_path.is_file():
            raise RuntimeError("JWAVE_FLOW_KB_PATH must point to a readable knowledge base")
        return cls(
            model_config_mode=model_config_mode,
            model=model,
            api_key=api_key,
            openai_base_url=base_url,
            kb_path=kb_path,
            executor_url=executor_url,
            executor_token=executor_token,
            bridge_token=bridge_token,
            top_k=max(1, min(int(os.environ.get("JWAVE_FLOW_TOP_K", "4")), 12)),
            exec_timeout=max(
                1, min(int(os.environ.get("JWAVE_FLOW_EXEC_TIMEOUT", "30")), 30)
            ),
        )


def health_payload() -> dict[str, Any]:
    mode = os.environ.get("MODEL_CONFIG_MODE", "managed").strip().lower()
    if mode not in MODEL_CONFIG_MODES:
        mode = "managed"
    return {
        "status": "ok",
        "service": "jwave-flow-agent-bridge",
        "api_version": "v1",
        "model_config_mode": mode,
        "capabilities": {
            "workflow": True,
            "knowledge_base_retrieval": True,
            "knowledge_base_management": False,
            "model_configuration": mode == "managed",
            "history": False,
            "memory": False,
            "sessions": False,
        },
    }


def _public_state(state: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key in PUBLIC_STATE_FIELDS:
        if key not in state:
            continue
        value = state[key]
        if key in {"text", "code", "code_final", "stdout", "stderr"} and isinstance(value, str):
            result[key] = value[:MAX_TEXT_CHARS]
        elif key in {"trace", "param_issues", "param_warnings", "param_constraints",
                     "static_issues", "semantic_issues", "results", "outputs", "new_files"}:
            result[key] = list(value or [])[:80]
        elif key == "selfcheck" and isinstance(value, dict):
            result[key] = value
        else:
            result[key] = value
    return result


def _sandbox_executor(config: BridgeConfig):
    """Build the only execution path exposed by this bridge."""

    class SandboxExecutor(BaseExecutor):
        name = "acouagent_jwave_executor"

        def run(self, code: str) -> ExecutionResult:
            started = time.monotonic()
            body = json.dumps(
                {"code": code, "timeout_seconds": config.exec_timeout},
                ensure_ascii=False,
            ).encode("utf-8")
            request = urllib.request.Request(
                config.executor_url + "/execute",
                data=body,
                method="POST",
                headers={
                    "Content-Type": "application/json",
                    "X-Executor-Token": config.executor_token,
                },
            )
            try:
                with urllib.request.urlopen(request, timeout=config.exec_timeout + 10) as response:
                    payload = json.loads(response.read())
            except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
                return ExecutionResult(
                    stderr=f"executor transport failure: {type(exc).__name__}",
                    execution_time=time.monotonic() - started,
                    backend=self.name,
                )
            if not isinstance(payload, dict):
                return ExecutionResult(
                    stderr="executor returned a non-object response",
                    execution_time=time.monotonic() - started,
                    backend=self.name,
                )
            exit_code = payload.get("exit_code")
            stderr = str(payload.get("stderr") or "")
            if payload.get("timed_out"):
                stderr = (stderr.rstrip() + "\nexecution timed out").strip()
            elif exit_code != 0 and not stderr.strip():
                stderr = f"executor exited with code {exit_code}"
            return ExecutionResult(
                stdout=str(payload.get("stdout") or ""),
                stderr=stderr,
                results=[],
                outputs=[],
                new_files=[],
                execution_time=float(
                    payload.get("duration_ms") or (time.monotonic() - started) * 1000
                ) / 1000,
                backend=self.name,
            )

        def close(self) -> None:
            return None

    return SandboxExecutor()


def _build_workflow(config: BridgeConfig, model_config: dict[str, Any] | None):
    if config.model_config_mode == "managed":
        if not isinstance(model_config, dict):
            raise ValueError("managed model configuration is required")
        model = str(model_config["model"])
        api_key = str(model_config["api_key"])
        base_url = str(model_config["base_url"])
        temperature = float(model_config["temperature"])
        request_timeout = float(model_config["timeout_seconds"])
    else:
        model = config.model
        api_key = config.api_key
        base_url = config.openai_base_url or ""
        temperature = 0.3
        request_timeout = 120.0
    settings = Settings(
        openai_api_key=api_key,
        openai_base_url=base_url or None,
        analyst_model=model,
        coder_model=model,
        temperature=temperature,
        request_timeout=request_timeout,
        kb_path=config.kb_path,
        retrieval_backend="bm25",
        top_k=config.top_k,
        exec_timeout=config.exec_timeout,
        verbose=False,
    )
    return SimulationWorkflow(settings, executor=_sandbox_executor(config))


def _run_stream(
    query: str,
    emit: Callable[[dict[str, Any]], None],
    *,
    model_config: dict[str, Any] | None = None,
    workflow_factory: Callable[[], Any] | None = None,
) -> None:
    factory = workflow_factory or (
        lambda: _build_workflow(BridgeConfig.from_env(), model_config)
    )
    workflow = factory()
    state: dict[str, Any] = {}
    try:
        for update in workflow.stream(query):
            if not isinstance(update, dict):
                continue
            for node, patch in update.items():
                if isinstance(patch, dict):
                    state.update(patch)
                    emit({"type": "node", "node": str(node), "state": _public_state(patch)})
        emit({"type": "complete", "state": _public_state(state)})
    finally:
        close = getattr(workflow, "close", None)
        if callable(close):
            try:
                close()
            except Exception as exc:
                logger.error("jwave_flow bridge workflow close failed: %s", type(exc).__name__)


def _model_config(value: Any) -> dict[str, Any]:
    fields = {"base_url", "model", "api_key", "temperature", "timeout_seconds"}
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError("invalid model_config")
    base_url = value.get("base_url")
    model = value.get("model")
    api_key = value.get("api_key")
    temperature = value.get("temperature")
    timeout_seconds = value.get("timeout_seconds")
    parsed = urlparse(base_url) if isinstance(base_url, str) else None
    if (
        parsed is None
        or parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("invalid model_config base_url")
    if not isinstance(model, str) or not 1 <= len(model) <= 160 or any(
        char.isspace() or ord(char) < 32 for char in model
    ):
        raise ValueError("invalid model_config model")
    if not isinstance(api_key, str) or not 1 <= len(api_key) <= 4096:
        raise ValueError("invalid model_config api_key")
    if type(temperature) not in (int, float) or not 0 <= float(temperature) <= 2:
        raise ValueError("invalid model_config temperature")
    if type(timeout_seconds) not in (int, float) or not 1 <= float(timeout_seconds) <= 180:
        raise ValueError("invalid model_config timeout")
    return {
        "base_url": base_url.rstrip("/"),
        "model": model,
        "api_key": api_key,
        "temperature": float(temperature),
        "timeout_seconds": float(timeout_seconds),
    }


def _json_body(handler: BaseHTTPRequestHandler, mode: str) -> dict[str, Any]:
    length = int(handler.headers.get("Content-Length", "0"))
    if length <= 0 or length > MAX_BODY_BYTES:
        raise ValueError("invalid request size")
    raw = handler.rfile.read(length)
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("request must be an object")
    allowed_fields = {"query", "model_config"} if mode == "managed" else {"query"}
    if set(value) - allowed_fields:
        raise ValueError("unsupported request fields")
    query = value.get("query")
    if not isinstance(query, str) or not query.strip() or len(query.encode("utf-8")) > 65_536:
        raise ValueError("query must be a non-empty bounded string")
    result: dict[str, Any] = {"query": query.strip()}
    if mode == "managed":
        result["model_config"] = _model_config(value.get("model_config"))
    return result


class BridgeHandler(BaseHTTPRequestHandler):
    server_version = "jwave-flow-agent-bridge/v1"
    protocol_version = "HTTP/1.1"
    workflow_factory: Callable[[], Any] | None = None
    _slots = threading.BoundedSemaphore(2)

    def log_message(self, format: str, *args: Any) -> None:
        logger.info("bridge request: %s", format % args)

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if self.path != "/health":
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
            return
        self._send_json(HTTPStatus.OK, health_payload())

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/v1/run":
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
            return
        if not hmac.compare_digest(
            self.headers.get("X-Jwave-Flow-Bridge-Token", ""),
            str(os.environ.get("JWAVE_FLOW_BRIDGE_TOKEN", "")),
        ):
            self._send_json(HTTPStatus.UNAUTHORIZED, {"error": "unauthorized"})
            return
        try:
            mode = os.environ.get("MODEL_CONFIG_MODE", "managed").strip().lower()
            payload = _json_body(self, mode)
        except (ValueError, json.JSONDecodeError, UnicodeError):
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid_request"})
            return
        if not self._slots.acquire(blocking=False):
            self._send_json(HTTPStatus.TOO_MANY_REQUESTS, {"error": "busy"})
            return
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        emitted = False
        try:
            def emit(event: dict[str, Any]) -> None:
                nonlocal emitted
                emitted = True
                self.wfile.write(
                    (json.dumps(event, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
                )
                self.wfile.flush()

            _run_stream(
                payload["query"],
                emit,
                model_config=payload.get("model_config"),
                workflow_factory=self.workflow_factory,
            )
            if not emitted:
                emit({"type": "error", "error_code": "workflow_no_events"})
        except (BrokenPipeError, ConnectionResetError):
            logger.info("bridge client disconnected")
        except Exception as exc:
            logger.error("jwave_flow bridge workflow failed: %s", type(exc).__name__)
            try:
                emit({"type": "error", "error_code": "workflow_failed"})
            except (BrokenPipeError, ConnectionResetError):
                pass
        finally:
            self._slots.release()


def create_server(
    host: str = DEFAULT_HOST, port: int = DEFAULT_PORT
) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((host, port), BridgeHandler)
    server.daemon_threads = True
    return server


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    host = os.environ.get("JWAVE_FLOW_BRIDGE_HOST", DEFAULT_HOST)
    port = int(os.environ.get("JWAVE_FLOW_BRIDGE_PORT", str(DEFAULT_PORT)))
    BridgeConfig.from_env()
    server = create_server(host, port)
    logger.info("jwave_flow bridge listening on %s:%d", host, port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
