"""Pre-execution provenance/confirmation, not acoustic solver acceptance."""
import asyncio
import copy
import json
from types import SimpleNamespace

import pytest

from app import local_workflow, skill_local_catalog, workflow_contract as contract

AUDIT = dict(profile_id='unit-profile',model='unit-model')
PARAMS = dict(sound_speed=1500.,density=1000.,source_frequency=100000.,domain_N=[64,64],
              domain_dx=[.001,.001],t_end=.00002,cfl=.3,pml_size=8,solver_mode='time_domain',
              medium_type='homogeneous_water',source_type='tone_burst',source_cycles=2,
              source_geometry='point',source_index=[32,32],source_amplitude_pa=1.,
              source_normalization='jwave_additive_pressure_coefficient',boundary_condition='absorbing_pml')


def build(query='水中点声源，声速1500m/s，密度1000kg/m3。',params=None,**kwargs):
    params = copy.deepcopy(PARAMS if params is None else params)
    return contract.build_contract(query,dict(scenario_type='point_source_2d',simulation_params=params,skill_query=None),
                                   AUDIT,'profile',True,**kwargs)


def test_model_values_without_independent_spans_are_proposals_not_user_declarations():
    value = build()
    assert value['status'] == 'ready_for_confirmation'
    by_name = {f['name']:f for f in value['fields']}
    assert by_name['sound_speed']['status'] == by_name['density']['status'] == 'explicit'
    assert by_name['source_frequency']['status'] == 'model_proposed'
    assert 'parameter:source_frequency' in {p['id'] for p in value['proposals']}
    assert by_name['sound_speed']['sources'][0]['fragment'] == '声速1500m/s'
    public = contract.public_contract(value)
    assert not any(k.startswith('_') for k in public)
    assert public['contract_sha256'] == value['contract_sha256']


@pytest.mark.parametrize('query', ['水中点声源，声速343m/s。','水中点声源，声速343m/s，又给声速1500m/s。',
                                   '水中点声源，声速1500。'])
def test_source_disagreement_conflict_and_ambiguous_unit_cannot_confirm(query):
    result = build(query)
    assert result['status'] == 'needs_input'
    assert any(i['field']=='sound_speed' for i in result['issues'])


def test_missing_time_is_not_silently_optional_or_filled_from_skills():
    params = dict(PARAMS)
    del params['t_end']
    result = build(params=params)
    assert result['status'] == 'needs_input'
    assert any(i['field']=='t_end' and i['status']=='missing' for i in result['issues'])
    assert 't_end' not in result['parameters']


def test_task_kind_and_explicit_boundary_mismatches_stop_before_execution():
    for query in ('周期边界的初始压力声波传播。','求频域Helmholtz声压。','刚性壁面中的点声源声场。'):
        result = build(query)
        assert result['status'] == 'needs_input' and result['issues']


def test_frozen_confirmation_is_bound_to_internal_values_source_and_profile():
    value = build()
    frozen = contract.frozen_extraction(value,'profile')
    assert frozen.source_text == value['source_text'] and frozen.extracted['simulation_params'] == PARAMS
    with pytest.raises(ValueError): contract.frozen_extraction(value,'other')
    changed = copy.deepcopy(value)
    changed['_extracted']['simulation_params']['sound_speed'] = 343
    with pytest.raises(ValueError): contract.frozen_extraction(changed,'profile')
    frozen.extracted['simulation_params']['sound_speed'] = 340
    assert value['_extracted']['simulation_params']['sound_speed'] == 1500


def test_explicit_initial_copy_needs_no_extraction_model_and_keeps_user_bytes(monkeypatch):
    params = copy.deepcopy(skill_local_catalog.specification('managed-local-periodic-air-axial.v1')['parameters'])
    query = '请计算声波传播：\n'+json.dumps(dict(params,scenario_type='initial_pressure_2d'),ensure_ascii=False)
    async def forbidden(*args): raise AssertionError('explicit declaration must not call extraction model')
    monkeypatch.setattr(local_workflow,'extract_parameters',forbidden)
    monkeypatch.setattr(contract.model_profiles,'resolve',lambda *a:(SimpleNamespace(audit_profile_id=lambda:'unit-profile',model='unit-model'),{}))
    value = asyncio.run(contract.prepare(query,'profile'))
    assert value['status'] == 'ready_for_confirmation' and value['proposals'] == []
    assert value['source_text'] == query and value['_extracted']['simulation_params'] == params
    assert all(f['status']=='explicit' for f in value['fields'])
    assert value['_extraction_origin'] == 'explicit_declaration'


def test_unknown_task_and_excessive_input_do_not_call_a_model(monkeypatch):
    async def forbidden(*args): raise AssertionError('must not extract this task')
    monkeypatch.setattr(local_workflow,'extract_parameters',forbidden)
    assert asyncio.run(contract.prepare('请模拟一根梁的伸长','profile'))['status'] == 'unsupported'
    assert asyncio.run(contract.prepare('请计算管道内声波的本征频率','profile'))['status'] == 'unsupported'
    with pytest.raises(ValueError): asyncio.run(contract.prepare('声'*65537,'profile'))


@pytest.mark.parametrize('query,field',[
    ('水中点声源，频率200kHz。','source_frequency'),
    ('水中点声源，频率100kHz又写频率200kHz。','source_frequency'),
    ('水中点声源，频率100。','source_frequency'),
    ('水中点声源，结束时间60us。','t_end'),
])
def test_frequency_and_time_mentions_are_independent_of_model_claims(query,field):
    value = build(query)
    assert value['status']=='needs_input'
    assert any(i['field']==field for i in value['issues'])


def test_frequency_and_time_si_conversion_keeps_exact_source_spans():
    query = '水中点声源，频率0.1MHz，结束时间20微秒。'
    value = build(query)
    assert value['status']=='ready_for_confirmation'
    fields = {f['name']:f for f in value['fields']}
    assert fields['source_frequency']['status']==fields['t_end']['status']=='explicit'
    assert fields['source_frequency']['sources'][0]['fragment']=='频率0.1MHz'
    assert fields['t_end']['sources'][0]['fragment']=='结束时间20微秒'


def test_boolean_cannot_impersonate_a_numeric_physical_value():
    result = build(params=dict(PARAMS,source_frequency=True))
    assert result['status']=='needs_input'
    assert any(i['field']=='source_frequency' for i in result['issues'])


def test_actual_model_scalar_grid_is_expanded_only_from_explicit_equal_axes():
    params = dict(PARAMS,domain_N=64,domain_dx=.001)
    result = build('二维水中点声源，网格64×64，网格间距1mm×1mm。',params)
    assert result['status']=='ready_for_confirmation'
    assert result['parameters']['domain_N']==[64,64] and result['parameters']['domain_dx']==[.001,.001]
    fields = {f['name']:f for f in result['fields']}
    assert fields['domain_N']['status']==fields['domain_dx']['status']=='derived'
    assert fields['domain_N']['sources'][0]['fragment']=='网格64×64'
    assert result['_extracted']['simulation_params']['domain_N']==[64,64]
    # This new source contract has no certified library adapter yet. It must
    # not silently fall back to a weaker, partial retrieval identity.
    assert result['_extracted']['skill_query'] is None


def test_grid_axis_loss_or_conflicting_declarations_cannot_confirm():
    for query in ('水中点声源，网格64×32。','水中点声源，网格64×64又写网格64×32。'):
        result = build(query,dict(PARAMS,domain_N=64))
        assert result['status']=='needs_input' and any(i['field']=='domain_N' for i in result['issues'])


def test_underdeclared_source_waveform_strength_and_location_are_not_hidden_defaults():
    params = {key:value for key,value in PARAMS.items() if key not in {'source_type','source_cycles','source_amplitude_pa','source_normalization','source_index'}}
    result = build(params=params)
    assert result['status']=='needs_input'
    assert {'source_type','source_amplitude_pa','source_normalization','source_index'} <= {i['field'] for i in result['issues']}
    assert 'source_amplitude_pa' not in result['parameters']


def test_tone_burst_is_not_silently_truncated_or_put_inside_absorbing_layers():
    for params in (dict(PARAMS,source_cycles=3),dict(PARAMS,source_index=[0,0])):
        result = build(params=params)
        assert result['status']=='needs_input'


def test_actual_model_si_aliases_do_not_make_explicit_parameters_appear_missing():
    params = dict(PARAMS)
    for old,new in [('sound_speed','sound_speed_m_s'),('density','density_kg_m3'),
                    ('source_frequency','source_frequency_hz'),('domain_dx','domain_dx_m'),('t_end','t_end_s')]:
        params[new] = params.pop(old)
    result = build('水中点声源，声速1500m/s，密度1000kg/m³，频率100kHz，网格64×64，网格间距1mm×1mm，结束时间20us。',params)
    assert result['status']=='ready_for_confirmation'
    assert result['parameters']['sound_speed']==1500 and result['parameters']['domain_dx']==[.001,.001]
    assert not any(i['status']=='missing' for i in result['issues'])


def test_null_source_conditions_are_missing_not_silently_confirmable_proposals():
    result = build(params=dict(PARAMS,source_amplitude_pa=None,source_index=None))
    assert result['status']=='needs_input'
    assert all(i['status']=='missing' for i in result['issues'] if i['field'] in {'source_amplitude_pa','source_index'})


def test_structural_source_fields_and_pressure_coefficient_match_the_literal_input():
    query = ('二维水中时域点声源，source_type=tone_burst，source_cycles=2，source_index=[32,32]，'
             'source_amplitude_pa=1 Pa，source_normalization=jwave_additive_pressure_coefficient。')
    value = build(query)
    assert value['status']=='ready_for_confirmation'
    fields = {f['name']:f for f in value['fields']}
    assert all(fields[name]['status']=='explicit' for name in ('source_type','source_cycles','source_index','source_amplitude_pa','source_normalization'))
    changed = build(query,dict(PARAMS,source_cycles=3,t_end=.00004))
    assert changed['status']=='needs_input' and any(i['field']=='source_cycles' for i in changed['issues'])


@pytest.mark.parametrize('value',[0,True,False,.4])
def test_nonpositive_or_boolean_cfl_is_not_a_confirmable_numerical_step(value):
    result = build(params=dict(PARAMS,cfl=value))
    assert result['status']=='needs_input' and any(i['field']=='cfl' for i in result['issues'])


def test_dimension_mismatch_cannot_be_hidden_by_model_scenario_selection():
    result = build('三维水中点声源。')
    assert result['status']=='needs_input' and any(i['field']=='dimensions' for i in result['issues'])
