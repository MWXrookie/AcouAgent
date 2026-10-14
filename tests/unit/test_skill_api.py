import sqlite3

import pytest
from starlette.applications import Starlette
from starlette.routing import Route
from starlette.testclient import TestClient

from app import config, dashboard, skill_api, skill_library_service as service
from app.skill_store import SkillStore
from tests.unit.test_skill_store import candidate


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SKILL_LIBRARY_ENABLED", False)
    monkeypatch.setattr(config, "PORTAL_ADMIN_TOKEN", "t"*32)
    monkeypatch.setattr(service, "STORE", SkillStore(tmp_path / "skills.db"))
    monkeypatch.setattr(dashboard, "DB_PATH", str(tmp_path / "history.db"))
    dashboard.init_db()
    class Registry:
        routes = []
        def custom_route(self, path, methods):
            def decorate(fn):
                self.routes.append(Route(path, fn, methods=methods))
                return fn
            return decorate
    registry = Registry()
    skill_api.register(registry)
    with TestClient(Starlette(routes=registry.routes)) as c:
        yield c


def test_disabled_feature_never_creates_skill_database(client):
    assert client.get("/skills/api").status_code == 404
    assert client.post("/skills/api/unknown", json={}).status_code == 404
    assert not service.STORE.path.exists()


def test_enabled_empty_list_is_read_only_and_bounded(client, monkeypatch):
    monkeypatch.setattr(config, "SKILL_LIBRARY_ENABLED", True)
    result = client.get("/skills/api").json()
    assert not result["available"] and result["items"] == []
    assert not service.STORE.path.exists()
    for query in ("limit=-1", "limit=0", "limit=51", "limit=bad", "archived=bad"):
        assert client.get("/skills/api?" + query).status_code == 400


def test_archive_restore_and_feedback_require_admin_and_preserve_audit(client, monkeypatch):
    monkeypatch.setattr(config, "SKILL_LIBRARY_ENABLED", True)
    skill = candidate(service.STORE)
    revision = service.STORE.get_skill(skill)["updated_at"]
    body = {"action": "archive", "expected_updated_at": revision, "reason": "manual review"}
    url = "/skills/api/" + skill
    assert client.post(url, json=body).status_code == 403
    assert service.STORE.get_skill(skill)["archived"] == 0
    headers = {"X-Admin-Token": "t"*32}
    response = client.post(url, json=body, headers=headers)
    assert response.status_code == 200 and response.json()["archived"]
    assert client.get("/skills/api").json()["items"] == []
    assert len(client.get("/skills/api?archived=true").json()["items"]) == 1
    body.update(action="restore", expected_updated_at=response.json()["updated_at"])
    restored = client.post(url, json=body, headers=headers)
    assert restored.status_code == 200 and restored.json()["status"] == "candidate"
    feedback = client.post(url, headers=headers,
        json={"action":"negative_feedback","run_id":"run-one","reason":"wrong result"})
    assert feedback.status_code == 200 and feedback.json()["recorded"]
    with sqlite3.connect(service.STORE.path) as db:
        assert db.execute("SELECT COUNT(*) FROM skill_feedback").fetchone()[0] == 1
    # A replay is idempotent and must not increment failures twice.
    replay = client.post(url, headers=headers,
        json={"action":"negative_feedback","run_id":"run-one","reason":"wrong result"})
    assert replay.json()["duplicate"] and service.STORE.get_skill(skill)["failure_count"] == 1


def test_api_rejects_oversize_unknown_fields_activation_and_missing_admin_configuration(client, monkeypatch):
    monkeypatch.setattr(config, "SKILL_LIBRARY_ENABLED", True)
    headers = {"X-Admin-Token":"t"*32}
    for body in ({"action":"activate"}, {"action":"archive","path":"/tmp/other.db"},
                 {"action":"negative_feedback","run_id":"unrelated","reason":"wrong"}):
        assert client.post("/skills/api/unknown", headers=headers, json=body).status_code == 400
    assert client.post("/skills/api/unknown", headers=headers, content=b"x"*4097).status_code == 413
    assert client.post("/skills/api/unknown", headers=headers, content=b"[").status_code == 400
    monkeypatch.setattr(config, "PORTAL_ADMIN_TOKEN", "")
    assert client.post("/skills/api/unknown", headers=headers, json={}).status_code == 403


def test_metadata_response_excludes_code_and_physics_payload(client, monkeypatch):
    monkeypatch.setattr(config, "SKILL_LIBRARY_ENABLED", True)
    candidate(service.STORE)
    result = client.get("/skills/api").json()
    assert result["available"] and result["trust_verified"] is False
    assert len(result["items"]) == 1
    assert "code_template" not in result["items"][0]
    assert "physics_evidence" not in result["items"][0]


def test_feature_flags_default_to_false_without_environment_opt_in(monkeypatch):
    import runpy
    from pathlib import Path
    flags = ("SKILL_LIBRARY_ENABLED", "SKILL_RETRIEVAL_ENABLED",
             "SKILL_INJECTION_ENABLED", "SKILL_LEARNING_ENABLED")
    for flag in flags:
        monkeypatch.delenv(flag, raising=False)
    values = runpy.run_path(str(Path(config.__file__)))
    assert all(values[flag] is False for flag in flags)


def test_management_refuses_write_when_admin_attempt_audit_is_unavailable(client, monkeypatch):
    monkeypatch.setattr(config, "SKILL_LIBRARY_ENABLED", True)
    skill = candidate(service.STORE)
    before = service.STORE.get_skill(skill)
    def broken(*args, **kwargs):
        raise sqlite3.OperationalError("synthetic failure")
    monkeypatch.setattr(dashboard, "record_admin_event", broken)
    result = client.post("/skills/api/" + skill, headers={"X-Admin-Token":"t"*32},
        json={"action":"archive","expected_updated_at":before["updated_at"],"reason":"review"})
    assert result.status_code == 503
    assert service.STORE.get_skill(skill) == before


def test_authorized_invalid_and_denied_attempts_have_admin_audit(client, monkeypatch):
    monkeypatch.setattr(config, "SKILL_LIBRARY_ENABLED", True)
    url = "/skills/api/unknown"
    assert client.post(url, json={}).status_code == 403
    assert client.post(url, headers={"X-Admin-Token":"t"*32}, json={"action":"activate"}).status_code == 400
    with sqlite3.connect(dashboard.DB_PATH) as db:
        allowed = [row[0] for row in db.execute("SELECT allowed FROM admin_events ORDER BY id")]
    assert allowed == [0, 1]


def test_detail_and_audit_read_do_not_initialize_or_grant_trust(client, monkeypatch):
    for suffix in ("", "/events"):
        assert client.get("/skills/api/missing" + suffix).status_code == 404
    monkeypatch.setattr(config, "SKILL_LIBRARY_ENABLED", True)
    for suffix in ("", "/events"):
        assert client.get("/skills/api/missing" + suffix).json()["available"] is False
    assert not service.STORE.path.exists()
    skill = candidate(service.STORE)
    before = service.STORE.path.read_bytes()
    detail = client.get("/skills/api/" + skill).json()
    assert detail["available"] and not detail["trust_verified"]
    assert detail["item"]["selected_version"]["version"] == 1
    assert detail["item"]["reuse_statistics"] is None
    assert "physics_evidence" not in detail["item"]["selected_version"]
    assert client.get("/skills/api/" + skill + "?version=999").json()["item"] is None
    events = client.get("/skills/api/" + skill + "/events?limit=1").json()
    assert len(events["items"]) == 1 and events["next_after"] > 0
    assert not events["items"][0]["content_hidden"]
    assert service.STORE.path.read_bytes() == before
    for query in ("version=0", "version=bad", "version=2147483648", "path=other"):
        assert client.get("/skills/api/" + skill + "?" + query).status_code == 400
    for query in ("after=-1", "after=9223372036854775808", "limit=51", "limit=0", "actor=x"):
        assert client.get("/skills/api/" + skill + "/events?" + query).status_code == 400


def test_legacy_sensitive_audit_is_hidden_and_pagination_progresses(client, monkeypatch):
    monkeypatch.setattr(config, "SKILL_LIBRARY_ENABLED", True)
    skill = candidate(service.STORE)
    with sqlite3.connect(service.STORE.path) as db:
        for detail in ('{"password":"sensitive"}', '{"reason":"' + "x"*5000 + '"}', '{"reason":"safe"}'):
            db.execute("INSERT INTO skill_events(skill_id,event_type,detail,created_at) VALUES (?,?,?,?)",
                       (skill, "legacy", detail, "2026-10-08"))
    cursor, items = 0, []
    for _ in range(5):
        page = client.get(f"/skills/api/{skill}/events?limit=1&after={cursor}").json()
        items.extend(page["items"])
        assert page["next_after"] > cursor
        cursor = page["next_after"]
        if not page["has_more"]:
            break
    assert len(items) == 4
    assert [x["content_hidden"] for x in items] == [False, True, True, False]
    assert "sensitive" not in str(items)


def test_detail_rejects_tampered_code_and_oversize_legacy_metadata(client, monkeypatch):
    monkeypatch.setattr(config, "SKILL_LIBRARY_ENABLED", True)
    skill = candidate(service.STORE)
    with sqlite3.connect(service.STORE.path) as db:
        db.execute("UPDATE skill_versions SET code_template='pass # changed' WHERE skill_id=?", (skill,))
    assert not client.get("/skills/api/" + skill).json()["available"]
    with sqlite3.connect(service.STORE.path) as db:
        db.execute("UPDATE simulation_skills SET state_text=? WHERE skill_id=?", ("x"*2049, skill))
    assert not client.get("/skills/api/" + skill).json()["available"]


def test_audit_page_high_watermark_excludes_later_appends(client, monkeypatch):
    monkeypatch.setattr(config, "SKILL_LIBRARY_ENABLED", True)
    skill = candidate(service.STORE)
    url = f"/skills/api/{skill}/events"
    first = client.get(url + "?limit=1").json()
    watermark = first["snapshot_until"]
    with sqlite3.connect(service.STORE.path) as db:
        db.execute("INSERT INTO skill_events(skill_id,event_type,detail,created_at) VALUES (?,?,?,?)",
                   (skill, "later", "{}", "2026-10-08"))
    next_page = client.get(url + f"?after={first['next_after']}&until={watermark}").json()
    assert next_page["items"] == [] and not next_page["has_more"]
    assert len(client.get(url).json()["items"]) == 2
    assert client.get(url + "?after=2&until=1").status_code == 400


def test_skill_page_is_default_disabled_and_served_with_isolation_headers(client, monkeypatch):
    assert client.get("/skills").status_code == 404
    monkeypatch.setattr(config, "SKILL_LIBRARY_ENABLED", True)
    response = client.get("/skills")
    assert response.status_code == 200
    assert "技能库" in response.text and "暂无 Wilson" not in response.text
    assert response.headers["cache-control"] == "no-store"
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    assert not service.STORE.path.exists()


def test_detail_selected_version_is_explicit_and_unsafe_legacy_source_fails_closed(client, monkeypatch):
    import hashlib
    monkeypatch.setattr(config, "SKILL_LIBRARY_ENABLED", True)
    skill = candidate(service.STORE)
    with sqlite3.connect(service.STORE.path) as db:
        db.execute("""INSERT INTO skill_versions(skill_id,version,normalized_params,code_template,code_hash,
            postconditions,physics_evidence,source_run_id,validator_version,created_at,reference_skill_ids)
            SELECT skill_id,2,normalized_params,?, ?,postconditions,physics_evidence,source_run_id,
            validator_version,created_at,reference_skill_ids FROM skill_versions WHERE skill_id=? AND version=1""",
            ("pass # v2", hashlib.sha256(b"pass # v2").hexdigest(), skill))
    url = "/skills/api/" + skill
    assert client.get(url).json()["item"]["selected_version"]["version"] == 2
    assert client.get(url + "?version=1").json()["item"]["selected_version"]["version"] == 1
    response = client.get(url)
    assert response.headers["cache-control"] == "no-store"
    with sqlite3.connect(service.STORE.path) as db:
        db.execute("UPDATE skill_versions SET source_run_id=? WHERE skill_id=? AND version=2", ("x"*129, skill))
    assert not client.get(url).json()["available"]
    assert client.get(url + "?version=1").json()["available"]


@pytest.mark.parametrize("legacy", [True, False])
def test_event_read_is_bounded_without_changing_audit(client, monkeypatch, legacy):
    monkeypatch.setattr(config, "SKILL_LIBRARY_ENABLED", True)
    skill = candidate(service.STORE)
    # Legacy databases retain the VM cutoff. Current databases must use the
    # bounded history index rather than failing just because history is large.
    with sqlite3.connect(service.STORE.path) as db:
        if legacy:
            db.execute("DROP INDEX idx_skill_event_history")
        else:
            plan = db.execute("EXPLAIN QUERY PLAN SELECT id FROM skill_events WHERE skill_id=? ORDER BY id DESC LIMIT 50", ("missing",)).fetchall()
            assert any("idx_skill_event_history" in row[3] for row in plan)
        db.executemany("INSERT INTO skill_events(skill_id,event_type,detail,created_at) VALUES (?,?,?,?)",
                       [(skill, "observed", "{}", "fixture")] * 400000)
    with sqlite3.connect(service.STORE.path) as db:
        count = db.execute("SELECT COUNT(*) FROM skill_events").fetchone()[0]
    result = client.get("/skills/api/missing/events").json()
    assert result["available"] is (not legacy) and result["items"] == []
    with sqlite3.connect(service.STORE.path) as db:
        assert db.execute("SELECT COUNT(*) FROM skill_events").fetchone()[0] == count
        assert bool(db.execute("SELECT 1 FROM sqlite_master WHERE name='idx_skill_event_history'").fetchone()) is (not legacy)


def test_feedback_confirm_withdraw_and_read_state(client, monkeypatch):
    monkeypatch.setattr(config, "SKILL_LIBRARY_ENABLED", True)
    sid=candidate(service.STORE)
    url="/skills/api/"+sid
    body={"action":"confirm_feedback","run_id":"run-one","request_id":"confirm-1","reason":"test"}
    assert client.post(url,json=body).status_code == 403
    headers={"X-Admin-Token":"t"*32}
    first=client.post(url,json=body,headers=headers)
    assert first.status_code == 200 and first.json()["recorded"]
    assert client.post(url,json=body,headers=headers).json()["duplicate"]
    read=client.get(url+"/feedback?run_id=run-one")
    assert read.json()["confirmed"] and not read.json()["identity_authenticated"]
    assert read.headers["cache-control"] == "no-store"
    body.update(action="withdraw_feedback",request_id="withdraw-1")
    assert client.post(url,json=body,headers=headers).status_code == 200
    assert not client.get(url+"/feedback?run_id=run-one").json()["confirmed"]
    body["actor"]="forged"
    assert client.post(url,json=body,headers=headers).status_code == 400
    assert client.get(url+"/feedback?run_id=run-one&run_id=other").status_code == 400


def test_feedback_read_disabled_missing_and_strict_json(client, monkeypatch):
    assert client.get("/skills/api/missing/feedback?run_id=run").status_code == 404
    monkeypatch.setattr(config,"SKILL_LIBRARY_ENABLED",True)
    assert not client.get("/skills/api/missing/feedback?run_id=run").json()["available"]
    assert not service.STORE.path.exists()
    for text in ('{"action":"archive","action":"confirm_feedback"}','{"action":NaN}'):
        response=client.post("/skills/api/missing",content=text,headers={"X-Admin-Token":"t"*32})
        assert response.status_code == 400
    assert not service.STORE.path.exists()


@pytest.mark.parametrize("flag",[1,"true",None])
def test_master_switch_requires_boolean_true_for_all_routes(client,monkeypatch,flag):
    monkeypatch.setattr(config,"SKILL_LIBRARY_ENABLED",flag)
    for path in ("/skills","/skills/api","/skills/api/example",
                 "/skills/api/example/events","/skills/api/example/feedback?run_id=run"):
        assert client.get(path).status_code==404
    assert client.post("/skills/api/example",json={}).status_code==404
    assert not service.STORE.path.exists()


def test_revalidation_admin_route_only_returns_questioned_skill_to_candidate(client,monkeypatch):
    monkeypatch.setattr(config,"SKILL_LIBRARY_ENABLED",True)
    sid=candidate(service.STORE)
    with service.STORE._db_existing_write() as db:
        db.execute("UPDATE simulation_skills SET status='questioned' WHERE skill_id=?",(sid,))
        latest=db.execute("SELECT max(id) FROM skill_events WHERE skill_id=?",(sid,)).fetchone()[0]
    item=client.get("/skills/api/"+sid).json()["item"]
    assert item["audit_revision"]==latest
    body=dict(action="request_revalidation",expected_updated_at=item["updated_at"],
        expected_event_id=item["audit_revision"],reason="review again")
    url="/skills/api/"+sid
    assert client.post(url,json=body).status_code==403
    response=client.post(url,json=body,headers={"X-Admin-Token":"t"*32})
    assert response.status_code==200 and response.json()["status"]=="candidate"
    assert response.json()["quality_level"]=="Q2" and not response.json()["trusted_run"]
    assert client.post(url,json=body,headers={"X-Admin-Token":"t"*32}).status_code==400


def test_detail_audit_revision_rejects_same_timestamp_concurrent_revalidation(client,monkeypatch):
    monkeypatch.setattr(config,"SKILL_LIBRARY_ENABLED",True)
    sid=candidate(service.STORE)
    with service.STORE._db_existing_write() as db:
        db.execute("UPDATE simulation_skills SET status='questioned' WHERE skill_id=?",(sid,))
    url="/skills/api/"+sid
    item=client.get(url).json()["item"]
    body=dict(action="request_revalidation",expected_updated_at=item["updated_at"],
        expected_event_id=item["audit_revision"],reason="review again")
    # A new statement may not update the skill timestamp. Audit identity must
    # still force a stale page to refresh before resetting the quality epoch.
    service.STORE.record_feedback_statement(sid,"run-one",confirmed=True,
        request_id="concurrent",actor="portal-admin",reason="statement only")
    response=client.post(url,json=body,headers={"X-Admin-Token":"t"*32})
    assert response.status_code==400
    current=client.get(url).json()["item"]
    assert current["status"]=="questioned"
    assert current["updated_at"]==item["updated_at"] and current["audit_revision"]>item["audit_revision"]
    with service.STORE._read_db() as db:
        assert not db.execute("SELECT 1 FROM skill_events WHERE event_type='revalidation_requested'").fetchone()
