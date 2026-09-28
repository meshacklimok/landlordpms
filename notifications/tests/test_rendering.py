"""Template fields: only bare {names} from the type's list (D-044 item 7)."""

import pytest
from django.core.exceptions import ValidationError

from notifications import catalog
from notifications.rendering import SMS_MAX, fields_in, render, validate_body


def test_render_fills_fields_and_blanks_missing_ones():
    assert render("Hi {tenant_name},  bal {balance}. {receipt_link}", {"tenant_name": "W", "balance": 5}) \
        == "Hi W, bal 5."


def test_render_never_evaluates_attributes_or_indexes():
    assert render("{tenant_name.__class__} {tenant_name[0]} {tenant_name!r} {tenant_name:>9}", {"tenant_name": "W"}) \
        == ""


def test_render_keeps_escaped_braces_and_survives_bad_text():
    assert render("{{literal}} {tenant_name}", {"tenant_name": "W"}) == "{literal} W"
    assert render("oops {", {}) == "oops {"


@pytest.mark.parametrize("text", ["{a.b}", "{a[0]}", "{a!r}", "{a:>5}", "{0}", "{", "}"])
def test_fields_in_refuses_anything_but_bare_names(text):
    with pytest.raises(ValidationError):
        fields_in(text)


def test_validate_body_lists_unknown_fields_and_sms_length():
    ntype = catalog.get("announcement")
    validate_body(ntype, catalog.SMS, "{text} {org_name}")
    with pytest.raises(ValidationError, match="{secret}"):
        validate_body(ntype, catalog.SMS, "{text} {secret}")
    with pytest.raises(ValidationError, match="at most"):
        validate_body(ntype, catalog.SMS, "x" * (SMS_MAX + 1))


@pytest.mark.parametrize("ntype", catalog.TYPES, ids=lambda t: t.codename)
def test_every_default_body_uses_only_its_own_fields(ntype):
    assert ntype.bodies, "every type needs at least one default body"
    for (channel, language), body in ntype.bodies.items():
        assert language in catalog.LANGUAGES
        assert channel in ntype.channels
        validate_body(ntype, channel, body)
