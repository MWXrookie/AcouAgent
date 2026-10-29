"""Read-only family/case/version counts stay separate under repeated learning."""
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.skill_store import SkillStore
from app.skill_organization import display_parameters


def add(store, run, *, hz=500000, code="print('skill')", scenario='point_source_2d'):
    return store.create_candidate(scenario_type=scenario,state_text='可核对的测试案例',
        normalized_params=dict(frequency_hz=hz,dx_m=.00025),code_template=code,
        postconditions={},physics_evidence={},source_run_id=run,validator_version='unit-only',quality_level='Q2',
        idempotent_source_run=True)


def dump(store):
    with sqlite3.connect(store.path) as db:
        return list(db.iterdump())


def test_replay_ten_runs_new_case_and_code_version_have_distinct_counts(tmp_path):
    store = SkillStore(tmp_path/'skills.db')
    first = add(store,'source-one')
    for _ in range(10): assert add(store,'source-one') == first
    for i in range(10): assert add(store,f'new-run-{i}') == first
    one = store.list_metadata()['organization']
    assert (one['family_count'],one['case_count'],one['version_count'],one['reexecution_count']) == (1,1,1,0)
    assert add(store,'changed-code',code="print('different')") == first
    second = add(store,'changed-frequency',hz=600000)
    assert second != first
    add(store,'other-family',scenario='plane_wave_2d')
    before = dump(store)
    response = store.list_metadata(limit=1)
    assert len(response['items']) == 1
    stats = response['organization']
    assert (stats['family_count'],stats['case_count'],stats['version_count'],stats['reexecution_count']) == (2,3,4,0)
    assert stats['scope'] == 'whole_registered_library' and stats['trust_verified'] is False
    assert dump(store) == before


def test_concurrent_observations_do_not_create_ten_cases_or_versions(tmp_path):
    store = SkillStore(tmp_path/'skills.db')
    store.migrate()
    with ThreadPoolExecutor(max_workers=5) as pool:
        ids = list(pool.map(lambda i:add(store,f'parallel-{i}'),range(10)))
    assert len(set(ids)) == 1
    stats = store.list_metadata()['organization']
    assert stats['case_count'] == stats['version_count'] == stats['family_count'] == 1
    assert stats['reexecution_count'] == 0  # Candidate observations are not executions.


def test_archived_case_is_visible_only_in_explicit_archived_scope(tmp_path):
    store = SkillStore(tmp_path/'skills.db')
    first = add(store,'one')
    add(store,'two',hz=600000)
    with sqlite3.connect(store.path) as db:
        db.execute('UPDATE simulation_skills SET archived=1 WHERE skill_id=?',(first,))
    assert store.list_metadata()['organization']['case_count'] == 1
    assert store.list_metadata(include_archived=True)['organization']['case_count'] == 2
    assert store.list_metadata(include_archived=True)['organization']['includes_archived'] is True


def test_chat_rail_preserves_specific_scientific_names():
    node = shutil.which('node')
    if not node: pytest.skip('Node is required for actual rail formatter')
    code = (Path(__file__).parents[2]/'app/web/design.js').read_text(encoding='utf-8')
    function = code[code.index('function referenceName(item)'):code.index('if(chat){')]
    names = [f'介质{medium}；周期模态{mode}' for medium in range(3) for mode in range(5)]
    inputs = [dict(state_text=text,scenario_type='initial_pressure_2d',status='active') for text in names]
    inputs += [dict(state_text='受控执行器；具体科学用途',scenario_type='initial_pressure_2d',status='active')]
    child = subprocess.run([node,'-e',function+'\nconsole.log(JSON.stringify('+json.dumps(inputs)+'.map(referenceName)));'],
                           capture_output=True,text=True,encoding='utf-8',check=True,timeout=10)
    assert json.loads(child.stdout) == names+['具体科学用途']


def test_card_parameters_are_bounded_numeric_whitelist_not_raw_metadata():
    result = display_parameters(json.dumps(dict(normalizer_version='normalizer_v2', parameters=dict(
        frequency_hz=500000,sound_speed_m_s=1500,grid_shape=[32,32],t_end_s=.001,
        private_note='not returned',code='not returned'))))
    assert len(result) == 4
    assert [field['label'] for field in result] == ['频率','声速','网格','结束时间']
    assert 'not returned' not in json.dumps(result)
    assert display_parameters('{"frequency_hz":NaN}') == []
    assert display_parameters('{"grid_shape":[true,32]}') == []
    assert display_parameters('x'*8193) == []
