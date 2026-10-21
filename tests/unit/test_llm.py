import asyncio
import sqlite3

from app import cache_store, llm, model_client


def test_retry_uses_shared_model_transport_and_preserves_code_cleanup(monkeypatch):
    captured = {}
    monkeypatch.setattr(cache_store, "_extract_error_signature", lambda *args: "signature")
    monkeypatch.setattr(
        cache_store,
        "_query_cache",
        lambda signature: {"confidence": 0.9, "fix_hint": "use the supported API"},
    )

    def complete(profile_id, task, messages, *, run_id=None):
        captured.update(
            profile_id=profile_id,
            task=task,
            messages=messages,
            run_id=run_id,
        )
        return {"content": "```python\nprint('fixed')\n```"}

    monkeypatch.setattr(model_client, "complete_sync", complete)

    fixed = llm._llm_fix_code(
        "profile-a",
        "print('broken')",
        {"exit_code": 1, "timed_out": False, "stdout": "", "stderr": "failure"},
        run_id="run-1",
    )

    assert fixed == "print('fixed')"
    assert captured["profile_id"] == "profile-a"
    assert captured["task"] == "retry"
    assert captured["run_id"] == "run-1"
    prompt = captured["messages"][0]["content"]
    assert "use the supported API" in prompt
    assert "print('broken')" in prompt


def test_async_retry_uses_shared_prompt_and_returns_cache_signature(monkeypatch):
    captured = {}
    monkeypatch.setattr(cache_store, "_extract_error_signature", lambda *args: "signature")
    monkeypatch.setattr(cache_store, "_query_cache", lambda signature: None)

    async def complete(profile_id, task, messages, *, run_id=None):
        captured.update(
            profile_id=profile_id,
            task=task,
            messages=messages,
            run_id=run_id,
        )
        return {"content": "```python\nprint('fixed async')\n```"}

    monkeypatch.setattr(model_client, "complete", complete)

    fixed = asyncio.run(llm.fix_code(
        "profile-b",
        "print('broken')",
        {"exit_code": 1, "timed_out": False, "stdout": "", "stderr": "failure"},
        run_id="run-2",
        requirements_context="keep the pressure field marker",
    ))

    assert fixed == {"code": "print('fixed async')", "error_signature": "signature", "cache_hit": False}
    assert captured["profile_id"] == "profile-b"
    assert captured["task"] == "retry"
    assert captured["run_id"] == "run-2"
    prompt = captured["messages"][0]["content"]
    assert "keep the pressure field marker" in prompt
    assert "print('broken')" in prompt


def test_async_retry_treats_cache_lookup_failure_as_miss(monkeypatch):
    captured = {}
    monkeypatch.setattr(cache_store, "_extract_error_signature", lambda *args: "signature")

    def unavailable_cache(_signature):
        raise sqlite3.OperationalError("database is locked")

    async def complete(profile_id, task, messages, *, run_id=None):
        captured["prompt"] = messages[0]["content"]
        return {"content": "print('fixed without cache')"}

    monkeypatch.setattr(cache_store, "_query_cache", unavailable_cache)
    monkeypatch.setattr(model_client, "complete", complete)

    fixed = asyncio.run(llm.fix_code(
        "profile-b",
        "print('broken')",
        {"exit_code": 1, "timed_out": False, "stdout": "", "stderr": "failure"},
    ))

    assert fixed == {"code": "print('fixed without cache')", "error_signature": "signature", "cache_hit": False}
    assert "jwave common error cheat sheet" in captured["prompt"]


def test_retry_prompt_is_bounded_by_utf8_bytes_for_multibyte_inputs(monkeypatch):
    monkeypatch.setattr(cache_store, "_extract_error_signature", lambda *args: None)
    prompt, _ = llm._fix_prompt(
        "#" + "声" * 20_000,
        {
            "exit_code": 1,
            "timed_out": False,
            "stdout": "前" + "声" * 10_000 + "stdout-tail",
            "stderr": "前" + "错" * 10_000 + "stderr-tail",
        },
        requirements_context="需求" * 10_000,
    )

    assert len(prompt.encode("utf-8")) < 100_000
    assert "stdout-tail" in prompt and "stderr-tail" in prompt
