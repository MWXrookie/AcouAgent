"""Validate the frozen reference, not an unimplemented production solver."""
import json
from pathlib import Path

import numpy as np

DATA = json.loads((Path(__file__).resolve().parents[1]/'fixtures/task_intake/room_reference.json').read_text(encoding='utf-8'))


def discrete_modes(nx, ny):
    # Cell-centred finite-volume -Laplacian with zero Neumann flux at ends.
    def spectrum(n, length):
        diagonal = np.full(n, 2.)
        diagonal[[0, -1]] = 1.
        operator = np.diag(diagonal)-np.diag(np.ones(n-1), 1)-np.diag(np.ones(n-1), -1)
        return np.maximum(np.linalg.eigvalsh(operator/(length/n)**2), 0.)
    p = DATA['parameters']
    lx, ly = spectrum(nx, p['length_x_m']), spectrum(ny, p['length_y_m'])
    values = [(p['sound_speed_m_s']/(2*np.pi)*np.sqrt(a+b), x, y)
              for x, a in enumerate(lx) for y, b in enumerate(ly) if x or y]
    return sorted(values)[:p['mode_count']]


def test_independent_discretization_converges_to_reference():
    expected = DATA['expected_modes']
    coarse, fine = discrete_modes(40, 30), discrete_modes(160, 120)
    assert [(x, y) for _, x, y in fine] == [(x, y) for x, y, _ in expected]
    gold = np.array([hz for _, _, hz in expected])
    coarse_error = np.max(np.abs(np.array([hz for hz, _, _ in coarse])-gold)/gold)
    fine_error = np.max(np.abs(np.array([hz for hz, _, _ in fine])-gold)/gold)
    assert fine_error < 0.001 and fine_error < coarse_error/8


def test_reference_convention_and_user_bytes_are_preserved():
    assert DATA['convention']['exclude_zero_mode']
    assert DATA['convention']['preserve_degenerate_modes']
    assert len(DATA['expected_modes']) == 10
    assert len({(x, y) for x, y, _ in DATA['expected_modes']}) == 10
    assert '\\text{m}' in DATA['original_query'] and '\\rho_0' in DATA['original_query']
    assert DATA['product_status'] == 'room_mode_solver_not_implemented'
