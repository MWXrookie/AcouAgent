"""Controlled rigid-room eigenmodes, independently checked against separation of variables.

Only numerical inputs from a re-parsed, source-bound task contract enter the
sandbox template. This is not a Helmholtz pressure-field quality profile and
does not admit, certify or learn skills.
"""
import json
import math

from app.task_contract import room_contract
from app.executor_evidence import sha256

METHOD = 'neumann-cell-centred-finite-volume.v1'
TOLERANCE = 0.001


def checked_parameters(contract):
    if not isinstance(contract, dict) or contract != room_contract(contract.get('source_text')):
        raise ValueError('room_contract_changed')
    if contract['status'] != 'ready_for_confirmation':
        raise ValueError('room_contract_not_ready')
    params = contract['parameters']
    for name in ('length_x_m', 'length_y_m', 'sound_speed_m_s'):
        if type(params.get(name)) not in (int, float) or not 1e-6 <= params[name] <= 1e6:
            raise ValueError('room_numeric_range_unsupported')
    if type(params.get('mode_count')) is not int or not 1 <= params['mode_count'] <= 20:
        raise ValueError('room_mode_count_unsupported')
    return {key: params[key] for key in ('length_x_m', 'length_y_m', 'sound_speed_m_s',
                                        'mode_count', 'include_zero_mode')}


def reference_modes(params, *, full=False):
    """Independent continuous Neumann solution; keep every degenerate index."""
    count = params['mode_count']
    rows = [(params['sound_speed_m_s']/2 * math.hypot(nx/params['length_x_m'], ny/params['length_y_m']), nx, ny)
            for nx in range(count+1) for ny in range(count+1)
            if params['include_zero_mode'] or nx or ny]
    return sorted(rows) if full else sorted(rows)[:count]


def build_code(contract):
    params = checked_parameters(contract)
    # JSON contains only checked finite numbers and a boolean. No source text,
    # expressions, model code, reference frequencies or expected arrays enter it.
    payload = json.dumps(params, sort_keys=True, allow_nan=False)
    return '''import json
import numpy as np
p = json.loads(''' + repr(payload) + ''')
k = p['mode_count']
n = max(160, 32*k)
def eigenvalues(cells):
    diagonal = np.full(cells, 2.0)
    diagonal[0] = diagonal[-1] = 1.0
    matrix = np.diag(diagonal)-np.diag(np.ones(cells-1), 1)-np.diag(np.ones(cells-1), -1)
    values = np.linalg.eigvalsh(matrix)
    if abs(values[0]) > 1e-10 or values[1] <= 0:
        raise ValueError('invalid_neumann_spectrum')
    values[0] = 0.0
    return values[:k+1]
def solve(cells):
    spectrum = eigenvalues(cells)
    x = spectrum/(p['length_x_m']/cells)**2
    y = spectrum/(p['length_y_m']/cells)**2
    rows = [(float(p['sound_speed_m_s']/(2*np.pi)*np.sqrt(a+b)), nx, ny)
            for nx,a in enumerate(x) for ny,b in enumerate(y)
            if p['include_zero_mode'] or nx or ny]
    return [{'nx':nx,'ny':ny,'frequency_hz':hz} for hz,nx,ny in sorted(rows)]
print(json.dumps({'method':'neumann-cell-centred-finite-volume.v1',
    'parameters':p,'cells_per_axis':n,'coarse_cells_per_axis':n//2,
    'modes':solve(n),'coarse_modes':solve(n//2)}, allow_nan=False))
'''


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate_solver_field')
        result[key] = value
    return result


def verify_result(contract, result):
    params = checked_parameters(contract)
    if (type(result.get('exit_code')) is not int or result['exit_code'] != 0
            or result.get('timed_out') is not False or result.get('stderr') != ''
            or result.get('execution_evidence', {}).get('matched') is not True
            or result.get('execution_evidence', {}).get('receipt', {}).get('code_sha256') != sha256(build_code(contract))):
        raise ValueError('room_executor_not_correlated_or_failed')
    stdout = result.get('stdout')
    if not isinstance(stdout, str) or len(stdout.encode()) > 131072:
        raise ValueError('room_output_invalid')
    def reject(_):
        raise ValueError('room_output_nonfinite')
    data = json.loads(stdout, object_pairs_hook=_unique, parse_constant=reject)
    n = max(160, 32*params['mode_count'])
    if (not isinstance(data, dict) or set(data) != {'method', 'parameters', 'cells_per_axis',
            'coarse_cells_per_axis', 'modes', 'coarse_modes'} or data['method'] != METHOD
            or data['parameters'] != params or type(data['cells_per_axis']) is not int
            or data['cells_per_axis'] != n or type(data['coarse_cells_per_axis']) is not int
            or data['coarse_cells_per_axis'] != n//2):
        raise ValueError('room_output_contract_mismatch')
    expected = reference_modes(params)
    full_reference = reference_modes(params, full=True)
    expected_by_index = {(nx, ny): hz for hz, nx, ny in full_reference}
    # Return the bounded index grid rather than truncating a discrete spectrum
    # at a degenerate continuous frequency. The continuous solution establishes
    # display ordering; every displayed index has its own sandbox eigenvalue.
    def compare(rows):
        if not isinstance(rows, list) or len(rows) != len(full_reference):
            raise ValueError('room_mode_count_mismatch')
        seen, errors, frequencies = set(), {}, []
        for row in rows:
            if not isinstance(row, dict) or set(row) != {'nx', 'ny', 'frequency_hz'}:
                raise ValueError('room_mode_invalid')
            if type(row['nx']) is not int or type(row['ny']) is not int:
                raise ValueError('room_index_invalid')
            index = row['nx'], row['ny']
            hz = row['frequency_hz']
            if (index in seen or index not in expected_by_index or type(hz) not in (int, float)
                    or not math.isfinite(hz) or hz < 0):
                raise ValueError('room_mode_invalid')
            seen.add(index)
            frequencies.append(hz)
            target = expected_by_index[index]
            if target == 0 and hz != 0:
                raise ValueError('room_zero_mode_mismatch')
            errors[index] = abs(hz-target)/target if target else 0.
        if frequencies != sorted(frequencies):
            raise ValueError('room_modes_not_sorted')
        return errors
    fine, coarse = compare(data['modes']), compare(data['coarse_modes'])
    maximum = max(fine.values())
    if maximum > TOLERANCE:
        raise ValueError('room_frequency_tolerance_failed')
    if any(fine[index] > coarse[index]/2 + 1e-9 for index in fine):
        raise ValueError('room_convergence_failed')
    displayed_maximum = max(fine[nx,ny] for _,nx,ny in expected)
    return dict(method=METHOD, verdict='normal', validation_kind='room_modes_analytic_crosscheck.v1',
                quality_level=None, max_relative_error=displayed_maximum, max_validation_grid_relative_error=maximum, cells_per_axis=n,
                include_zero_mode=params['include_zero_mode'], modes=[dict(nx=nx, ny=ny,
                    frequency_hz=hz, numerical_frequency_hz=next(row['frequency_hz'] for row in data['modes']
                    if (row['nx'],row['ny']) == (nx,ny)), relative_error=fine[nx,ny]) for hz,nx,ny in expected],
                skill_admitted=False)


def report(validation, run_id):
    lines = [f"已完成前{len(validation['modes'])}个房间模态计算，与解析标准的最大相对误差为{validation['max_relative_error']:.4%}。", '',
             '边界条件：四壁法向声压梯度为零（Neumann）。令 p=X(x)Y(y)，',
             '得 X=cos(nx·πx/Lx)、Y=cos(ny·πy/Ly)，因此：', '',
             '`f(nx,ny) = (c/2)·sqrt((nx/Lx)² + (ny/Ly)²)`，nx、ny为非负整数。', '',
             '采用均匀介质、空房间的理想模型；不计算吸声或混响。',
             '零频(0,0)模态：'+('计入。' if validation['include_zero_mode'] else '不计入。')+'简并模态按不同阶数分别计数。', '',
             '| 序号 | 阶数(nx,ny) | 解析标准/Hz | 沙箱数值解/Hz | 相对误差 |',
             '|---|---|---:|---:|---:|']
    for i, row in enumerate(validation['modes'], 1):
        lines.append(f"| {i} | ({row['nx']},{row['ny']}) | {row['frequency_hz']:.6f} | "
                     f"{row['numerical_frequency_hz']:.6f} | {row['relative_error']:.4%} |")
    lines += ['', '数值方法：单元中心有限体积离散Neumann负拉普拉斯矩阵，分离求本征值；'
              f"每轴{validation['cells_per_axis']}个单元，并用半分辨率检查收敛。这不是有限元计算。",
              f"所列模态最大相对误差：{validation['max_relative_error']:.4%}；全部校验阶数最大误差{validation['max_validation_grid_relative_error']:.4%}（门限0.1%）。",
              '独立解析核对通过。此结果尚未加入可复用技能库。', '',
              '[公式及刚性壁依据：MathWorks](https://www.mathworks.com/help/audio/ug/find-room-modes-with-finite-element-analysis.html)。'
              '具体尺寸来自本次输入，数值为本次新计算。', '', f'执行编号：`{run_id}`']
    return '\n'.join(lines)
