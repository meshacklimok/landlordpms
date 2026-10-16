"""The key screens on a phone (D-096): tables scroll in their card, touch targets, number keyboards."""

import re
from pathlib import Path

import pytest
from django import forms
from django.conf import settings
from django.template.loader import render_to_string

from billing.forms import DepositForm

TEMPLATES = Path(settings.BASE_DIR) / "templates"


def test_every_table_is_inside_a_scroll_wrapper():
    unwrapped = []
    for path in sorted(TEMPLATES.rglob("*.html")):
        text = path.read_text(encoding="utf-8")
        for match in re.finditer(r"<table\b", text):
            before = text[:match.start()]
            if "table-responsive" not in before[before.rfind("<"):]:
                unwrapped.append(f"{path.relative_to(TEMPLATES)}:{text.count(chr(10), 0, match.start()) + 1}")
    assert unwrapped == []


class _Money(forms.Form):
    rent = forms.DecimalField()
    code = forms.CharField()


def test_number_fields_ask_for_the_number_keyboard():
    html = render_to_string("partials/form.html", {"form": _Money()})
    assert re.search(r'name="rent"[^>]*inputmode="decimal"', html)
    assert not re.search(r'name="code"[^>]*inputmode', html)


def test_the_deposit_amount_uses_the_number_keyboard():
    assert DepositForm().fields["amount"].widget.attrs["inputmode"] == "decimal"


@pytest.mark.django_db
def test_small_buttons_grow_on_touch_screens(client):
    assert "css/app.css" in client.get("/status/").content.decode()
    css = (Path(settings.BASE_DIR) / "static" / "css" / "app.css").read_text(encoding="utf-8")
    assert "@media (pointer: coarse)" in css and ".btn-sm" in css
