"""Controlled room solver physics and negative evidence; no model/HTTP calls."""
import contextlib
import copy
import io
import json
from pathlib import Path

import pytest

from app import room_modes
from app.executor_evidence import sha256
from app.task_contract import room_contract

ORIGINAL = json.loads((Path(__file__).parents[1]/'fixtures/task_intake/room_reference.json').read_text(encoding='utf-8'))


def compute(contract):
    code = room_modes.build_code(contract)
    out = io.StringIO()
    # Unit test only: the production runner must dispatch through the executor.
    with contextlib.redirect_stdout(out):
        exec(compile(code, '<controlled-room-unit-test>', 'exec'), {})
    return dict(stdout=out.getvalue(), stderr='', exit_code=0, timed_out=False,
                execution_evidence=dict(matched=True, receipt=dict(code_sha256=sha256(code))))


@pytest.mark.parametrize('query', [ORIGINAL['original_query'],
    '二维矩形房间，Lx=400cm，Ly=3000mm，声速343m/s，四壁刚性，前10个模态，不包括零模态',
    '二维矩形房间，Lx=4m，Ly=3m，声速343m/s，四壁刚性，前20个模态，包含零模态',
    '二维矩形房间，Lx=4m，Ly=3m，声速343m/s，四壁刚性，前12个模态',
    '二维矩形房间，Lx=3m，Ly=3m，声速343m/s，四壁刚性，前9个模态'])
def test_numerical_solution_matches_continuous_standard_and_converges(query):
    contract = room_contract(query)
    verified = room_modes.verify_result(contract, compute(contract))
    assert verified['verdict'] == 'normal'
    assert verified['max_relative_error'] < .001
    assert verified['quality_level'] is None and verified['skill_admitted'] is False
    assert len(verified['modes']) == contract['parameters']['mode_count']
    assert len({(m['nx'], m['ny']) for m in verified['modes']}) == len(verified['modes'])
    if query == ORIGINAL['original_query']:
        assert [(m['nx'],m['ny'],m['frequency_hz']) for m in verified['modes']] == [tuple(r) for r in ORIGINAL['expected_modes']]
    text = room_modes.report(verified, 'unit-only')
    assert '沙箱数值解' in text and '有限体积' in text and '不是有限元' in text


@pytest.fixture(scope='module')
def valid_result():
    contract = room_contract(ORIGINAL['original_query'])
    return contract, compute(contract)


@pytest.mark.parametrize('mutation', ['failed_exit', 'timeout', 'stderr', 'no_receipt', 'other_code',
    'wrong_parameters', 'wrong_count', 'wrong_index', 'wrong_frequency', 'no_convergence', 'nonfinite', 'duplicate_json'])
def test_no_exit_zero_or_tampered_output_can_grant_physics(valid_result, mutation):
    contract, source = valid_result
    result = copy.deepcopy(source)
    data = json.loads(result['stdout'])
    if mutation == 'failed_exit': result['exit_code'] = 1
    elif mutation == 'timeout': result['timed_out'] = True
    elif mutation == 'stderr': result['stderr'] = 'warning'
    elif mutation == 'no_receipt': result['execution_evidence']['matched'] = False
    elif mutation == 'other_code': result['execution_evidence']['receipt']['code_sha256'] = '0'*64
    elif mutation == 'wrong_parameters': data['parameters']['length_x_m'] = 5
    elif mutation == 'wrong_count': data['modes'].pop()
    elif mutation == 'wrong_index': data['modes'][0]['nx'] = True
    elif mutation == 'wrong_frequency': data['modes'][0]['frequency_hz'] *= 2
    elif mutation == 'no_convergence': data['coarse_modes'] = data['modes']
    elif mutation == 'nonfinite': data['modes'][0]['frequency_hz'] = float('nan')
    if mutation == 'duplicate_json':
        result['stdout'] = result['stdout'].replace('{', '{"method":"fake",', 1)
    else:
        result['stdout'] = json.dumps(data)
    with pytest.raises(ValueError): room_modes.verify_result(contract, result)


def test_changed_source_or_unready_contract_cannot_build_code():
    contract = room_contract(ORIGINAL['original_query'])
    contract['parameters']['length_x_m'] = 5
    with pytest.raises(ValueError): room_modes.build_code(contract)
    missing = room_contract('二维矩形房间，四壁刚性，前10个模态')
    with pytest.raises(ValueError): room_modes.build_code(missing)
