"""What the organization wants the assistant to know (D-119): entries written by an owner, read by the assistant.

Owners (organization.manage) add, change and remove entries on the knowledge page. The staff assistant's
`knowledge` tool reads entries on the properties the member can see; the tenant assistant's `landlord_notes`
reads only entries marked for tenants, on the tenant's own properties. Phone numbers, emails and long numbers are
refused when saving, because the assistant never sends them to the model (D-091 item 4).
"""

import re

from django.core.exceptions import ValidationError
from django.db.models import Q
from django.utils.translation import gettext as _

from accounts.models import Membership
from accounts.permissions import require, visible_properties
from audit import services as audit
from properties.models import Property

from . import tools
from .models import KnowledgeEntry

MAX_ENTRIES = 100
# Under this many characters every entry is given, so wording and language do not matter; above it, the best matches.
SEND_ALL_UNDER = 6000
MAX_MATCHES = 5
_WORD = re.compile(r"\w{3,}", re.UNICODE)


# ---------------------------------------------------------------------------
# Managing entries
# ---------------------------------------------------------------------------


def entries(m: Membership):
    return KnowledgeEntry.objects.for_org(m.organization).select_related("property")


def _check(m: Membership, entry: KnowledgeEntry) -> None:
    errors = {}
    entry.title = " ".join(str(entry.title or "").split())
    entry.answer = str(entry.answer or "").strip()
    for field in ("title", "answer"):
        value, limit = getattr(entry, field), KnowledgeEntry._meta.get_field(field).max_length
        if not value:
            errors[field] = _("This field is required.")
        elif len(value) > limit:
            errors[field] = _("Keep it under %(n)s characters.") % {"n": limit}
        elif tools.mask(value) != value:
            errors[field] = _("Leave out phone numbers, emails, ID numbers and other long numbers: the assistant "
                              "never passes them on. Say where to find them instead, such as the office.")
    if entry.property_id and entry.property.organization_id != m.organization_id:
        errors["property"] = _("Choose one of your properties.")
    if errors:
        raise ValidationError(errors)


def save(m: Membership, entry: KnowledgeEntry, *, request=None) -> KnowledgeEntry:
    """Adds or changes an entry; the caller has filled in title, answer, property and for_tenants."""
    require(m, "organization.manage")
    new = entry.pk is None
    if new:
        if entries(m).count() >= MAX_ENTRIES:
            raise ValidationError(_("You can keep at most %(n)s entries. Remove one you no longer need.")
                                  % {"n": MAX_ENTRIES})
        entry.organization, entry.created_by = m.organization, m.user
    elif entry.organization_id != m.organization_id:
        raise ValidationError(_("That entry is not in your organization."))
    _check(m, entry)
    entry.updated_by = m.user
    entry.save()
    audit.record("assistant.knowledge_added" if new else "assistant.knowledge_changed", actor=m.user,
                 organization=m.organization, obj=entry, request=request,
                 changes={"title": [None, entry.title], "for_tenants": [None, entry.for_tenants],
                          "property": [None, entry.property.name if entry.property_id else None]})
    return entry


def delete(m: Membership, entry: KnowledgeEntry, *, request=None) -> None:
    require(m, "organization.manage")
    if entry.organization_id != m.organization_id:
        raise ValidationError(_("That entry is not in your organization."))
    audit.record("assistant.knowledge_removed", actor=m.user, organization=m.organization, obj=entry,
                 request=request, changes={"title": [entry.title, None]})
    entry.delete()


def property_choices(m: Membership):
    return visible_properties(m, Property.objects.all()).order_by("name")


# ---------------------------------------------------------------------------
# Reading entries for the assistant
# ---------------------------------------------------------------------------


def _pick(rows: list[KnowledgeEntry], text: str) -> dict:
    """Every entry when they are short together, else the entries sharing the most words with `text`."""
    if sum(len(r.title) + len(r.answer) for r in rows) <= SEND_ALL_UNDER:
        chosen, more = rows, False
    else:
        words = {w.lower() for w in _WORD.findall(text or "")}
        scored = []
        for r in rows:
            title, answer = set(_WORD.findall(r.title.lower())), set(_WORD.findall(r.answer.lower()))
            score = 2 * len(words & title) + len(words & answer)
            if score:
                scored.append((-score, r.title, r))
        chosen = [r for _s, _t, r in sorted(scored, key=lambda x: x[:2])[:MAX_MATCHES]]
        more = True
        if not chosen:
            return {"entries": [], "titles": [r.title for r in rows],
                    "note": "no entry matched those words; if a title fits, look again with its words"}
    return {"entries": [{"title": r.title, "answer": r.answer,
                         "property": r.property.name if r.property_id else "all properties"} for r in chosen],
            "only_best_matches": more}


def for_staff(m: Membership, args: dict, today) -> dict:
    props = visible_properties(m, Property.objects.all())
    rows = list(entries(m).filter(Q(property__isnull=True) | Q(property__in=props)))
    if not rows:
        return {"entries": [], "note": "the organization has written nothing for the assistant yet"}
    return _pick(rows, tools._text(args.get("text"), 200))


def for_tenant(leases, text: str) -> dict:
    """Entries marked for tenants in the tenant's landlords' organizations, on their own properties or all."""
    query = Q()
    for lease in leases:
        query |= Q(organization_id=lease.organization_id) & (
            Q(property__isnull=True) | Q(property_id=lease.unit.property_id))
    rows = list(KnowledgeEntry.objects.filter(query, for_tenants=True).select_related("property")) if leases else []
    if not rows:
        return {"entries": [], "note": "the landlord has written nothing on this; tell them to contact the landlord"}
    return _pick(rows, text)


TEXT = {"type": "string", "description": "The question in a few words, to pick the matching entries."}

STAFF_TOOL = tools.Tool(
    "knowledge", "What your organization wrote for the assistant: its own rules, policies and practices, such as "
    "office hours, deposits and refunds, house rules, who to call, when water is billed. Check it for questions "
    "about how this organization does things.",
    {"type": "object", "properties": {"text": TEXT}, "required": ["text"]}, for_staff)
