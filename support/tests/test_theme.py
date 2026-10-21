"""Light and dark mode and the flat look (D-111)."""

import re
from pathlib import Path

import pytest
from django.conf import settings

BASE = Path(settings.BASE_DIR)
TEMPLATES = BASE / "templates"
STATIC = BASE / "static"


def _files(*patterns):
    """Our own templates, styles and scripts. static/vendor holds libraries as published (Bootstrap ships a
    .bg-gradient class); templates are checked, so it is never used."""
    for root in (TEMPLATES, STATIC):
        for pattern in patterns:
            yield from (p for p in root.rglob(pattern) if "vendor" not in p.relative_to(root).parts)


def test_no_gradients_anywhere():
    found = [str(p.relative_to(BASE)) for p in _files("*.html", "*.css", "*.js")
             if re.search(r"gradient\(|bg-gradient|createLinearGradient|createRadialGradient", p.read_text(encoding="utf-8"), re.I)]
    assert found == []


def test_colours_live_in_the_tokens_not_in_templates():
    """Templates use Bootstrap classes; a hard-coded colour would stay the same in dark mode."""
    found = []
    for path in TEMPLATES.rglob("*.html"):
        if path.name == "offline.html":  # stands alone when offline; has its own light and dark colours
            continue
        text = path.read_text(encoding="utf-8")
        found += [f"{path.relative_to(TEMPLATES)}: {m}" for m in re.findall(r"style=\"[^\"]*(?:color|background)[^\"]*\"", text)]
        found += [f"{path.relative_to(TEMPLATES)}: {m}" for m in re.findall(r"\b(?:bg-white|link-dark|text-black)\b", text)]
    assert found == []


@pytest.mark.django_db
def test_every_page_loads_the_theme_before_drawing(client):
    page = client.get("/status/").content.decode()
    head = page[:page.index("</head>")]
    assert "css/app.css" in head
    script = re.search(r'<script src="[^"]*js/theme\.js(?:\?v=\w+)?"[^>]*>', head)
    assert script and "defer" not in script.group(0) and "async" not in script.group(0)


@pytest.mark.django_db
def test_signed_out_pages_offer_the_theme_switch(client):
    page = client.get("/status/").content.decode()
    for value in ("light", "dark", "auto"):
        assert f'data-theme-value="{value}"' in page


def test_both_palettes_are_defined():
    css = (STATIC / "css" / "app.css").read_text(encoding="utf-8")
    assert ":root, [data-bs-theme=light]" in css and "[data-bs-theme=dark]" in css
    for token in ("--app-canvas", "--bs-body-bg", "--bs-primary", "--chart-1", "--chart-late-4"):
        light, dark = css.split("[data-bs-theme=dark] {", 1)
        assert token in light and token in dark, token
    assert "#000000" not in css.lower()  # dark mode is a dark grey, never pure black


def test_the_font_is_served_from_here():
    """Self-hosted, so the Content Security Policy needs no font CDN and the app works offline."""
    assert (STATIC / "fonts" / "inter-latin-wght-normal.woff2").exists()
    assert (STATIC / "fonts" / "Inter-LICENSE.txt").exists()


def test_the_offline_page_follows_dark_mode():
    assert "prefers-color-scheme: dark" in (TEMPLATES / "support" / "offline.html").read_text(encoding="utf-8")
