"""Shared presentation only; no simulation or trust decisions."""
from pathlib import Path
def render_page(html: str) -> str:
    web=Path(__file__).parent / "web"
    # Available before page handlers; conversion affects presentation only.
    plain_text='<script>'+(web/'scientific-text.js').read_text(encoding='utf-8')+'</script>'
    # Several existing pages omit an explicit <head>. Insert before their first
    # handler rather than relying on that optional HTML element.
    html=html.replace('<script', plain_text+'<script', 1) if '<script' in html else html+plain_text
    assets="<style>"+(web/"design.css").read_text(encoding="utf-8")+"</style><script>"+(web/"design.js").read_text(encoding="utf-8")+"</script>"
    if 'id="query-input"' in html:
        assets += '<script>'+(web/'task-intake.js').read_text(encoding='utf-8')+'</script>'
    # Append after page-specific CSS and handlers, including legacy body styles.
    return html.replace("</body>", assets+"</body>") if "</body>" in html else html+assets
