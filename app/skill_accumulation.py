"""Internal fixed-recipe candidate accumulation; never grants trust or activation."""
import sqlite3
import hashlib
import json
from app import config, controlled_dispatch, dashboard, skill_library_service
from scripts.validation import plane_wave_run_binding as binding
from scripts.validation.plane_wave_evidence import EXPECTED_CONFIGURATION


def record_controlled_candidate(run_id):
    """Re-read server history; no caller code, parameters, evidence or grades."""
    denied = {"recorded": False, "trusted_run": False, "runtime_approved": False,
              "run_bound": False, "parameters_bound": False}
    if config.SKILL_LEARNING_ENABLED is not True or config.SKILL_LIBRARY_ENABLED is not True:
        return dict(denied, reason="candidate_accumulation_disabled")
    if not isinstance(run_id, str) or not 1 <= len(run_id) <= 128:
        return dict(denied, reason="invalid_run_reference")
    checked = binding.inspect_run(dashboard.DB_PATH, run_id)
    if not all(checked.get(key) is True for key in (
            "code_contract_matched", "numeric_pass", "execution_receipt_matched",
            "runtime_contract_matched", "dispatch_context_matched",
            "provenance_prerequisites_passed")):
        return dict(denied, reason="candidate_prerequisites_failed")
    # The verifier checks installed hashes; this code is the same pinned file.
    try:
        code = (binding.DIRECTORY / "recipes/val1_plane_wave_v2.py").read_text().strip()
        if binding.digest(code) != binding.RECIPE_SHA256:
            return dict(denied, reason="candidate_prerequisites_failed")
        params = controlled_dispatch.fixed_retrieval_parameters()
        skill_id = skill_library_service.STORE.create_candidate(
            scenario_type="initial_pressure_2d", state_text="固定VAL-1高斯初始压力；仅隔离候选证据",
            normalized_params=params, code_template=code,
            preconditions={"dimensions": 2, "solver_mode": "time_domain",
                           "medium_type": "homogeneous_water", "dx_m": [0.00025, 0.00025]},
            postconditions={"numeric_prerequisites_passed": True},
            physics_evidence={"scope": "isolated_validation_only",
                "configuration_sha256": hashlib.sha256(json.dumps(EXPECTED_CONFIGURATION, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
                "stdout_sha256": checked["stdout_sha256"], "code_sha256": checked["code_sha256"],
                "validator_sha256": checked["validator_sha256"],
                "trusted_run": False, "runtime_approved": False,
                "run_bound": False, "parameters_bound": False},
            source_run_id=run_id, validator_version=checked["validator_version"],
            quality_level="Q2", idempotent_source_run=True)
        return dict(denied, recorded=True, skill_id=skill_id, reason="candidate_only")
    except (OSError, sqlite3.Error, ValueError, TypeError, KeyError):
        # Optional candidate storage must not replay execution or conceal its result.
        return dict(denied, reason="candidate_store_unavailable")


def accumulate_pending_controlled_candidates(limit=20, after_execution_id=0):
    """Bounded internal catch-up of recorded fixed runs, never arbitrary cases.

    Recover candidate storage after a temporary failure without re-executing
    simulations. Repeated scans reuse source-run idempotency. This is not a
    success-rate estimator or a route to activation.
    """
    result = {"examined": 0, "recorded": 0, "rejected": 0,
              "trusted_run": False, "runtime_approved": False,
              "run_bound": False, "parameters_bound": False, "next_after_execution_id": after_execution_id}
    if config.SKILL_LEARNING_ENABLED is not True or config.SKILL_LIBRARY_ENABLED is not True:
        return dict(result, reason="candidate_accumulation_disabled")
    if (type(limit) is not int or not 1 <= limit <= 50
            or type(after_execution_id) is not int or not 0 <= after_execution_id <= 2**63-1):
        raise ValueError("candidate_scan_limit")
    from pathlib import Path
    path = Path(dashboard.DB_PATH).resolve()
    db = None
    try:
        db = sqlite3.connect(path.as_uri()+"?mode=ro", uri=True, timeout=1)
        db.set_progress_handler(lambda: 1, 1_000_000)
        rows = db.execute("""SELECT id,run_id FROM executions
            WHERE id>? AND code_sha256=? AND record_version='gateway-execution.v1'
            ORDER BY id ASC LIMIT ?""", (after_execution_id,binding.RECIPE_SHA256,limit)).fetchall()
    except (sqlite3.Error, OSError, ValueError, TypeError):
        return dict(result, reason="execution_store_unavailable")
    finally:
        if db is not None:
            db.close()
    for execution_id,run_id in rows:
        outcome = record_controlled_candidate(run_id)
        result["examined"] += 1
        result["recorded" if outcome["recorded"] else "rejected"] += 1
        if outcome["reason"] == "candidate_store_unavailable":
            # Do not advance beyond an unwritten candidate on transient
            # storage failure. Resume with the returned cursor, not executor.
            return dict(result, reason="candidate_store_unavailable")
        result["next_after_execution_id"] = execution_id
    return dict(result, reason="candidate_scan_complete")
