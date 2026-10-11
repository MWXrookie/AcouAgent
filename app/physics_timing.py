"""Conservative timing diagnostics, without execution or trust assignment."""
import math


def assess_timing(*, dt_s=None, sample_count=None, distance_m=None,
                  sound_speed_m_s=None, frequency_hz=None, sample_start_s=0):
    """Uniform medium, explicitly declared sample origin; authenticity is external.

    Arrival is a geometric lower-bound check, not detected acoustic arrival.
    Duration alone never proves convergence or a steady state.
    """
    result = {"status": "unknown", "steady_state_verified": False,
              "trust_verified": False, "reason": "missing_or_invalid_timing"}
    values = (dt_s, distance_m, sound_speed_m_s, frequency_hz, sample_start_s)
    try:
        if any(type(v) not in (int, float) or not math.isfinite(v) for v in values):
            return result
    except (OverflowError, ValueError):
        return result
    if (type(sample_count) is not int or not 1 <= sample_count <= 10_000_000
            or dt_s <= 0 or distance_m < 0 or sound_speed_m_s <= 0
            or frequency_hz <= 0 or sample_start_s < 0):
        return result
    end = sample_start_s + dt_s * (sample_count - 1)
    arrival = distance_m / sound_speed_m_s
    period = 1.0 / frequency_hz
    cycles = (end - sample_start_s) * frequency_hz
    if not all(math.isfinite(v) for v in (end, arrival, period, cycles)) or period <= 0:
        return result
    result.update(end_time_s=end, geometric_arrival_s=arrival,
                  source_period_s=period, observed_source_cycles=cycles)
    if end < arrival:
        result.update(status="insufficient", reason="ends_before_geometric_arrival")
    elif end - max(arrival,sample_start_s) < period:
        result.update(status="insufficient", reason="less_than_one_period_after_arrival")
    else:
        result.update(status="unverified", reason="convergence_not_assessed")
    return result


def assess_sensor_time_axis(time_values, *, sample_start_s=0):
    """Diagnose actual exported samples only; no geometry or source inference."""
    result = {"status": "unknown", "reason": "missing_or_invalid_time_axis",
              "steady_state_verified": False, "trust_verified": False}
    if not isinstance(time_values, list) or not 2 <= len(time_values) <= 100000:
        return result
    try:
        if any(type(v) not in (int, float) or not math.isfinite(v) for v in time_values):
            return result
        if (type(sample_start_s) not in (int,float) or not math.isfinite(sample_start_s)
                or sample_start_s<0 or not math.isclose(time_values[0],sample_start_s,rel_tol=2e-6,abs_tol=0)):
            return result
        dt = time_values[1] - time_values[0]
        if not math.isfinite(dt) or dt <= 0:
            return result
        # Tolerance accommodates float32-exported axes, not duplicated/reversed times.
        for i in range(1, len(time_values)):
            step = time_values[i] - time_values[i - 1]
            if step <= 0 or not math.isclose(time_values[i], time_values[0] + i * dt,
                                           rel_tol=2e-6, abs_tol=dt * 2e-6):
                return result
    except (OverflowError, ValueError):
        return result
    result.update(reason="geometry_and_source_metadata_unavailable",
                  dt_s=dt, sample_count=len(time_values), end_time_s=time_values[-1],
                  sample_start_s=time_values[0])
    return result


def assess_sensor_timing(sensor):
    """Optional v1/v2 metadata stays untrusted; v2 declares post-update samples."""
    meta = sensor.get("timing_metadata") if isinstance(sensor,dict) else None
    version = meta.get("version") if isinstance(meta,dict) else None
    explicit_start = meta.get("sample_start_s") if type(version) is int and version==2 else 0
    axis = assess_sensor_time_axis(sensor.get("time") if isinstance(sensor, dict) else None,
                                  sample_start_s=explicit_start)
    if "dt_s" not in axis:
        return axis
    keys = {"version", "time_unit", "medium", "sound_speed_m_s",
            "source_frequency_hz", "source_position_m", "sensor_position_m",
            "source_start_s", "source_kind"}
    if type(version) is int and version==2:
        keys |= {"sample_start_s","sample_origin"}
    if not isinstance(meta, dict) or set(meta) != keys:
        return axis
    if (type(meta["version"]) is not int or meta["version"] not in (1,2)
            or meta["time_unit"] != "s" or meta["medium"] != "uniform"
            or meta["source_kind"] != "continuous_sinusoid"
            or type(meta["source_start_s"]) not in (int, float)
            or meta["source_start_s"] != 0):
        return axis
    if version==2 and (meta["sample_origin"]!="jwave_0_2_1_after_update"
            or not math.isclose(axis["sample_start_s"],axis["dt_s"],rel_tol=2e-6,abs_tol=0)):
        return axis
    source, target = meta["source_position_m"], meta["sensor_position_m"]
    try:
        if (not isinstance(source, list) or not isinstance(target, list)
                or len(source) not in (2, 3) or len(source) != len(target)
                or any(type(v) not in (int, float) or not math.isfinite(v)
                       for v in source + target)):
            return axis
        distance = math.dist(source, target)
        result = assess_timing(dt_s=(axis["end_time_s"]-axis["sample_start_s"]) / (axis["sample_count"] - 1), sample_count=axis["sample_count"],
                               sample_start_s=axis["sample_start_s"],
                               distance_m=distance,
                               sound_speed_m_s=meta["sound_speed_m_s"],
                               frequency_hz=meta["source_frequency_hz"])
    except (OverflowError, ValueError):
        return axis
    if result["status"] == "unknown":
        return axis
    # Preserve the exported end rather than synthesizing a new time axis.
    result["end_time_s"] = axis["end_time_s"]
    result["metadata_authenticated"] = False
    result["basis"] = "unverified_output_metadata"
    return result
