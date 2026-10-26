"""Every page with a chart loads the scripts that draw it (D-114): a missing one leaves an empty box, with no error."""

from pathlib import Path

from django.conf import settings

TEMPLATES = Path(settings.BASE_DIR) / "templates"
# Partials whose pages load the scripts once for all of them.
LOADED_BY_PARENT = {"console/_chart.html"}


def test_pages_with_charts_load_chart_js_and_charts_js():
    missing = []
    for path in TEMPLATES.rglob("*.html"):
        name = path.relative_to(TEMPLATES).as_posix()
        text = path.read_text(encoding="utf-8")
        if "data-chart=" not in text or name in LOADED_BY_PARENT:
            continue
        if "vendor/chartjs-" not in text or "js/charts.js" not in text:
            missing.append(name)
    assert missing == []
