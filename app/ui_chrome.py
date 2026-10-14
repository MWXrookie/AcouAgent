"""Shared presentation only; no simulation or trust decisions."""
from pathlib import Path
def render_page(html: str) -> str:
    web=Path(__file__).parent / "web"
    assets="<style>"+(web/"design.css").read_text(encoding="utf-8")+"</style><script>"+(web/"design.js").read_text(encoding="utf-8")+"</script>"
    # Append after page-specific CSS and handlers, including legacy body styles.
    return html.replace("</body>", assets+"</body>") if "</body>" in html else html+assets
