"""A second model-produced parameter copy must not redirect skill execution."""
import copy
import json

import pytest

from app.local_workflow import _skill_query

PARAMS = dict(sound_speed=1500.,density=1000.,source_frequency=100000.,domain_N=[64,64],
              domain_dx=[.001,.001],t_end=.00002,cfl=.3,pml_size=8)


@pytest.mark.parametrize('field,value', [('source_frequency',200000.),('t_end',.00006),
    ('domain_N',[128,128]),('density',1.21),('pml_size',0)])
def test_mismatching_model_retrieval_copy_cannot_select_a_skill(field,value):
    claimed = copy.deepcopy(PARAMS)
    claimed[field] = value
    assert _skill_query(dict(scenario_type='point_source_2d',simulation_params=PARAMS,
        skill_query=dict(scenario_type='point_source_2d',parameters=claimed))) is None


def test_retrieval_scenario_must_equal_the_actual_simulation_scenario():
    assert _skill_query(dict(scenario_type='point_source_2d',simulation_params=PARAMS,
        skill_query=dict(scenario_type='plane_wave_2d',parameters=PARAMS))) is None


def test_equivalent_declared_aliases_can_match_without_inventing_physics():
    primary = dict(PARAMS)
    claimed = dict(sound_speed_m_s=1500.,density_kg_m3=1000.,frequency_hz=100000.,grid_shape=[64,64],
                   dx_m=[.001,.001],t_end_s=.00002,cfl=.3,pml_size=8)
    result = _skill_query(dict(scenario_type='point_source_2d',simulation_params=primary,
        skill_query=dict(scenario_type='point_source_2d',parameters=claimed)))
    assert result is not None
    params = json.loads(result[1])
    assert params['frequency_hz'] == 100000. and params['t_end_s'] == .00002
    assert 'source_type' not in params and 'boundary_condition' not in params
    assert params['dimensions'] == len(params['grid_shape']) == 2


def test_conflicting_parameter_aliases_cannot_be_overwritten_by_iteration_order():
    primary = dict(PARAMS,density_kg_m3=1.21)
    assert _skill_query(dict(scenario_type='point_source_2d',simulation_params=primary,
        skill_query=dict(scenario_type='point_source_2d',parameters=primary))) is None
