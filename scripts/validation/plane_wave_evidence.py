"""Offline VAL-1 numeric check. Never grants Q3/Q4 or authenticates a run.

Only the pinned baseline profile is supported. Summary/theory/err_pct supplied
by the producer are ignored: metrics are recomputed from signed raw traces.
"""
import json
import math
import sys

VERSION = "val1-plane-wave.v2"
PROFILE = "val1-plane-wave-v2"
EXPECTED_CONFIGURATION = {
    "domain_N": [384, 384], "domain_dx_m": [0.00025, 0.00025],
    "sound_speed_m_s": 1500.0, "density_kg_m3": 1000.0,
    "pml_size": 20, "cfl": 0.1, "t_end_s": 34e-6,
    "initial_pressure_pa": 1.0, "initial_velocity": "zero",
    "source_x_index": 160, "sigma_grid": 12,
    "sensor_indices": [[224, 192], [304, 192]], "smooth_initial": False,
}


def validate_plane_wave(result):
    receipt = {"validator_version": VERSION, "numeric_pass": False,
               "trusted_run": False, "reason": "invalid_raw_evidence"}
    try:
        raw = result["raw_evidence"]
        if raw["profile"] not in ("val1-plane-wave-v1", PROFILE) or raw["units"] != {"time": "s", "pressure": "Pa"}:
            raise ValueError("unsupported_profile_or_units")
        if raw["profile"] == PROFILE:
            # Exact type-aware structure: True must not impersonate 1 Pa.
            config = raw.get("configuration")
            if (not isinstance(config, dict)
                    or json.dumps(config, sort_keys=True, allow_nan=False) !=
                    json.dumps(EXPECTED_CONFIGURATION, sort_keys=True, allow_nan=False)):
                raise ValueError("unsupported_configuration")
        times = raw["time"]
        traces = [raw["pressure_x1"], raw["pressure_x2"]]
        if not isinstance(times, list) or not 100 <= len(times) <= 4096:
            raise ValueError("invalid_sample_count")
        if any(not isinstance(v, list) or len(v) != len(times) for v in traces):
            raise ValueError("trace_length_mismatch")
        for values in [times, *traces]:
            if any(type(v) not in (int, float) or not math.isfinite(v) for v in values):
                raise ValueError("nonfinite_or_nonnumeric_samples")
        dt = times[1] - times[0]
        if (abs(times[0]) > 1e-12 or not 0 < dt <= 0.1 * 0.00025 / 1500 * 1.001
                or times[-1] < 33e-6 or times[-1] > 35e-6
                or any(abs((b-a)/dt - 1) > 0.001 for a, b in zip(times, times[1:]))):
            raise ValueError("invalid_time_axis")
        # Fixed homogeneous, lossless Gaussian p0=1 Pa, zero initial velocity.
        # d'Alembert gives two copies of p0/2 moving at +/-1500 m/s.
        peaks, waveform_errors, arrival_errors = [], [], []
        sigma_t = 12 * 0.00025 / 1500
        for offset, trace in zip((64, 144), traces):
            arrival = offset * 0.00025 / 1500
            indices = [i for i, t in enumerate(times) if abs(t-arrival) <= 3*sigma_t]
            peak_index = max(indices, key=lambda i: trace[i])
            peaks.append(trace[peak_index])
            arrival_errors.append(abs(times[peak_index]-arrival))
            waveform_errors.append(max(abs(trace[i] - 0.5 * (
                math.exp(-0.5*((times[i]-arrival)/sigma_t)**2)
                + math.exp(-0.5*((times[i]+arrival)/sigma_t)**2))) / 0.5
                for i in indices))
        if min(peaks) <= 0:
            raise ValueError("nonpositive_peak")
        errors = [abs(v/0.5-1) for v in peaks] + [abs(peaks[1]/peaks[0]-1)]
        passed = (all(v < 0.01 for v in errors + waveform_errors)
                  and all(v <= 2*dt for v in arrival_errors))
        receipt.update(numeric_pass=passed, reason="numeric_checks_passed" if passed else "physics_mismatch",
                       measured_peaks_pa=peaks, relative_errors=errors,
                       waveform_relative_errors=waveform_errors,
                       arrival_errors_s=arrival_errors, tolerance_relative=0.01)
    except (KeyError, TypeError, ValueError, IndexError) as exc:
        receipt["reason"] = str(exc)
    return receipt


if __name__ == "__main__":
    checked = validate_plane_wave(json.load(sys.stdin))
    print(json.dumps(checked, allow_nan=False))
    sys.exit(0 if checked["numeric_pass"] else 1)
