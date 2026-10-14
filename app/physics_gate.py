"""Deterministic evidence levels for simulation results.

This is deliberately conservative: it establishes Q0/Q1/Q2 evidence without
claiming that an arbitrary generated acoustic field has passed physics.  The
scenario-specific Q3/Q4 checks are added alongside validated benchmarks.
"""

from __future__ import annotations

import json
import math
from typing import Any


VALID_LEVELS = ("Q0", "Q1", "Q2", "Q3", "Q4")


def _check(check_id: str, passed: bool, detail: str) -> dict[str, Any]:
    return {"check_id": check_id, "passed": passed, "detail": detail}


def _number(value):
    if type(value) not in (int, float):
        raise ValueError("numeric_type_required")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("finite_number_required")
    return result


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_parameter")
        result[key] = value
    return result


def _reject_constant(value):
    raise ValueError("nonfinite_parameter")


def assess_quality(
    *,
    exit_code: int | None,
    timed_out: bool,
    analysis: dict[str, Any],
    params_json: str = "{}",
) -> dict[str, Any]:
    """Return a structured, intentionally non-overclaiming evidence result."""
    checks: list[dict[str, Any]] = []
    executed = type(exit_code) is int and type(timed_out) is bool and type(analysis) is dict
    checks.append(_check("execution_recorded", executed, "execution result is present"))
    if not executed:
        return {"quality_level": "Q0", "physics_checks": checks}

    finite = analysis.get("verdict") == "normal"
    try:
        peak = _number(analysis.get("max_pressure"))
        rms = _number(analysis.get("rms_pressure"))
        shape = analysis.get("field_shape")
        metrics_ok = (math.isfinite(peak) and math.isfinite(rms)
                      and peak > 0 and 0 <= rms <= peak
                      and isinstance(shape, list) and len(shape) in (2, 3)
                      and all(type(size) is int and size > 0 for size in shape))
    except (TypeError, ValueError, OverflowError):
        metrics_ok = False
    nonzero = (analysis.get("has_signal") is True) and metrics_ok
    succeeded = exit_code == 0 and not timed_out
    checks.extend([
        _check("execution_succeeded", succeeded, "exit_code is 0 and execution did not time out"),
        _check("finite_nonzero_field", finite and nonzero,
               "analysis has finite positive peak, bounded RMS, and a valid field shape"),
    ])
    if not (succeeded and finite and nonzero):
        return {"quality_level": "Q0", "physics_checks": checks}

    level = "Q1"
    try:
        if type(params_json) is not str or len(params_json.encode("utf-8")) > 4096:
            raise ValueError("parameter_budget")
        params = json.loads(params_json, object_pairs_hook=_unique, parse_constant=_reject_constant)
    except (TypeError, ValueError, OverflowError, RecursionError):
        params = None
    params_ok = isinstance(params, dict) and bool(params)
    checks.append(_check("parameters_supplied", params_ok, "structured simulation parameters were supplied"))
    if params_ok:
        # Keep this independent of MCP registration, and avoid importing tools.
        gaussian_initial = (params.get("source_kind") == "gaussian_initial_pressure"
                            and params.get("solver_mode") == "time_domain")
        required = ("sound_speed", "domain_N", "domain_dx") + (
            ("initial_pressure_pa","initial_sigma_m") if gaussian_initial else ("source_frequency",))
        required_ok = all(params.get(key) not in (None, "") for key in required)
        checks.append(_check("parameters_complete", required_ok, "minimum physical parameters are present"))
        if required_ok:
            try:
                speed = _number(params["sound_speed"])
                frequency = None if gaussian_initial else _number(params["source_frequency"])
                dx_values = params["domain_dx"] if isinstance(params["domain_dx"], list) else [params["domain_dx"]]
                grid_sizes = params["domain_N"] if isinstance(params["domain_N"], list) else [params["domain_N"]]
                cfl = _number(params.get("cfl", 0.3))
                pml_size = params.get("pml_size")
                grid_ok = (len(grid_sizes) in (2, 3) and len(dx_values) in (1, len(grid_sizes))
                           and all(type(size) is int and size >= 32 for size in grid_sizes)
                           and type(pml_size) is int and pml_size >= 0
                           and all(2 * pml_size < size for size in grid_sizes)
                           and grid_sizes == shape)
                spacing_ok = all(math.isfinite(_number(dx)) and _number(dx) > 0
                                 for dx in dx_values)
                physically_acceptable = (
                    grid_ok and spacing_ok
                    and all(math.isfinite(value) for value in (speed, cfl))
                    and speed > 0 and 0 < cfl <= 0.3
                    and ((
                        "source_frequency" not in params
                        and params.get("initial_velocity") == "zero"
                        and _number(params["initial_pressure_pa"]) > 0
                        and _number(params["initial_sigma_m"]) >= 4*max(_number(dx) for dx in dx_values)
                    ) if gaussian_initial else (
                        frequency > 0 and all(0 < _number(dx) <= speed / frequency / 4 for dx in dx_values)))

                )
                positions = [params.get("source_index"), params.get("sensor_index")]
                multiple=params.get("sensor_indices")
                pml_ok = multiple is None or (type(multiple) is list and 1<=len(multiple)<=3)
                if type(multiple) is list:positions.extend(multiple)
                supplied_positions = [position for position in positions if position is not None]
                for position in supplied_positions:
                    if (not isinstance(position, list) or len(position) != len(grid_sizes)
                            or not grid_ok):
                        pml_ok = False
                        break
                    pml_ok = pml_ok and all(type(index) is int and pml_size <= index < size - pml_size
                                            for index, size in zip(position, grid_sizes))
            except (TypeError, ValueError, OverflowError):
                physically_acceptable = False
                pml_ok = False
            checks.append(_check("parameter_constraints", physically_acceptable,
                                 "grid, field shape, PML, spacing, source-resolution and CFL constraints pass"))
            checks.append(_check("pml_geometry", pml_ok,
                                 "supplied source/sensor indices are outside PML; absent geometry is deferred"))
            if physically_acceptable and pml_ok:
                level = "Q2"

    return {"quality_level": level, "physics_checks": checks}
