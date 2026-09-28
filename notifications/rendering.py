"""Message text: default or organization wording, filled with `{placeholder}` fields (D-044 item 7).

Staff can edit templates, so the text is never run through the Django template engine.
Only bare `{name}` fields from the type's placeholder list are allowed: no attribute
access, indexing or format specs.
"""

import string

from django.core.exceptions import ValidationError
from django.utils.translation import gettext as _

from . import catalog
from .models import MessageTemplate

SMS_SEGMENT = 160
SMS_MAX = 3 * 153  # three concatenated segments


def fields_in(text: str) -> list[str]:
    """The placeholder names in `text`. Raises ValidationError for anything but bare names."""
    names = []
    try:
        parsed = list(string.Formatter().parse(text))
    except ValueError:
        raise ValidationError(_("Unmatched { or } in the text. Use {{ and }} for a literal brace.")) from None
    for _literal, name, spec, conversion in parsed:
        if name is None:
            continue
        if not name.isidentifier() or spec or conversion:
            raise ValidationError(_("“{%(field)s}” is not a valid field.") % {"field": name})
        names.append(name)
    return names


def validate_body(ntype: catalog.NotificationType, channel: str, text: str) -> None:
    unknown = sorted(set(fields_in(text)) - set(ntype.placeholders))
    if unknown:
        raise ValidationError(_("Unknown fields: %(fields)s. You can use: %(allowed)s.") % {
            "fields": ", ".join("{" + f + "}" for f in unknown),
            "allowed": ", ".join("{" + f + "}" for f in ntype.placeholders)})
    if channel == catalog.SMS and len(text) > SMS_MAX:
        raise ValidationError(_("An SMS can be at most %(max)s characters.") % {"max": SMS_MAX})


def template_for(org, ntype: catalog.NotificationType, channel: str, language: str) -> str | None:
    """The organization's wording if it has one (in the language, else English), else the default."""
    overrides = {t.language: t.body for t in MessageTemplate.objects.filter(
        organization=org, type=ntype.codename, channel=channel, language__in={language, catalog.EN})}
    return (overrides.get(language) or ntype.bodies.get((channel, language))
            or overrides.get(catalog.EN) or ntype.bodies.get((channel, catalog.EN)))


def render(text: str, context: dict) -> str:
    """Fills bare `{name}` fields; a field with no value becomes empty. Extra spaces are collapsed.

    Anything else in braces is dropped rather than evaluated, even in text that skipped validation.
    """
    try:
        parsed = list(string.Formatter().parse(text))
    except ValueError:
        parsed = [(text, None, None, None)]
    out = []
    for literal, name, spec, conversion in parsed:
        out.append(literal)
        if name and name.isidentifier() and not spec and not conversion:
            value = context.get(name)
            out.append("" if value is None else str(value))
    return " ".join("".join(out).split())
