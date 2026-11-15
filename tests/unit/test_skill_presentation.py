"""Descriptions preserve declared physics; presentation never grants reuse."""
import copy
import json
from app.skill_presentation import present
from app.skill_local_catalog import PERIODIC_RECIPES, specification


def test_reviewed_periodic_cases_have_distinct_plain_names_and_physical_purposes():
    names=[]
    for scope in PERIODIC_RECIPES:
        spec=specification(scope);original=copy.deepcopy(spec['parameters'])
        view=present(spec['scene'],original,spec['state_text'],detail=True)
        names.append(view['title'])
        assert '；二维周期边界' not in view['title']
        assert view['purpose'] and '随时间' in (view['purpose']+view['output'])
        assert '周期边界' in view['boundary']
        assert not any('rad' in text or 'managed-' in text for text in view['setup'])
        assert original==spec['parameters']
    assert len(names)==len(set(names))==15
    assert '水中水平驻波' in names and '空气中45°斜向驻波' in names


def test_candidate_names_distinguish_time_and_metadata_matches_detail():
    p=dict(source_type='continuous_sinusoid',medium_type='water',sound_speed_m_s=1500,density_kg_m3=1000,
           solver_mode='time_domain',frequency_hz=100000,grid_shape=[64,64],t_end_s=.00002)
    a=present('point_source_2d',p,'已记录仿真的未认证候选案例')
    b=present('point_source_2d',dict(p,t_end_s=.00006),'已记录仿真的未认证候选案例')
    assert a['title']!=b['title'] and '20 μs' in a['title'] and '60 μs' in b['title']
    assert a['title']==present('point_source_2d',p,'已记录仿真的未认证候选案例',detail=True)['title']
    assert not {'quality_level','status','trust_verified'} & a.keys()


def test_description_uses_wavevectors_not_untrusted_old_mode_name():
    spec=specification(next(k for k in PERIODIC_RECIPES if 'water-axial' in k))
    p=spec['parameters'];p['source_geometry']='managed-local-periodic-air-oblique.v1'
    view=present(spec['scene'],p,'空气：45度斜向驻波；二维周期边界，均匀无损、固定初压模态',detail=True)
    assert view['title']=='水中水平驻波'
    p['modal_wavenumber_rad_per_m']=[[1,2]]
    assert '45°' not in present(spec['scene'],p,'',detail=True)['title']


def test_human_name_and_unknown_legacy_values_do_not_change_physics():
    p=dict(grid_shape=[32,32],t_end_s=.00002)
    assert present('point_source_2d',p,'我的水箱实验')['title']=='我的水箱实验'
    assert present('point_source_2d',p,'水：我的水箱实验')['title']=='水：我的水箱实验'
    detail=present('point_source_2d',p,'',detail=True)
    assert detail['boundary']=='未记录' and not any('声速' in x for x in detail['setup'])
    assert present('point_source_2d',dict(boundary_condition=[]),'',detail=True) is None
    assert present('point_source_2d','x'*8193) is None
    assert present('point_source_2d','not json') is None


def test_frequency_units_and_source_normalization_are_not_absolute_pressure():
    raw=dict(normalizer_version='normalizer_v2',parameters=dict(solver_mode='frequency_domain',
        frequency_hz=238732.414637843,medium_type='homogeneous_water',grid_shape=[128,128],boundary_condition='absorbing_pml'))
    text=present('point_source_2d',json.dumps(raw),'Automatic controlled source',detail=True)
    assert '238.732 kHz' in text['title'] and '数值源归一化' in text['output']
    assert raw['parameters']['frequency_hz']==238732.414637843
