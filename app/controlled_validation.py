"""Internal fixed VAL-1 dispatch. No caller code, parameters, or grade input.

Not a public MCP tool and not automatic skill learning. The isolated runtime
contract and numeric evidence are prerequisites, not production approval.
"""
import uuid

from app import controlled_dispatch, dashboard, execution, runtime_registry_service, skill_accumulation
from scripts.validation import plane_wave_run_binding as binding


def run_controlled_plane_wave():
    """Execute only the pinned 30s recipe through the existing executor path."""
    from app.controlled_request import build_request
    request = build_request('{"version":1,"profile":"val1-plane-wave-v2"}')
    code = request["code"]
    run_id = str(uuid.uuid4())
    nonce = uuid.uuid4().hex
    from app.controlled_request_adapter import make_envelope, verify_envelope
    envelope = make_envelope(run_id, nonce)
    if not verify_envelope(envelope, expected_run_id=run_id, expected_nonce=nonce)["request_context_matched"]:
        raise ValueError("controlled_request_context_mismatch")
    if code != envelope["code"]:
        raise ValueError("controlled_request_code_changed")
    controlled_dispatch.record(dashboard.DB_PATH, run_id, nonce, envelope)
    if not controlled_dispatch.inspect_file(dashboard.DB_PATH, run_id, nonce).get("request_context_matched"):
        raise ValueError("persisted_request_context_mismatch")
    result = execution._execute_code(code, request["timeout_seconds"], request_nonce=nonce)
    dashboard.record_execution(
        tool_name="run_controlled_plane_wave.v1", code=code,
        exit_code=result.get("exit_code"), timed_out=result.get("timed_out", False),
        duration_ms=result.get("duration_ms"), stdout=result.get("stdout", ""),
        stderr=result.get("stderr", ""), run_id=run_id,
        execution_evidence=result.get("execution_evidence"))
    evidence = result.get("execution_evidence")
    usage = evidence.get("approval_usage", {}) if isinstance(evidence, dict) else {}
    completion = runtime_registry_service.record_dispatch_completion(usage, nonce, run_id)
    # Re-read server records, not the returned matched/grade flags.
    checked = binding.inspect_run(dashboard.DB_PATH, run_id)
    candidate = skill_accumulation.record_controlled_candidate(run_id)
    return {"run_id": run_id, "validation": checked, "approval_completion": completion,
            "candidate_accumulation": candidate}
