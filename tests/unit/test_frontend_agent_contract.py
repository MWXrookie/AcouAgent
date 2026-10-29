from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_replaceable_chat_uses_agent_client_not_legacy_endpoints():
    source = (ROOT / "app/web/chat.js").read_text(encoding="utf-8")
    assert "agentClient" in source
    for forbidden in ("fetch('/chat'", 'fetch("/chat"', "fetch('/api/model-profiles'", "fetch('/api/byok/session'", "fetch('/api/model-profiles/test'"):
        assert forbidden not in source


def test_static_chat_assets_are_present_and_keep_api_base_configurable():
    html = (ROOT / "app/web/chat.html").read_text(encoding="utf-8")
    assert 'name="acouagent-api-base"' in html
    assert "/assets/agent-client.js" in html
    assert "/assets/chat.js" in html
    assert (ROOT / "app/web/chat.css").is_file()
