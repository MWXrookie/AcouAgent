"""The skill list must preserve scientific distinctions in stored titles."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest


def test_catalog_titles_remain_distinct_in_browser():
    node = shutil.which('node')
    if node is None:
        pytest.skip('Node required to execute the actual browser title function')
    source = (Path(__file__).resolve().parents[2] / 'app/web/skills.html').read_text(encoding='utf-8')
    function = source[source.index('function skillName(item)'):source.index('function readableSummary')]
    # Actual titles produced by the fixed periodic catalog: the prefix encodes
    # medium and mode, while the suffix is deliberately shared.
    from scripts.validation.periodic_eigenmodes import title
    from app.skill_local_catalog import PERIODIC_RECIPES
    names = [title(scope)+'；二维周期边界，均匀无损、固定初压模态'
             for scope in PERIODIC_RECIPES]
    assert len(names) == 15 and len(set(names)) == 15
    items = [dict(state_text=name, scenario_type='initial_pressure_2d', status='active') for name in names]
    items += [dict(state_text='本机受控执行器；固定均匀水介质高斯初压', status='active'),
              dict(state_text='已记录仿真的未认证候选案例', status='candidate')]
    script = "const scenarioLabel=()=> '二维点声源';\n"+function+'\nconsole.log(JSON.stringify('+json.dumps(items,ensure_ascii=False)+'.map(skillName)));'
    result = subprocess.run([node, '-e', script], capture_output=True, text=True, encoding='utf-8', check=True)
    displayed = json.loads(result.stdout)
    assert displayed[:15] == names
    assert displayed[15:] == ['固定均匀水介质高斯初压', '二维点声源 · 待验证案例']


def test_frequency_copy_preserves_complete_physics_and_result_contract():
    node = shutil.which('node')
    if node is None:
        pytest.skip('Node required to execute browser request construction')
    root = Path(__file__).resolve().parents[2]
    source = (root/'app/web/skills.html').read_text(encoding='utf-8')
    helpers = source[source.index('function simulationParameters'):source.index('function readableSummary')]
    examples = [json.loads((root/'docs/skills-next/examples'/name).read_text(encoding='utf-8'))
                for name in ('manual-skill.json', 'b02-manual-skill.json', 'b03-manual-skill.json')]
    items = [dict(scenario_type=spec['scenario_type'], selected_version=dict(normalized_params=dict(
        normalizer_version='normalizer_v2', parameters=spec['parameters'], result_spec=spec['result_spec'])))
        for spec in examples]
    script = helpers+'\nconst items='+json.dumps(items)+';console.log(JSON.stringify(items.map(i=>({request:simulationRequest(i),params:simulationParameters(i.selected_version)}))));'
    result = subprocess.run([node, '-e', script], capture_output=True, text=True, encoding='utf-8', check=True)
    for spec, actual in zip(examples, json.loads(result.stdout)):
        assert actual['request'] == dict(scenario_type=spec['scenario_type'], simulation_params=spec['parameters'], result_spec=spec['result_spec'])
        assert actual['params'] == spec['parameters']
    script = helpers+"\nconst item={scenario_type:'initial_pressure_2d',selected_version:{normalized_params:{source_type:'modal_initial_pressure',sound_speed_m_s:1500}}};console.log(JSON.stringify(simulationRequest(item)));let rejected=false;try{simulationRequest({selected_version:{normalized_params:{normalizer_version:'normalizer_v2',parameters:{}}}})}catch(e){rejected=true}if(!rejected)process.exit(1);"
    result = subprocess.run([node, '-e', script], capture_output=True, text=True, encoding='utf-8', check=True)
    assert json.loads(result.stdout) == dict(scenario_type='initial_pressure_2d', source_type='modal_initial_pressure', sound_speed_m_s=1500)
