"""Low-cost, non-executing compatibility probe for self-hosted model profiles."""
from __future__ import annotations

import ast
import json
import threading
import time
import uuid
from typing import Any

from app import model_client

_LOCK = threading.Lock()
_PROMPT = (
    'Return only this JSON object with no markdown or explanation: '
    '{"status":"ok","code":"print(\'acouagent\')"}'
)


class ModelPreflightBusy(RuntimeError):
    """Only one paid model preflight may run in this gateway process at a time."""


def _safe_probe_document(text: str) -> dict[str, Any] | None:
    if not isinstance(text, str) or len(text.encode("utf-8")) > 4096:
        return None
    try:
        value = json.loads(text.strip())
    except (json.JSONDecodeError, UnicodeError, TypeError):
        return None
    if not isinstance(value, dict) or set(value) != {"status", "code"}:
        return None
    if value.get("status") != "ok" or not isinstance(value.get("code"), str):
        return None
    return value


def _safe_code_probe(code: object) -> bool:
    if not isinstance(code, str) or len(code.encode("utf-8")) > 256:
        return False
    try:
        tree = ast.parse(code)
    except (SyntaxError, ValueError, TypeError):
        return False
    if len(tree.body) != 1 or not isinstance(tree.body[0], ast.Expr):
        return False
    call = tree.body[0].value
    return bool(
        isinstance(call, ast.Call)
        and isinstance(call.func, ast.Name)
        and call.func.id == "print"
        and not call.keywords
        and len(call.args) == 1
        and isinstance(call.args[0], ast.Constant)
        and call.args[0].value == "acouagent"
    )


async def run(profile_id: str | None) -> dict[str, Any]:
    """Probe transport, strict JSON and a harmless code-shaped response.

    The returned document intentionally excludes endpoint, prompt, response body,
    credentials and the replayable session profile id.
    """
    if not _LOCK.acquire(blocking=False):
        raise ModelPreflightBusy("model_preflight_in_progress")
    started = time.perf_counter()
    run_id = str(uuid.uuid4())
    try:
        result = await model_client.probe(
            profile_id,
            [{"role": "user", "content": _PROMPT}],
            run_id=run_id,
        )
    finally:
        _LOCK.release()

    latency_ms = max(0, round((time.perf_counter() - started) * 1000))
    document = _safe_probe_document(result.get("content", ""))
    json_ok = document is not None
    code_ok = _safe_code_probe(document.get("code")) if document else False
    checks = [
        {"id": "transport", "passed": True, "detail": "模型端点返回有效聊天响应。"},
        {
            "id": "strict_json",
            "passed": json_ok,
            "detail": "返回内容可解析为要求的严格 JSON。" if json_ok
            else "端点可连接，但返回内容不符合严格 JSON 合同。",
        },
        {
            "id": "code_shape",
            "passed": code_ok,
            "detail": "能够返回受控的最小 Python 代码结构。" if code_ok
            else "未通过受控代码结构检查，完整仿真不可据此判定兼容。",
        },
    ]
    compatible = all(item["passed"] for item in checks)
    return {
        "ok": compatible,
        "status": "basic_compatible" if compatible else "connected_incompatible",
        "connected": True,
        "latency_ms": latency_ms,
        "profile_id": result["profile_id"],
        "provider": result["provider"],
        "model": result["model"],
        "checks": checks,
        "full_simulation_validated": False,
        "notice": "该测试会产生一次小型模型调用；完整兼容性仍需固定题、executor 与物理门验收。",
    }
