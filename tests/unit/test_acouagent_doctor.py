import json

from scripts.diagnostics import acouagent_doctor as doctor


def _valid_env():
    return {
        "MCP_AUTH_TOKEN": "a" * 32,
        "EXECUTOR_SHARED_TOKEN": "b" * 32,
        "ORCHESTRATION_BACKEND": "local",
        "MODEL_BASE_URL": "",
        "MODEL_NAME": "",
        "MODEL_API_KEY": "",
        "MODEL_PROFILES_JSON": "",
    }


def test_configuration_check_never_returns_secret_values(tmp_path):
    (tmp_path / ".env").write_text("present", encoding="utf-8")
    values = _valid_env()
    values["MODEL_BASE_URL"] = "https://example.invalid"
    values["MODEL_NAME"] = "test-model"
    values["MODEL_API_KEY"] = "secret-that-must-not-appear"
    checks = doctor._configuration_checks(tmp_path, values)
    encoded = json.dumps([item.__dict__ for item in checks])
    assert "secret-that-must-not-appear" not in encoded
    assert not any(item.status == "fail" for item in checks)


def test_configuration_rejects_placeholders_and_reused_tokens(tmp_path):
    (tmp_path / ".env").write_text("present", encoding="utf-8")
    values = _valid_env()
    values["MCP_AUTH_TOKEN"] = values["EXECUTOR_SHARED_TOKEN"] = "replace-with-" + "x" * 32
    checks = doctor._configuration_checks(tmp_path, values)
    tokens = next(item for item in checks if item.check_id == "configuration.tokens")
    assert tokens.status == "fail"


def test_configuration_rejects_partial_or_invalid_model_profiles(tmp_path):
    (tmp_path / ".env").write_text("present", encoding="utf-8")
    partial = _valid_env()
    partial["MODEL_BASE_URL"] = "https://example.invalid"
    check = next(
        item for item in doctor._configuration_checks(tmp_path, partial)
        if item.check_id == "configuration.model"
    )
    assert check.status == "fail"

    invalid_json = _valid_env()
    invalid_json["MODEL_PROFILES_JSON"] = "{not-json"
    check = next(
        item for item in doctor._configuration_checks(tmp_path, invalid_json)
        if item.check_id == "configuration.model"
    )
    assert check.status == "fail"
    assert "not-json" not in check.message


def test_auto_mode_is_secret_safe_and_degrades_to_prepare(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text(
        "MCP_AUTH_TOKEN=" + "a" * 32 + "\n"
        "EXECUTOR_SHARED_TOKEN=" + "b" * 32 + "\n"
        "MODEL_BASE_URL=https://example.invalid\n"
        "MODEL_NAME=test-model\n"
        "MODEL_API_KEY=private-model-key\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(doctor, "_host_checks", lambda _root: [])
    monkeypatch.setattr(doctor, "_docker_checks", lambda _root: [])
    monkeypatch.setattr(doctor, "_request", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        doctor, "_port_check",
        lambda _values: doctor.Check("host.port_8001", "pass", "available"),
    )
    report = doctor.diagnose(tmp_path, "auto")
    assert report["mode"] == "prepare"
    assert report["overall"] == "warn"
    assert "private-model-key" not in json.dumps(report)


def test_human_renderer_contains_stable_check_ids():
    report = {
        "mode": "prepare",
        "overall": "warn",
        "checks": [{"check_id": "runtime.auto_detection", "status": "warn", "message": "offline"}],
    }
    rendered = doctor._render_human(report)
    assert "[WARN] runtime.auto_detection: offline" in rendered
