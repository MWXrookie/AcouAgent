import hashlib
import json
import pytest
from app.controlled_request import build_request
from scripts.validation import plane_wave_run_binding as binding

def test_fixed_request_derives_server_owned_code_and_configuration():
    r = build_request('{"version":1,"profile":"val1-plane-wave-v2"}')
    assert binding.digest(r["code"]) == r["code_sha256"] == binding.RECIPE_SHA256
    canonical = json.dumps(r["configuration"],sort_keys=True,separators=(",",":"))
    assert hashlib.sha256(canonical.encode()).hexdigest() == r["configuration_sha256"]
    assert r["timeout_seconds"] == 30
    assert not r["parameters_bound"] and not r["trusted_run"]
    r["configuration"]["cfl"] = 9
    assert build_request('{"version":1,"profile":"val1-plane-wave-v2"}')["configuration"]["cfl"] == .1

@pytest.mark.parametrize("payload", [
    '{"version":true,"profile":"val1-plane-wave-v2"}',
    '{"version":1,"profile":"other"}',
    '{"version":1,"profile":"val1-plane-wave-v2","code":"print(1)"}',
    '{"version":1,"profile":"val1-plane-wave-v2","timeout":300}',
    '{"version":1,"profile":"val1-plane-wave-v2","parameters":{}}',
    '{"version":1,"version":1,"profile":"val1-plane-wave-v2"}',
    '{"version":NaN,"profile":"val1-plane-wave-v2"}', '[]', '{}', 'x'*513, None,
])
def test_request_rejects_override_and_malformed_input(payload):
    with pytest.raises(ValueError):
        build_request(payload)
