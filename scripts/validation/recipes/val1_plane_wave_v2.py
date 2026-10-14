"""Pinned VAL-1 plane wave v2 only; execute through the existing sandbox."""
import json
import jax.numpy as jnp
import jwave as jw
from jaxdf import FourierSeries
from jwave.acoustics.time_varying import TimeWavePropagationSettings
SETTINGS = TimeWavePropagationSettings(smooth_initial=False)
CFL = 0.1

def window_peak(signal: jnp.ndarray, t: jnp.ndarray, t0: float, t1: float) -> float:
    """窄时间窗内原始信号绝对值最大值（避开相邻到达与伪影）。"""
    mask = (t >= t0) & (t <= t1)
    return float(jnp.max(jnp.abs(signal[mask])))

def err_pct(measured: float, theory: float) -> float:
    """相对误差百分比（理论值作分母）。"""
    return abs(measured / theory - 1.0) * 100.0

def case1_plane_wave_conservation() -> dict:
    dx = 0.25e-3  # 0.25 mm
    Nx, Ny = 384, 384  # 96mm x 96mm; complete +/-3sigma probe windows avoid lateral contamination
    domain = jw.Domain((Nx, Ny), (dx, dx))
    medium = jw.Medium(domain, sound_speed=1500.0, density=1000.0, pml_size=20)
    time_axis = jw.TimeAxis.from_medium(medium, cfl=CFL, t_end=34e-6)

    A = 1.0  # Pa
    x0, sigma = 160, 12  # 波前中心 40mm, 半宽 3mm（网格单位）
    xs = jnp.arange(Nx, dtype=jnp.float32)
    p0_arr = A * jnp.exp(-((xs - x0) ** 2) / (2.0 * sigma**2))
    p0_arr = jnp.broadcast_to(p0_arr[:, None], (Nx, Ny))
    p0 = FourierSeries(p0_arr, domain)

    p = jw.simulate_wave_propagation(medium, time_axis, p0=p0, settings=SETTINGS).params
    t = time_axis.to_array()
    yc = Ny // 2
    x1, x2 = x0 + 64, x0 + 144  # 距中心 +16mm / +36mm
    # 到达时刻: t1=(x1-x0)*dx/c, 窗口 ±3sigma_t (sigma_t = sigma*dx/c)
    t1_arr = (x1 - x0) * dx / 1500.0
    t2_arr = (x2 - x0) * dx / 1500.0
    sig_t = sigma * dx / 1500.0
    a1 = window_peak(p[:, x1, yc, 0], t, t1_arr - 3 * sig_t, t1_arr + 3 * sig_t)
    a2 = window_peak(p[:, x2, yc, 0], t, t2_arr - 3 * sig_t, t2_arr + 3 * sig_t)

    theory_peak = A / 2.0
    return {
        "case": "1 平面波振幅守恒",
        "raw_evidence": {
            "profile": "val1-plane-wave-v2",
            "configuration": {
                "domain_N": [Nx, Ny], "domain_dx_m": [dx, dx],
                "sound_speed_m_s": 1500.0, "density_kg_m3": 1000.0,
                "pml_size": 20, "cfl": CFL, "t_end_s": 34e-6,
                "initial_pressure_pa": A, "initial_velocity": "zero",
                "source_x_index": x0, "sigma_grid": sigma,
                "sensor_indices": [[x1, yc], [x2, yc]], "smooth_initial": False,
            },
            "units": {"time": "s", "pressure": "Pa"},
            "time": [float(v) for v in t],
            "pressure_x1": [float(v) for v in p[:, x1, yc, 0]],
            "pressure_x2": [float(v) for v in p[:, x2, yc, 0]],
        },
        "measured": {"peak_x1": a1, "peak_x2": a2, "ratio_x2_x1": a2 / a1},
        "theory": {"peak": theory_peak, "ratio": 1.0},
        "err_pct": {
            "peak_x1": err_pct(a1, theory_peak),
            "peak_x2": err_pct(a2, theory_peak),
            "ratio": err_pct(a2 / a1, 1.0),
        },
    }

if __name__ == "__main__":
    print(json.dumps(case1_plane_wave_conservation(), allow_nan=False))
