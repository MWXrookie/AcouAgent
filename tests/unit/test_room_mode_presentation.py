"""Execute the actual browser formatters, including hostile Markdown inputs."""
import json
from pathlib import Path
import re
import shutil
import subprocess

import pytest

ROOT = Path(__file__).parents[2]


def node(script):
    executable = shutil.which('node')
    if executable is None:
        pytest.skip('Node is required to execute browser formatters')
    result = subprocess.run([executable, '-e', script], capture_output=True, text=True,
                            encoding='utf-8', check=True, timeout=10)
    return json.loads(result.stdout)


def test_report_reference_is_clickable_without_enabling_script_links_or_html():
    source = (ROOT/'app/dashboard.py').read_text(encoding='utf-8')
    functions = re.findall(r'function renderMarkdown\(md\) \{.*?(?=\nfunction )',source,re.S)
    escape = re.search(r'function esc\(s\) \{[^\n]+', source)[0]
    assert len(functions) == 2
    values = ['[依据](https://www.mathworks.com/help/audio/ug/find-room-modes-with-finite-element-analysis.html)',
              '[bad](javascript:alert(1))', '[bad](https://example.com/"onclick="alert(1))',
              '<img src=x onerror=alert(1)>', '```\n[code](https://example.com)\n```']
    for fn in functions:
        result = node(escape+'\n'+fn+'\nconsole.log(JSON.stringify('+json.dumps(values)+'.map(renderMarkdown)));')
        assert '<a href="https://www.mathworks.com/' in result[0]
        assert 'rel="noopener noreferrer"' in result[0]
        assert '<a ' not in result[1] and '<a ' not in result[2]
        assert '<img ' not in result[3] and '&lt;img' in result[3]
        assert '<a ' not in result[4] and '[code]' in result[4]


def test_modal_metric_has_hz_and_does_not_treat_eigenmodes_as_pressure():
    source = (ROOT/'app/web/dashboard.html').read_text(encoding='utf-8')
    fn = source[source.index('function analysisUnit'):source.index('function renderRow')]
    def row(modes, **parameters):
        return dict(tool_name='controlled_room_modes_2d', stdout=json.dumps(dict(
            method='neumann-cell-centred-finite-volume.v1', modes=modes, parameters=parameters)))
    values = [row([dict(frequency_hz=0),dict(frequency_hz=42.875)]),
              row([dict(frequency_hz=0)],include_zero_mode=True,mode_count=1),
              dict(analysis=dict(max_pressure=1,units='Pa'))]
    result = node(fn+'\nconsole.log(JSON.stringify('+json.dumps(values)+'.map(formatMetric)));')
    assert result == ['最低非零模态 42.875000 Hz','零频静态模态 0 Hz','1.00000 Pa']
