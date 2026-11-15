import json
import uuid

import pytest

from cores.jwave_flow.workflow.skill_client import SkillClient, SkillClientError


class Response:
    def __init__(self, payload):
        self.payload = payload
        self.request_url = ""

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def geturl(self):
        return self.request_url

    def read(self, limit):
        return json.dumps(self.payload).encode("utf-8")


class Opener:
    def __init__(self):
        self.requests = []
        self.responses = []

    def open(self, request, timeout):
        self.requests.append((request, timeout))
        response = self.responses.pop(0)
        response.request_url = request.full_url
        return response


def client():
    value = SkillClient("http://gateway:8001/internal/jwave-flow/skills", "s" * 32, 2)
    opener = Opener()
    value._opener = opener
    return value, opener


def test_skill_client_resolve_and_execute_contract():
    value, opener = client()
    run_id = str(uuid.uuid4())
    opener.responses.append(Response({
        "mode": "reference",
        "skill_ticket": "ticket",
        "skills": [],
        "skill_usage": [],
        "guidance_context": "",
    }))
    resolved = value.resolve(
        workflow_run_id=run_id,
        original_context="context",
        scenario_type="point_source_2d",
        parameters_json="{\"frequency_hz\":1}",
    )
    assert resolved["mode"] == "reference"
    request, timeout = opener.requests[0]
    assert timeout == 2
    assert request.get_header("X-jwave-flow-skill-token") == "s" * 32
    assert json.loads(request.data)["workflow_run_id"] == run_id

    opener.responses.append(Response({
        "execution_reuse_verified": True,
        "run_id": "run-1",
        "result": {"stdout": "ok"},
    }))
    executed = value.execute_reference(
        workflow_run_id=run_id,
        skill_ticket="ticket",
        original_context="context",
        scenario_type="point_source_2d",
        parameters_json="{\"frequency_hz\":1}",
    )
    assert executed["execution_reuse_verified"] is True
    assert opener.requests[1][1] == 40


def test_skill_client_fails_closed_on_invalid_mode():
    value, opener = client()
    opener.responses.append(Response({"mode": "trusted"}))
    with pytest.raises(SkillClientError, match="skill_port_invalid_mode"):
        value.resolve(
            workflow_run_id=str(uuid.uuid4()),
            original_context="context",
            scenario_type="point_source_2d",
            parameters_json="{}",
        )


def test_skill_client_rejects_non_exact_url_and_short_token():
    with pytest.raises(ValueError, match="invalid_skill_port_url"):
        SkillClient("http://user:pass@gateway/path", "s" * 32)
    with pytest.raises(ValueError, match="skill_port_token_too_short"):
        SkillClient("http://gateway", "short")
