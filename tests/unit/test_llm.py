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
