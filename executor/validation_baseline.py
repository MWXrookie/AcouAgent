"""AcouAgent · VAL-1 仿真验证基准集 (jwave 0.2.1, executor 沙箱内一键重跑)

用途
----
阶段二 VAL-1：为 T-007 analyze_simulation_result 的 verdict 提供物理基准。
四个用例均为解析解可对照的无损线性声学问题，不调用 DeepSeek（零模型费用）。

用例（理论值 vs 仿真实测，误差 < 1% 判 PASS）
--------------------------------------------
1. 平面波无损耗传播（振幅守恒）
   p0 = 高斯平面波前（沿 x 变化、y 均匀），零初始速度 → 分裂为 ±x 两个半幅波。
   理论：单侧传播波峰 = A/2，且传播过程峰值不变（A(x2)/A(x1) = 1）。
2. 点源圆柱波远场衰减（2D 几何扩散）
   2D 点源 3 周期 tone burst → 远场压力包络 ∝ 1/sqrt(r)。
   理论：A(r2)/A(r1) = sqrt(r1/r2)。
   （规划文档中的 1/r 为 3D 球面波定律；jwave 默认 2D 域，对应 1/sqrt(r)，与 k-Wave 2D 一致）
3. 平界面反射/透射（法向入射平面波）
   两种介质半空间界面（声速/密度阶跃），法向入射平面波。
   理论：R = (Z2-Z1)/(Z2+Z1)，T_p = 2*Z2/(Z1+Z2)，Z = rho*c。
4. 点源球面波远场衰减（3D 几何扩散）
   3D 点源 3 周期 tone burst → 远场压力包络 ∝ 1/r。
   理论：A(r2)/A(r1) = r1/r2。
5. 频域 Helmholtz 介质衰减（吸收系数，P1 衰减扩展）
   均匀吸收介质（Medium.attenuation，dB 单位、幂律 y=2）中点源频域解。
   理论：A(r2)/A(r1) = sqrt(r1/r2) * exp(-Im(k)*(r2-r1))，
   Im(k) = omega^2 * db2neper(alpha_db, 2.0)。
   ⚠️ 时域 simulate_wave_propagation 忽略 attenuation，衰减只在频域 helmholtz_solver 生效。

实测要点（已逐一在沙箱内验证，2026-08-17）
------------------------------------------
- y 向必须足够高（Ny ≥ 192）：PML 软孔径横向衍射泄漏会使内部振幅沿 x 衰减
  （Ny=64 时 0.5→0.34；Ny=256 时恒定 0.4998）。所有用例 Ny ≥ 256。
- 峰值测量：cfl=0.1 细时间采样 + 窄时间窗 + 原始信号最大值（raw max）。
  ⚠️ jwave 自带 analytic_signal 有缺陷（清零正频率且去掉 DC），且 Hilbert 包络
  具有 1/t 尾部、会把相邻到达（含初始条件伪影）卷进来——包络法会系统性偏离。
  已在 4 组对比度扫描验证 raw-max 法 R/T 误差 < 0.2%。
- smooth_initial=False：默认 True 会对 p0 做 Blackman 低通滤波，破坏解析对照。
- 点源 signals 必须补齐到 Nt 长度（tone_burst 只有 ~150 采样，越界索引静默出错）。
- 不用 Sensors 类（位置约定有歧义），直接取全场结果 p[n, x, y, 0] 按 (x, y) 索引。
  实测确认：params 轴序 = (Nt, Nx, Ny, 1)，axis0=x、axis1=y，PML 位于各向边缘。

- 3D 用例（case4）：探针必须位于 PML 物理区之内。jwave 各向边缘各占 pml_size
  网格为吸收层，物理区半径 = N/2 - pml_size。N=56/pml=8 时物理区仅 10mm，
  曾把 r=18mm 探针放进 PML 导致振幅被吸收至 1e-4、比值失真（err 94.5%）。
  现用 N=72（物理区 14mm），探针 8mm/12mm，实测 err < 0.1%。

判定
----
每用例误差 = |实测/理论 - 1| * 100%，< 1% 判 PASS。

运行方式（VM 上，executor 容器只读 rootfs，用 stdin 写入 /tmp）
----------------------------------------------------------------
  docker exec -i jwave-executor sh -c "cat > /tmp/validation_baseline.py" < executor/validation_baseline.py
  docker exec -e JAX_PLATFORMS=cpu -e XLA_PYTHON_CLIENT_PREALLOCATE=false -e HOME=/tmp \
      jwave-executor python /tmp/validation_baseline.py

说明：直接 docker exec 运行无 30s 上限（该上限仅作用于 run_jwave_code HTTP 通道）。
四个用例合计含 4 次 JAX 编译，全程约 2-5 分钟。
"""

import json
import time

import jax.numpy as jnp
import jwave as jw
from jaxdf import FourierSeries
from jwave.acoustics.time_varying import TimeWavePropagationSettings

SETTINGS = TimeWavePropagationSettings(smooth_initial=False)

THRESHOLD_PCT = 1.0  # 门禁: 误差 < 1%
CFL = 0.1            # 细时间采样，保证 raw max 采样误差可忽略


def window_peak(signal: jnp.ndarray, t: jnp.ndarray, t0: float, t1: float) -> float:
    """窄时间窗内原始信号绝对值最大值（避开相邻到达与伪影）。"""
    mask = (t >= t0) & (t <= t1)
    return float(jnp.max(jnp.abs(signal[mask])))


def err_pct(measured: float, theory: float) -> float:
    """相对误差百分比（理论值作分母）。"""
    return abs(measured / theory - 1.0) * 100.0


# ---------------------------------------------------------------------------
# 用例 1: 平面波无损耗传播（振幅守恒）
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# 用例 2: 点源圆柱波远场 1/sqrt(r) 衰减
# ---------------------------------------------------------------------------
def case2_cylindrical_spreading() -> dict:
    dx = 0.5e-3  # 0.5 mm
    N = 256  # 128mm x 128mm
    domain = jw.Domain((N, N), (dx, dx))
    medium = jw.Medium(domain, sound_speed=1500.0, density=1000.0, pml_size=20)

    f0 = 200e3  # 200 kHz -> lambda = 7.5mm = 15 网格
    time_axis = jw.TimeAxis.from_medium(medium, cfl=CFL, t_end=60e-6)
    dt = time_axis.dt
    sig = jw.signal_processing.tone_burst(1.0 / dt, f0, 3)
    sig = jnp.concatenate([sig, jnp.zeros(int(time_axis.Nt) - len(sig))])  # 补齐到 Nt!
    signals = jnp.expand_dims(sig, 0)  # (1, Nt)
    cx = cy = N // 2
    positions = (jnp.array([cx]), jnp.array([cy]))  # 整数坐标!
    sources = jw.Sources(positions, signals, dt, domain)

    p = jw.simulate_wave_propagation(
        medium, time_axis, sources=sources, settings=SETTINGS
    ).params
    t = time_axis.to_array()
    # 源点位置校验: 前几个时间步全场最大应在源点附近
    peak_at = jnp.unravel_index(jnp.argmax(jnp.abs(p[5, :, :, 0])), (N, N))
    source_ok = bool(peak_at[0] == cx and peak_at[1] == cy)

    r1, r2 = 64, 112  # 32mm / 56mm（4.3λ / 7.5λ, 远场）
    # burst 时长 Tb=3/f0=15us, 到达 t_r=r*dx/c
    t_burst = 3.0 / f0
    t1_arr = r1 * dx / 1500.0
    t2_arr = r2 * dx / 1500.0
    a1 = window_peak(p[:, cx + r1, cy, 0], t, t1_arr - t_burst / 2, t1_arr + t_burst)
    a2 = window_peak(p[:, cx + r2, cy, 0], t, t2_arr - t_burst / 2, t2_arr + t_burst)

    theory_ratio = float(jnp.sqrt(r1 / r2))
    return {
        "case": "2 圆柱波 1/sqrt(r) 衰减",
        "measured": {"peak_r1": a1, "peak_r2": a2, "ratio_r2_r1": a2 / a1,
                     "source_ok": source_ok},
        "theory": {"ratio": theory_ratio},
        "err_pct": {"ratio": err_pct(a2 / a1, theory_ratio)},
    }


# ---------------------------------------------------------------------------
# 用例 3: 平界面反射/透射（法向入射）
# ---------------------------------------------------------------------------
def case3_interface_reflection() -> dict:
    dx = 0.25e-3  # 0.25 mm
    Nx, Ny = 384, 256  # 96mm x 64mm
    xi = 192  # 界面在网格 192 (48mm)
    c1, rho1 = 1500.0, 1000.0   # 介质1 (x < 界面): 水
    c2, rho2 = 2500.0, 2000.0   # 介质2 (x >= 界面)
    Z1, Z2 = rho1 * c1, rho2 * c2
    R_theory = (Z2 - Z1) / (Z2 + Z1)
    T_theory = 2.0 * Z2 / (Z1 + Z2)

    xs = jnp.arange(Nx, dtype=jnp.float32)
    ss = jnp.broadcast_to(
        jnp.where(xs < xi, c1, c2).astype(jnp.float32)[:, None], (Nx, Ny)
    )
    dd = jnp.broadcast_to(
        jnp.where(xs < xi, rho1, rho2).astype(jnp.float32)[:, None], (Nx, Ny)
    )
    domain = jw.Domain((Nx, Ny), (dx, dx))
    medium = jw.Medium(domain, sound_speed=ss, density=dd, pml_size=20)
    time_axis = jw.TimeAxis.from_medium(medium, cfl=CFL, t_end=24e-6)

    A = 1.0
    xp, sigma = 128, 10  # 波前中心 32mm（界面左侧 16mm）
    p0_arr = A * jnp.exp(-((xs - xp) ** 2) / (2.0 * sigma**2))
    p0_arr = jnp.broadcast_to(p0_arr[:, None], (Nx, Ny))
    p0 = FourierSeries(p0_arr, domain)

    p = jw.simulate_wave_propagation(
        medium, time_axis, p0=p0, settings=SETTINGS
    ).params
    t = time_axis.to_array()
    yc = Ny // 2
    x_inc = 160  # 界面左侧 8mm: 先收入射、后收反射
    x_tr = 224   # 界面右侧 8mm: 透射

    # 到达时刻（实测口径）: 入射 5.3us, 反射 16us, 透射 13.9us
    # 窗口避开入射尾波与初始条件伪影（~20us 后）
    a_inc = window_peak(p[:, x_inc, yc, 0], t, 3e-6, 9e-6)
    a_ref = window_peak(p[:, x_inc, yc, 0], t, 14e-6, 19e-6)
    a_tr = window_peak(p[:, x_tr, yc, 0], t, 11e-6, 17e-6)

    return {
        "case": "3 平界面反射/透射",
        "measured": {"A_inc": a_inc, "A_ref": a_ref, "A_tr": a_tr,
                     "R": a_ref / a_inc, "T": a_tr / a_inc},
        "theory": {"R": R_theory, "T": T_theory, "Z1": Z1, "Z2": Z2},
        "err_pct": {
            "R": err_pct(a_ref / a_inc, R_theory),
            "T": err_pct(a_tr / a_inc, T_theory),
        },
    }


# ---------------------------------------------------------------------------
# 用例 4: 3D 球面波远场 1/r 衰减（3D 几何扩散，P1 3D 能力扩展）
# ---------------------------------------------------------------------------
def case4_3d_spherical_decay() -> dict:
    dx = 0.5e-3  # 0.5 mm
    # N=72: 36mm 立方体, pml=8 -> 物理区半径 = 36-8 = 28 网格 = 14mm。
    # ⚠️ 曾用 N=56 时物理区半径仅 10mm，r2=36(18mm) 探针落在 PML 吸收层内，
    #    振幅被吸收至 1e-4 量级、比值完全失真（err 94.5%）。探针必须位于物理区。
    N = 72
    domain = jw.Domain((N, N, N), (dx, dx, dx))
    medium = jw.Medium(domain, sound_speed=1500.0, density=1000.0, pml_size=8)

    f0 = 300e3  # 300 kHz -> lambda = 5mm = 10 网格
    time_axis = jw.TimeAxis.from_medium(medium, cfl=CFL, t_end=25e-6)
    dt = time_axis.dt
    sig = jw.signal_processing.tone_burst(1.0 / dt, f0, 3)
    sig = jnp.concatenate([sig, jnp.zeros(int(time_axis.Nt) - len(sig))])
    signals = jnp.expand_dims(sig, 0)
    c0 = N // 2
    positions = (jnp.array([c0]), jnp.array([c0]), jnp.array([c0]))  # 3D 整数坐标
    sources = jw.Sources(positions, signals, dt, domain)

    p = jw.simulate_wave_propagation(
        medium, time_axis, sources=sources, settings=SETTINGS
    ).params
    t = time_axis.to_array()

    # 沿 +x 轴物理区内两点（r1=8mm, r2=12mm；均 < 14mm 物理区半径）
    r1, r2 = 16, 24  # 网格
    t_burst = 3.0 / f0
    t1_arr = r1 * dx / 1500.0
    t2_arr = r2 * dx / 1500.0
    a1 = window_peak(p[:, c0 + r1, c0, c0, 0], t, t1_arr - t_burst / 2, t1_arr + t_burst)
    a2 = window_peak(p[:, c0 + r2, c0, c0, 0], t, t2_arr - t_burst / 2, t2_arr + t_burst)

    # 3D 球面波: 能量摊到 4πr² 球面 -> 振幅 ∝ 1/r
    theory_ratio = float(r1 / r2)
    return {
        "case": "4 3D 球面波 1/r 衰减",
        "measured": {"peak_r1": a1, "peak_r2": a2, "ratio_r2_r1": a2 / a1},
        "theory": {"ratio": theory_ratio},
        "err_pct": {"ratio": err_pct(a2 / a1, theory_ratio)},
    }


# ---------------------------------------------------------------------------
# 用例 5: 频域 Helmholtz 介质衰减（吸收系数，P1 衰减扩展）
# ---------------------------------------------------------------------------
def case5_attenuation_helmholtz() -> dict:
    """均匀吸收介质中的点源频域解: 圆柱扩散 1/sqrt(r) × 指数吸收 exp(-Im(k) r)。

    jwave 0.2.1 的时域 simulate_wave_propagation **忽略** Medium.attenuation；
    衰减只在频域 helmholtz_solver 的 wavevector 算子中生效：
      k_mod = (omega/c)^2 + 2j * omega^3 * alpha / c,  alpha = db2neper(attenuation, 2.0)
    => Im(k) = omega^2 * db2neper(alpha_db, 2.0)（小衰减近似）。
    理论：A(r2)/A(r1) = sqrt(r1/r2) * exp(-Im(k) * (r2 - r1))。
    """
    from jwave.acoustics import db2neper, helmholtz_solver

    dx = 0.5e-3
    N = 160  # pml=20 -> 物理区半径 = 80-20 = 60 网格 = 30mm
    domain = jw.Domain((N, N), (dx, dx))
    f0 = 1e6  # 1 MHz: omega 大 -> 吸收项 Im(k) 显著（~11.5 neper/m @ alpha_db=1）
    omega = 2 * jnp.pi * f0
    c0 = 1500.0
    alpha_db = 1.0
    medium = jw.Medium(domain, sound_speed=c0, density=1000.0,
                       attenuation=alpha_db, pml_size=20)

    c = N // 2
    src_grid = jnp.zeros((N, N), dtype=jnp.complex64).at[c, c].set(1.0)
    src = jw.FourierSeries(src_grid, domain)
    u = helmholtz_solver(medium, omega, src, method="gmres")
    g = jnp.asarray(u.on_grid).reshape(N, N)

    r1_mm, r2_mm = 12.0, 24.0  # 物理区内（< 30mm）
    i1, i2 = int(r1_mm / (dx * 1e3)), int(r2_mm / (dx * 1e3))
    a1 = float(jnp.abs(g[c + i1, c]))
    a2 = float(jnp.abs(g[c + i2, c]))

    alpha_np = float(db2neper(jnp.array([alpha_db]), 2.0)[0])
    imk = float(omega ** 2 * alpha_np)
    r1, r2 = r1_mm * 1e-3, r2_mm * 1e-3
    theory_ratio = float(jnp.sqrt(r1 / r2) * jnp.exp(-imk * (r2 - r1)))
    return {
        "case": "5 介质衰减 1/sqrt(r)×e^(-αr)（频域 Helmholtz）",
        "measured": {"amp_r1": a1, "amp_r2": a2, "ratio_r2_r1": a2 / a1},
        "theory": {"ratio": theory_ratio, "im_k_neper_m": imk},
        "err_pct": {"ratio": err_pct(a2 / a1, theory_ratio)},
    }


def main() -> None:
    cases = [case1_plane_wave_conservation, case2_cylindrical_spreading,
             case3_interface_reflection, case4_3d_spherical_decay,
             case5_attenuation_helmholtz]
    results = []
    print("=" * 78)
    print("AcouAgent VAL-1 验证基准集 · jwave 0.2.1 · executor 沙箱")
    print("=" * 78)
    for fn in cases:
        tag = fn.__name__
        t0 = time.monotonic()
        try:
            r = fn()
            r["runtime_s"] = round(time.monotonic() - t0, 1)
            r["status"] = "PASS" if all(v < THRESHOLD_PCT for v in r["err_pct"].values()) else "FAIL"
        except Exception as exc:  # noqa: BLE001
            r = {"case": tag, "status": "ERROR", "error": f"{type(exc).__name__}: {exc}",
                 "runtime_s": round(time.monotonic() - t0, 1)}
        results.append(r)
        print(json.dumps(r, ensure_ascii=False, indent=2))
        print("-" * 78)

    passed = [r for r in results if r.get("status") == "PASS"]
    print(f"\n判定门禁: 每用例全部误差 < {THRESHOLD_PCT}%")
    print(f"结果: {len(passed)}/{len(results)} PASS")
    print("VALIDATION_DONE")


if __name__ == "__main__":
    main()
