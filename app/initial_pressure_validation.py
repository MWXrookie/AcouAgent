"""Physical preflight for explicit initial-pressure contracts; no trust elevation."""
import math

INITIAL_SOURCES = {"modal_initial_pressure", "gaussian_initial_pressure"}
ALIASES = {"sound_speed_m_s": "sound_speed", "density_kg_m3": "density",
           "grid_shape": "domain_N", "dx_m": "domain_dx", "t_end_s": "t_end",
           "source_type": "source_kind", "frequency_hz": "source_frequency"}


def is_initial(params):
    return any(type(params.get(key)) is str and params[key] in INITIAL_SOURCES for key in ("source_kind", "source_type"))


def validate_initial(params):
    """Return errors without filling unspecified fields or consulting the skill catalog."""
    errors = []
    def error(field, message):
        errors.append({"field": field, "message": message})
    normalized = {}
    for key, value in params.items():
        target = ALIASES.get(key, key)
        if target in normalized and normalized[target] != value:
            error(target, f"{target} 的别名值冲突")
        normalized[target] = value
    p = normalized
    def number(field, value, positive=True):
        try:
            finite = type(value) in (int, float) and math.isfinite(value)
        except OverflowError:
            finite = False
        if not finite or (positive and value <= 0):
            error(field, f"{field} 必须是有限" + ("正数" if positive else "数值"))
            return None
        return float(value)
    for field in ("sound_speed", "t_end", "cfl", "initial_pressure_pa"):
        number(field, p.get(field))
    density = p.get("density")
    if type(density) is list:
        if not 1 <= len(density) <= 2:
            error("density", "密度列表必须包含 1 至 2 项")
        for value in density:
            number("density", value)
    else:
        number("density", density)
    if "secondary_sound_speed_m_s" in p:
        number("secondary_sound_speed_m_s", p["secondary_sound_speed_m_s"])
    if type(p.get("cfl")) in (int, float) and p["cfl"] > .3:
        error("cfl", "CFL 超过安全值 0.3")
    if p.get("solver_mode") != "time_domain":
        error("solver_mode", "初压合同要求 time_domain")
    if p.get("initial_velocity") != "zero":
        error("initial_velocity", "初压合同要求显式零初速度")
    # Even an explicit null is ambiguous: an initial condition has no driven frequency.
    if any(key in p for key in ("source_frequency", "source_frequency_mhz", "frequency_mhz")):
        error("source_frequency", "初压合同不接受驱动源频率")
    shape = p.get("domain_N")
    grid_ok = (type(shape) is list and len(shape) in (2, 3)
               and all(type(n) is int and 32 <= n <= 1024 for n in shape))
    if not grid_ok:
        error("domain_N", "网格必须为二维或三维整数列表，每维 32 至 1024")
    dx = p.get("domain_dx")
    dx = dx if type(dx) is list else [dx]
    spacing_ok = bool(grid_ok and len(dx) in (1, len(shape)))
    for value in dx:
        spacing_ok = number("domain_dx", value) is not None and spacing_ok
    if not spacing_ok:
        error("domain_dx", "网格步长维数必须与网格一致或为统一步长")
    if spacing_ok and len(dx) == 1:
        dx = dx * len(shape)
    pml = p.get("pml_size")
    if type(pml) is not int or pml < 0 or (grid_ok and any(2*pml >= n for n in shape)):
        error("pml_size", "PML 必须为非负整数且不能覆盖有效网格")
    if grid_ok and type(pml) is int and pml >= 0:
        positions = []
        for field in ("source_index", "sensor_index"):
            if field in p:
                positions.append(p[field])
        if "sensor_indices" in p:
            if type(p["sensor_indices"]) is not list or not 1 <= len(p["sensor_indices"]) <= 3:
                error("sensor_indices", "探针列表必须包含 1 至 3 项")
            else:
                positions.extend(p["sensor_indices"])
        for position in positions:
            if (type(position) is not list or len(position) != len(shape)
                    or any(type(i) is not int or not pml <= i < n-pml for i, n in zip(position, shape))):
                error("sensor_indices", "源或探针必须位于有效网格内")
    kind = p.get("source_kind")
    if kind == "gaussian_initial_pressure":
        sigma = number("initial_sigma_m", p.get("initial_sigma_m"))
        if sigma is not None and spacing_ok and sigma < 4*max(dx):
            error("initial_sigma_m", "高斯初压宽度至少覆盖 4 个网格步长")
    elif kind == "modal_initial_pressure":
        if p.get("boundary_condition") != "periodic" or pml != 0:
            error("boundary_condition", "周期初压模态要求显式 periodic 边界和零 PML")
        amplitudes, modes = p.get("modal_amplitudes_pa"), p.get("modal_wavenumber_rad_per_m")
        modal_ok = (type(amplitudes) is list and type(modes) is list
                    and 1 <= len(modes) <= 16 and len(amplitudes) == len(modes))
        if not modal_ok:
            error("modal_wavenumber_rad_per_m", "必须提供等长的模态振幅和波数列表")
        else:
            for amplitude in amplitudes:
                number("modal_amplitudes_pa", amplitude, positive=False)
            if all(type(a) in (int, float) and a == 0 for a in amplitudes):
                error("modal_amplitudes_pa", "初压模态不能全部为零")
            for mode in modes:
                if not grid_ok or type(mode) is not list or len(mode) != len(shape):
                    error("modal_wavenumber_rad_per_m", "模态波数维数必须与网格一致")
                    continue
                values = [number("modal_wavenumber_rad_per_m", k, positive=False) for k in mode]
                if any(k is None for k in values):
                    continue
                norm = math.hypot(*values)
                if norm == 0 or (spacing_ok and norm*max(dx) > math.pi/2):
                    error("modal_wavenumber_rad_per_m", "非零模态波长至少需要 4 个网格点")
                if spacing_ok:
                    cycles = [k*n*d/(2*math.pi) for k, n, d in zip(values, shape, dx)]
                    if any(not math.isfinite(v) or not math.isclose(v, round(v), rel_tol=0, abs_tol=1e-8) for v in cycles):
                        error("modal_wavenumber_rad_per_m", "模态波数必须匹配周期域")
    else:
        error("source_kind", "初压激励类型不受支持")
    return {"valid": not errors, "errors": errors, "warnings": []}
