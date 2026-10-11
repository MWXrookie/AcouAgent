"""Offline source/window acceptance diagnostics; never grants physics trust."""
import math


def inspect_window(*, start_s, end_s, center_s, sigma_s, frequency_hz,
                   field_kind, distance_kind):
    values = (start_s, end_s, center_s, sigma_s, frequency_hz)
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in values):
        raise ValueError("numeric_values_required")
    if any(not math.isfinite(v) for v in values):
        raise ValueError("finite_values_required")
    if start_s < 0 or end_s <= start_s or sigma_s <= 0 or frequency_hz <= 0:
        raise ValueError("invalid_window_or_source")
    if field_kind not in ("temporal_max_abs", "final_frame", "time_resolved"):
        raise ValueError("unknown_field_kind")
    if distance_kind not in ("theoretical", "measured"):
        raise ValueError("unknown_distance_kind")
    center_inside = start_s <= center_s <= end_s
    coverage = start_s <= center_s - 3 * sigma_s and end_s >= center_s + 3 * sigma_s
    issues = []
    if not center_inside:
        issues.append("source_center_outside_window")
    if not coverage:
        issues.append("three_sigma_envelope_truncated")
    if distance_kind == "measured" and field_kind != "time_resolved":
        issues.append("distance_measurement_evidence_missing")
    return {"source_center_in_window": center_inside,
            "three_sigma_envelope_covered": coverage,
            "field_kind": field_kind, "issues": issues,
            "coverage_convention": "finite_three_sigma_not_exact_gaussian_support",
            "trusted_run": False, "physics_accepted": False}
