"""Run the production formatter: readability must preserve physical meaning."""
import json
from pathlib import Path
import re
import shutil
import subprocess

import pytest

ROOT = Path(__file__).parents[2]
FORMATTER = (ROOT/'app/web/scientific-text.js').read_text(encoding='utf-8')


def run_js(script):
    node = shutil.which('node')
    if not node:
        pytest.skip('Node required for actual browser formatter')
    return json.loads(subprocess.check_output([node, '-e', FORMATTER+'\n'+script],
                                              encoding='utf-8', timeout=10))


@pytest.mark.parametrize(('source', 'expected'), [
    (r'尺寸 $L_x = 4.0\ \mathrm{m}$，$L_y = 3.0\ \text{m}$。', '尺寸 Lx = 4.0 m，Ly = 3.0 m。'),
    (r'$\rho_0 = 1.21\ \text{kg/m}^3$，$c=343\ \mathrm{m/s}$', 'ρ₀ = 1.21 kg/m³，c=343 m/s'),
    (r'\( f=\frac{c}{2}\sqrt{(n_x/L_x)^2+(n_y/L_y)^2} \)', 'f=(c)/(2)sqrt((nx/Lx)²+(ny/Ly)²)'),
    (r'$$\frac{a+b}{\frac{c}{d}}$$', '(a+b)/((c)/(d))'),
    (r'\[10^{-3}\ \mathrm{m}=1\ \mathrm{mm}\]', '10⁻³ m=1 mm'),
    (r'$p=\sin(\omega t)\times 2\ \mathrm{Pa}$', 'p=sin(ω t)× 2 Pa'),
    (r'$x\leq y, x\neq 0, x\approx 1$', 'x≤ y, x≠ 0, x≈ 1'),
    (r'$\left(\frac{a}{b}\right)^2$', '((a)/(b))²'),
    (r'$\sqrt[3]{x+y}$', 'root(3, x+y)'),
    (r'密度 \rho_0，单位 \mathrm{kg/m}^3', '密度 ρ₀，单位 kg/m³'),
    (r'L_x = 4.0\ \text{m}', 'Lx = 4.0 m'),
    ('2 MHz, 0.000001 s, -1.25e-6 Pa', '2 MHz, 0.000001 s, -1.25e-6 Pa'),
    (r'`$x^2$`', r'`$x^2$`'),
    ('```python\ns = r"$x^2$"\n```', '```python\ns = r"$x^2$"\n```'),
    (r'https://example.com/$x^2$', r'https://example.com/$x^2$'),
    (r'C:\Users\ASUS\model.py', r'C:\Users\ASUS\model.py'),
    (r'费用 $20 and $30', r'费用 $20 and $30'),
    (r'预算 \$20', r'预算 \$20'),
    (r'$\unknown{a+b}$', r'\unknown{a+b}'),
])
def test_physics_notation_and_protected_content(source, expected):
    assert run_js('console.log(JSON.stringify(plainMathText('+json.dumps(source)+')));') == expected


def test_real_markdown_renderers_escape_formula_html_and_preserve_code_and_links():
    source = (ROOT/'app/dashboard.py').read_text(encoding='utf-8')
    escape = re.search(r'function esc\(s\) \{[^\n]+', source)[0]
    functions = re.findall(r'function renderMarkdown\(md\) \{.*?(?=\nfunction )', source, re.S)
    values = [r'$\rho_0=1.21\ \mathrm{kg/m}^3$',
              r'$\text{<img src=x onerror=alert(1)>}$',
              '```python\ns = r"$x^2$"\n```', '[source](https://example.com/$x$)']
    for fn in functions:
        actual = run_js(escape+'\n'+fn+'\nconsole.log(JSON.stringify('+json.dumps(values)+'.map(renderMarkdown)));')
        assert 'ρ₀=1.21 kg/m³' in actual[0]
        assert '<img ' not in actual[1] and '&lt;img' in actual[1]
        assert '$x^2$' in actual[2]
        assert 'href="https://example.com/$x$"' in actual[3]


@pytest.mark.parametrize('head', ['<head></head>', ''])
def test_shared_formatter_precedes_inline_page_handlers(head):
    from app.ui_chrome import render_page
    result = render_page('<html>'+head+'<body><script>plainMathText("$x$")</script></body></html>')
    assert result.index('globalThis.plainMathText=') < result.index('plainMathText("$x$")')
