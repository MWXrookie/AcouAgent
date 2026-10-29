"""Read-only language for saved cases; never an identity or trust decision."""
import json
import math


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def measure(value, kind):
    if not _number(value):
        return None
    scale, unit = 1, ''
    if kind == 'frequency':
        scale, unit = (1e6, 'MHz') if abs(value) >= 1e6 else (1e3, 'kHz') if abs(value) >= 1e3 else (1, 'Hz')
    elif kind == 'time':
        scale, unit = (1, 's') if abs(value) >= 1 else (1e-3, 'ms') if abs(value) >= 1e-3 else (1e-6, 'μs')
    elif kind == 'length':
        scale, unit = (1, 'm') if abs(value) >= 1 else (1e-3, 'mm')
    return f'{value / scale:.6g}' + (' ' + unit if unit else '')


def _medium(p):
    c, rho = p.get('sound_speed_m_s'), p.get('density_kg_m3')
    if p.get('material_regions') or isinstance(rho, list):
        return '分区介质'
    if p.get('medium_type') in ('water', 'homogeneous_water'):
        return '水'
    if p.get('medium_type') == 'air':
        return '空气'
    if p.get('medium_type') == 'homogeneous_lossless':
        return {(1500, 1000): '水', (343, 1.225): '空气', (1540, 1050): '软组织参数'}.get((c, rho), '均匀介质') if _number(c) and _number(rho) else '均匀介质'
    return '均匀介质'


def _mode(p):
    vectors, amplitudes = p.get('modal_wavenumber_rad_per_m'), p.get('modal_amplitudes_pa')
    if (not isinstance(vectors, list) or not 1 <= len(vectors) <= 8
            or not isinstance(amplitudes, list) or len(vectors) != len(amplitudes)
            or not all(_number(a) and a != 0 for a in amplitudes)
            or not all(isinstance(v, list) and len(v) == 2 and all(_number(n) for n in v) for v in vectors)):
        return '驻波传播', '给定初始压力分布的时域变化。'
    if len(vectors) == 1:
        x, y = vectors[0]
        if x and not y:
            return '水平驻波', '水平方向初始压力对应的驻波振荡。'
        if y and not x:
            return '竖直驻波', '竖直方向初始压力对应的驻波振荡。'
        if x and y and math.isclose(abs(x), abs(y), rel_tol=1e-9):
            return '45°斜向驻波', '45°方向初始压力对应的驻波振荡。'
        return '斜向驻波', '斜向初始压力对应的驻波振荡。'
    if len(vectors) == 2:
        a, b = vectors
        if ((a[0] and not a[1] and b[1] and not b[0])
                or (b[0] and not b[1] and a[1] and not a[0])):
            return '水平与竖直驻波叠加', '叠加水平和竖直两个方向的驻波，观察它们共同形成的声压变化。'
        if a[0] and b[0] and not a[1] and not b[1] and math.isclose(max(abs(a[0]), abs(b[0])) / min(abs(a[0]), abs(b[0])), 3, rel_tol=1e-9):
            return '基波与三倍频叠加', '叠加基波和频率为其三倍的分量，比较叠加后的压力波形。'
        if all(v[0] and v[1] for v in vectors):
            return '双斜向驻波叠加', '叠加两种斜向驻波，观察两种频率共同作用时的声压变化。'
    return f'{len(vectors)}种驻波叠加', '叠加多种初始压力分量，观察指定测点的声压变化。'


def _present(scenario, raw, original='', *, detail=False):
    if isinstance(raw, str):
        if len(raw) > 8192:
            return None
        try:
            raw = json.loads(raw)
        except (ValueError, TypeError):
            return None
    if not isinstance(raw, dict):
        return None
    p = raw.get('parameters') if raw.get('normalizer_version') == 'normalizer_v2' else raw
    if not isinstance(p, dict):
        return None
    original = original if isinstance(original, str) else ''
    medium, kind = _medium(p), p.get('source_type')
    prefix = medium + ('下的' if medium == '软组织参数' else '中')
    freq = measure(p.get('frequency_hz'), 'frequency')
    duration = measure(p.get('t_end_s'), 'time')
    if p.get('medium_type') == 'lossless_two_halfspaces':
        title, purpose = '两种介质交界面的反射与透射', '模拟压力脉冲到达两种介质的交界面，查看界面两侧的反射与透射。'
    elif kind == 'modal_initial_pressure':
        mode, purpose = _mode(p)
        title = prefix + mode
    elif kind == 'gaussian_initial_pressure':
        title, purpose = prefix + '高斯压力脉冲传播', '从高斯形状的初始压力出发，观察压力脉冲的传播和指定测点的波形。'
    elif p.get('solver_mode') == 'frequency_domain':
        regions = p.get('material_regions')
        label = '声速分区' if isinstance(regions, list) and any(isinstance(r, dict) and 'sound_speed_m_s' in r for r in regions) else '密度分区' if regions else medium
        point = p.get('source_geometry') == 'point' or scenario in ('point_source_2d', 'point_source_3d')
        title = label + '中' + (freq or '') + ('稳态点声源' if point else '稳态声场')
        purpose = '给定持续发声的点声源，计算固定频率下的稳态声压分布。' if point else '根据给定声源和介质设置，计算固定频率下的声压分布。'
    else:
        point = p.get('source_geometry') == 'point' or scenario in ('point_source_2d', 'point_source_3d')
        title = prefix + (freq or '') + ('点声源传播' if point else '声波传播') + (' · ' + duration if duration else '')
        purpose = '点声源在给定时间内的声压传播。' if point else '给定声源和介质条件下的声波传播。'
    # A short human-authored name remains the user's name. Internal generated
    # titles are replaced for display only; the stored title is kept intact.
    generated = ('受控执行器' in original or original in ('Automatic controlled source', '已记录仿真的未认证候选案例')
                 or (original.startswith(('空气：', '水：', '软组织参数：')) and '；二维周期边界，均匀无损、固定初压模态' in original) or not original)
    if not generated and len(original) <= 80 and '\n' not in original:
        title = original
    shape = p.get('grid_shape')
    shape_text = ' × '.join(str(n) for n in shape) if isinstance(shape, list) and 1 <= len(shape) <= 3 and all(type(n) is int and n > 0 for n in shape) else None
    subtitle = ' · '.join(x for x in [duration if kind in ('modal_initial_pressure', 'gaussian_initial_pressure') else None,
                                     '网格 ' + shape_text if shape_text else None] if x)
    result = dict(title=title, subtitle=subtitle)
    if not detail:
        return result
    boundary = {'periodic': '周期边界',
                'absorbing_pml': 'PML吸收边界',
                'rigid': '刚性壁面'}.get(p.get('boundary_condition'), '未记录')
    setup = []
    for key, label, quantity in [('frequency_hz', '声源频率', 'frequency'), ('t_end_s', '计算时长', 'time'),
                                  ('initial_sigma_m', '初始压力宽度', 'length'), ('interface_position_m', '界面位置', 'length')]:
        value = measure(p.get(key), quantity)
        if value:
            setup.append(label + ' ' + value)
    for key, label, unit in [('sound_speed_m_s', '声速', 'm/s'), ('secondary_sound_speed_m_s', '另一侧声速', 'm/s'),
                              ('density_kg_m3', '密度', 'kg/m³'), ('initial_pressure_pa', '初始压力峰值', 'Pa')]:
        values = p.get(key) if isinstance(p.get(key), list) else [p.get(key)]
        if 1 <= len(values) <= 3 and all(_number(v) for v in values):
            setup.append(label + ' ' + ' / '.join(f'{v:.6g}' for v in values) + ' ' + unit)
    if shape_text:
        setup.append('计算网格 ' + shape_text)
    spacing = p.get('dx_m')
    if isinstance(spacing, list) and 1 <= len(spacing) <= 3 and all(_number(v) and v > 0 for v in spacing):
        setup.append('网格间距 ' + ' × '.join(measure(v, 'length') for v in spacing))
    position = p.get('source_index')
    if (scenario in ('point_source_2d', 'point_source_3d') and isinstance(position, list)
            and 1 <= len(position) <= 3 and all(type(v) is int and v >= 0 for v in position)):
        setup.append('声源网格位置 (' + ', '.join(str(v) for v in position) + ')')
    output = ('稳态声压分布（数值源归一化）'
              if p.get('solver_mode') == 'frequency_domain' else '指定测点随时间变化的声压波形。' if p.get('sensor_indices') else '压力结果')
    result.update(purpose=purpose, boundary=boundary, setup=setup, output=output)
    return result


def present(scenario, raw, original='', *, detail=False):
    try:
        return _present(scenario, raw, original, detail=detail)
    except (ValueError, TypeError, OverflowError, RecursionError):
        # Auxiliary wording must never make an old record unreadable.
        return None
