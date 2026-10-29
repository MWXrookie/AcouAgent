import copy
import json
from pathlib import Path

import pytest

from app.task_contract import ConfirmationDrafts, room_contract

RAW = json.loads((Path(__file__).resolve().parents[1]/'fixtures/task_intake/room_reference.json').read_text(encoding='utf-8'))['original_query']
PLAIN = '二维矩形房间，刚性壁面，Lx=4 m，Ly=3 m，c=343 m/s，计算前10个模态频率。'


def test_user_latex_keeps_original_source_and_values_without_model_defaults():
    result = room_contract(RAW)
    assert result['status'] == 'ready_for_confirmation'
    assert result['issues'] == []
    assert result['source_text'] == RAW
    for key, value in dict(length_x_m=4, length_y_m=3, sound_speed_m_s=343, density_kg_m3=1.21, mode_count=10).items():
        field = next(f for f in result['fields'] if f['name'] == key)
        assert field['value'] == value and field['status'] == 'explicit'
        for source in field['sources']:
            assert RAW[source['start']:source['end']] == source['fragment']
    assert {p['id'] for p in result['proposals']} == {'exclude_zero_mode', 'ideal_uniform_empty_room'}
    assert 'source_frequency' not in result['parameters']


@pytest.mark.parametrize('lengths', ['Lx=400 cm，Ly=3000 mm', r'L_x=4.0\times10^{0}\ \mathrm{m}，L_y=3e0 m', '长4米，宽3米'])
def test_equivalent_unit_formats(lengths):
    result = room_contract('二维矩形房间，刚性壁面，'+lengths+'，声速343米/秒，求前十个模态频率。')
    assert result['status'] == 'ready_for_confirmation'
    assert result['parameters']['length_x_m'] == 4 and result['parameters']['length_y_m'] == 3


@pytest.mark.parametrize('query,field,state', [
    (PLAIN.replace('Ly=3 m，', ''), 'length_y_m', 'missing'),
    (PLAIN.replace('c=343 m/s', 'c=343'), 'sound_speed_m_s', 'unit_ambiguous'),
    (PLAIN.replace('Lx=4 m', 'Lx=4 m，Lx=5 m'), 'length_x_m', 'conflict'),
    (PLAIN.replace('Lx=4 m', 'Lx=-4 m'), 'length_x_m', 'invalid'),
    (PLAIN.replace('Ly=3 m', 'Ly=0 m'), 'length_y_m', 'invalid'),
    (PLAIN.replace('刚性壁面，', ''), 'boundary_condition', 'missing'),
])
def test_missing_conflict_and_invalid_never_become_ready(query, field, state):
    result = room_contract(query)
    assert result['status'] != 'ready_for_confirmation'
    assert any(i['field'] == field and i['status'] == state for i in result['issues'])


def test_changed_input_changes_confirmation_digest_but_does_not_invent_density():
    first = room_contract(PLAIN)
    assert 'density_kg_m3' not in first['parameters']
    assert not any(i['field'] == 'density_kg_m3' for i in first['issues'])
    assert first['contract_sha256'] != room_contract(PLAIN.replace('4 m', '5 m'))['contract_sha256']
    assert room_contract('水中1MHz点声源求声场') is None


def test_confirmation_binds_source_session_profile_digest_and_all_proposals():
    drafts = ConfirmationDrafts()
    contract = room_contract(PLAIN)
    key = drafts.put(contract, 'session', 'profile')
    accepted = [p['id'] for p in contract['proposals']]
    values = [key, contract['contract_sha256'], 'session', 'profile', PLAIN, accepted]
    for index, invalid in [(1, 'changed'), (2, 'other'), (3, 'other'), (4, PLAIN+'改为5米'), (5, [])]:
        changed = copy.deepcopy(values)
        changed[index] = invalid
        with pytest.raises(ValueError):
            drafts.consume(*changed)
    assert drafts.consume(*values) == contract
    with pytest.raises(ValueError, match='expired_or_consumed'):
        drafts.consume(*values)


def test_drafts_expire_and_capacity_is_bounded():
    clock = [0.]
    drafts = ConfirmationDrafts(clock=lambda: clock[0], ttl=10, limit=1)
    contract = room_contract(PLAIN)
    key = drafts.put(contract, 'session', None)
    with pytest.raises(ValueError, match='capacity'):
        drafts.put(contract, 'session', None)
    clock[0] = 11
    with pytest.raises(ValueError, match='expired'):
        drafts.consume(key, contract['contract_sha256'], 'session', None, PLAIN, [])
    assert drafts.put(contract, 'session', None)


@pytest.mark.parametrize('text,include', [('包含零模态', True), ('不包括零模态', False), ('不计入零频模态', False), ('不包含零模态', False)])
def test_zero_mode_policy_respects_negation(text, include):
    value = room_contract(PLAIN+text)
    assert value['parameters']['include_zero_mode'] is include
    assert not any(p['id'] == 'exclude_zero_mode' for p in value['proposals'])


def test_conflicting_zero_mode_policy_and_nonuniform_medium_are_not_ready():
    assert room_contract(PLAIN+'包含零模态，但不包括零模态')['status'] == 'needs_input'
    value = room_contract(PLAIN+'房内介质非均匀')
    assert value['status'] == 'unsupported'
    assert 'nonuniform_medium' in value['unsupported']


def test_replacing_or_cancelling_draft_invalidates_old_confirmation():
    drafts = ConfirmationDrafts(limit=1)
    first = room_contract(PLAIN)
    key = drafts.put(first, 's', None)
    second = room_contract(PLAIN.replace('4 m', '5 m'))
    with pytest.raises(ValueError):
        drafts.put(second, 'other', None, previous=key)
    new = drafts.put(second, 's', None, previous=key)
    with pytest.raises(ValueError, match='expired_or_consumed'):
        drafts.consume(key, first['contract_sha256'], 's', None, PLAIN, [p['id'] for p in first['proposals']])
    with pytest.raises(ValueError):
        drafts.cancel(new, 'other', None)
    assert drafts.cancel(new, 's', None)
    assert not drafts.cancel(new, 's', None)
