"""HTTP trust boundaries and manual lifecycle; synthetic fields are mechanics only."""
import copy
import json
import sqlite3

import pytest
from starlette.applications import Starlette
from starlette.routing import Route
from starlette.testclient import TestClient

from app import config, skill_authoring_api as api, skill_manual_worker as pump
from app.skill_drafts import DraftStore
from app.skill_validation_jobs import ValidationJobs, canonical, digest
from tests.unit.test_skill_general_library import env  # reviewed synthetic receipt fixture

P = "/skills/api/v1"


@pytest.fixture
def client(env, monkeypatch):
    monkeypatch.setattr(config, "PORTAL_ADMIN_TOKEN", "t" * 32)
    monkeypatch.setattr(config, "SKILL_GENERAL_RUNTIME_SHA256", digest(canonical(env["runtime"])))
    monkeypatch.setattr(config, "SKILL_LEARNING_ENABLED", False)
    ValidationJobs(env["store"], digest(canonical(env["runtime"]))).install_trial_profile()
    class Registry:
        def __init__(self):
            self.routes = []
        def custom_route(self, path, methods):
            def decorate(fn):
                self.routes.append(Route(path, fn, methods=methods))
                return fn
            return decorate
    registry = Registry(); api.register(registry)
    # Include legacy routes: new static prefix must not shadow the original APIs.
    from app.skill_api import register
    register(registry)
    with TestClient(Starlette(routes=registry.routes), headers={"X-Admin-Token": "t" * 32}) as client:
        yield client


def create(client, env, key="create"):
    result = client.post(P + "/drafts", json={"spec": env["spec"]}, headers={"Idempotency-Key": key})
    assert result.status_code == 201, result.text
    return result.json()


def prepared(client, env):
    draft = create(client, env)
    path = P + "/drafts/" + draft["draft_id"] + "/controlled"
    body = dict(expected_revision=draft["revision"], apply=False)
    preview = client.post(path, json=body)
    assert preview.status_code == 200 and preview.json()["changed"]
    assert client.get(P + "/drafts/" + draft["draft_id"]).json()["version"] == 1
    body["apply"] = True
    applied = client.post(path, json=body)
    assert applied.status_code == 200, applied.text
    return applied.json()["draft"]


def test_manual_full_lifecycle_and_edit_cannot_inherit_validation(client, env):
    draft = prepared(client, env)
    path = P + "/drafts/" + draft["draft_id"]
    body = dict(expected_revision=draft["revision"], draft_version=draft["current_version"])
    submitted = client.post(path + "/validations", json=body, headers={"Idempotency-Key": "validate"})
    assert submitted.status_code == 202, submitted.text
    replay = client.post(path + "/validations", json=body, headers={"Idempotency-Key": "validate"})
    assert replay.json()["job_id"] == submitted.json()["job_id"]
    jobpath = P + "/jobs/" + submitted.json()["job_id"]
    assert client.get(jobpath + "/result").status_code == 409
    pump.tick(env["library"], "manual-test")
    job = client.get(jobpath).json()
    assert job["state"] == "validated", job
    assert "request_nonce" not in json.dumps(job) and "spec" not in job
    result = client.get(jobpath + "/result")
    assert result.status_code == 200, result.text
    assert result.json()["has_result"] and result.json()["comparison"]["numeric_pass"]
    assert result.json()["phase_certified"] is False
    assert result.json()["quality_level"] is None
    field = client.get(jobpath + "/field")
    assert field.status_code == 200 and field.content[:6] == b"\x93NUMPY"
    assert field.headers["cache-control"] == "no-store"
    import io
    import numpy as np
    array = np.load(io.BytesIO(field.content), allow_pickle=False)
    assert array.shape == (128, 128) and np.iscomplexobj(array)
    accepted = client.post(jobpath + "/admit", json={"expected_revision": job["revision"]})
    assert accepted.status_code == 200 and accepted.json()["admitted"], accepted.text
    assert client.get(jobpath + "/result").json()["quality_level"] == "Q4"
    current = client.get(path).json()
    changed = copy.deepcopy(current["spec"]); changed["description"] += " changed"
    edited = client.put(path, json={"expected_revision": current["revision"], "spec": changed})
    assert edited.status_code == 200 and edited.json()["version"] == 3
    assert len(env["calls"]) == 1
    # No admission for the edited version until a new independently validated job exists.
    with env["store"]._read_db() as db:
        assert db.execute("SELECT count(*) FROM skill_validation_admissions").fetchone()[0] == 1


@pytest.mark.parametrize("method,path", [("get", "/drafts"), ("post", "/drafts"), ("get", "/jobs/unknown"),
    ("get", "/jobs/unknown/result"), ("get", "/drafts/unknown"), ("get", "/drafts/unknown/jobs")])
def test_sensitive_reads_and_writes_require_admin(client, method, path):
    response = getattr(client, method)(P + path, headers={"X-Admin-Token": "invalid"})
    assert response.status_code == 403 and response.json()["error"]["code"] == "admin_required"
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("raw,status", [(b'{"spec":{},"spec":{}}', 400), (b'{"spec":NaN}', 400),
    (b'{"spec":1e999}', 400), (b'x' * 64001, 413), (b'[' * 18 + b']' * 18, 400), (b'\xff', 400)])
def test_bounded_json_ingress(client, raw, status):
    response = client.post(P + "/drafts", content=raw)
    assert response.status_code == status
    assert response.json()["request_id"]


def test_client_authority_rejected_and_field_error_located(client, env):
    bad = copy.deepcopy(env["spec"]); bad["name"] = ""
    response = client.post(P + "/drafts", json={"spec": bad})
    assert response.status_code == 422
    assert response.json()["error"]["fields"][0]["pointer"] == "/spec/name"
    for extra in ({"origin": "automatic"}, {"actor_id": "internal-auto-learning"}, {"quality_level": "Q4"}):
        response = client.post(P + "/drafts", json={"spec": env["spec"], **extra})
        assert response.status_code in (400, 422)
    bad = copy.deepcopy(env["spec"]); bad["quality_level"] = "Q4"
    assert client.post(P + "/drafts", json={"spec": bad}).status_code == 422


def test_cas_idempotency_list_export_archive_and_static_routing(client, env):
    draft = create(client, env)
    replay = client.post(P + "/drafts", json={"spec": env["spec"]}, headers={"Idempotency-Key": "create"})
    assert replay.status_code == 200 and replay.json()["draft_id"] == draft["draft_id"]
    bad = copy.deepcopy(env["spec"]); bad["name"] += " other"
    assert client.post(P + "/drafts", json={"spec": bad}, headers={"Idempotency-Key": "create"}).status_code == 409
    path = P + "/drafts/" + draft["draft_id"]
    assert client.put(path, json={"spec": bad, "expected_revision": 99}).status_code == 409
    assert client.get(P + "/drafts").json()["items"][0]["spec"] == draft["spec"]
    assert client.get(P + "/spec-schema").status_code == 200
    assert client.get("/skills/api").status_code == 200
    assert client.get("/skills/authoring").status_code == 200
    archived = client.post(path + "/archive", json={"expected_revision": draft["revision"]})
    assert archived.json()["state"] == "archived"


def test_trial_cannot_admit_and_cancel_does_not_dispatch(client, env):
    draft = create(client, env)
    response = client.post(P + "/drafts/" + draft["draft_id"] + "/trials",
        json={"expected_revision": draft["revision"], "draft_version": draft["version"]}, headers={"Idempotency-Key": "trial"})
    assert response.status_code == 202, response.text
    path = P + "/jobs/" + response.json()["job_id"]
    assert client.post(path + "/admit", json={"expected_revision": 1}).status_code == 409
    assert client.post(path + "/cancel", json={"expected_revision": 1}).json()["state"] == "cancelled"
    pump.tick(env["library"], "worker")
    assert not env["calls"]


def test_disabled_no_storage_initialization(client, env, monkeypatch, tmp_path):
    from app import skill_library_service
    from app.skill_store import SkillStore
    missing = SkillStore(tmp_path / "missing.db")
    monkeypatch.setattr(skill_library_service, "STORE", missing)
    monkeypatch.setattr(config, "SKILL_AUTHORING_ENABLED", False)
    assert client.get(P + "/drafts").status_code == 404
    assert not missing.path.exists()


def test_other_actor_is_hidden_and_errors_do_not_leak_input(client, env, monkeypatch):
    d = DraftStore(env["store"]).create(canonical(env["spec"]), actor_id="someone-else", idempotency_key="other")
    assert client.get(P + "/drafts/" + d["draft_id"]).status_code == 404
    def broken(*args, **kwargs):
        raise sqlite3.OperationalError("secret-key C:/private/code.py")
    monkeypatch.setattr(DraftStore, "list_drafts", broken)
    response = client.get(P + "/drafts")
    assert response.status_code == 503 and "secret-key" not in response.text and "private" not in response.text
