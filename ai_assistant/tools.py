"""The assistant's tools (D-091 item 3): read-only, permission-aware, scoped to the member's properties.

Each tool checks the capability its own page checks and reads through the same selectors, so a question
never reaches a figure the member could not open. Tools return plain dicts: names, amounts, dates and
statuses, never phone numbers, emails, ID numbers, notes or documents (item 4). `mask` is the second guard.
"""

import datetime
import re
from collections.abc import Callable
from dataclasses import dataclass

from django.core.exceptions import PermissionDenied
from django.db.models import prefetch_related_objects
from django.urls import reverse
from django.utils import timezone

from accounts.models import Membership
from accounts.permissions import can, visible_properties
from billing import selectors as billing_selectors
from billing.invoicing import lease_balance
from core.money import format_money
from leases.models import Lease
from leases.services import visible_leases
from maintenance.models import MaintenanceRequest
from maintenance.services import visible_requests
from payments.selectors import visible_payments
from properties import selectors as property_selectors
from properties.models import Property
from reports import metrics
from search import services as search_services
from support import help as help_topics

LIST_LIMIT = 25
NOT_ALLOWED = {"error": "not allowed: this member's role cannot see this. Say so; do not guess."}

# Phone numbers (Kenyan and international), emails, KRA PINs and long ID numbers (item 4).
_MASKS = (
    re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+"),
    re.compile(r"\+?\d{3}[\s-]?\d{3}[\s-]?\d{3}[\s-]?\d{3}"),
    re.compile(r"\b0[17]\d{2}[\s-]?\d{3}[\s-]?\d{3}\b"),
    re.compile(r"\b[AP]\d{9}[A-Z]\b"),
    re.compile(r"\b\d{7,}\b"),
)


def mask(text: str) -> str:
    for pattern in _MASKS:
        text = pattern.sub("[hidden]", text)
    return text


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    schema: dict
    run: Callable[[Membership, dict, datetime.date], dict]

    def spec(self) -> dict:
        return {"name": self.name, "description": self.description, "input_schema": self.schema}


def _text(value, limit=80) -> str:
    return str(value or "").strip()[:limit]


def _money(m: Membership, amount) -> str:
    return format_money(amount, m.organization.currency)


def _property(m: Membership, name: str) -> tuple[Property | None, dict | None]:
    """The member's property named `name` (exact, then partial), or an error to hand back."""
    name = _text(name)
    if not name:
        return None, None
    props = visible_properties(m, Property.objects.all())
    exact = list(props.filter(name__iexact=name)[:2]) or list(props.filter(code__iexact=name)[:2])
    found = exact or list(props.filter(name__icontains=name).order_by("name")[:6])
    if len(found) == 1:
        return found[0], None
    if not found:
        return None, {"error": f"no property this member can see matches {name!r}"}
    return None, {"error": "more than one property matches; ask which", "matches": [p.name for p in found]}


def tenant_names(m: Membership, lease: Lease) -> list[str] | None:
    if not can(m, "tenants.view"):
        return None
    return [lt.tenant.name for lt in sorted(lease.lease_tenants.all(), key=lambda lt: not lt.is_primary)]


# ---------------------------------------------------------------------------
# The tools
# ---------------------------------------------------------------------------


def find(m: Membership, args: dict, today: datetime.date) -> dict:
    results = search_services.search(m, _text(args.get("text"), search_services.MAX_LENGTH), limit=5)
    if results.too_short:
        return {"error": "give at least two characters to look for"}
    return {"groups": [{"kind": str(g.title), "more": g.more,
                        "items": [{"label": h.label, "detail": h.detail, "url": h.url} for h in g.hits]}
                       for g in results.groups]}


def who_owes(m: Membership, args: dict, today: datetime.date) -> dict:
    if not can(m, "invoices.view"):
        return NOT_ALLOWED
    prop, error = _property(m, args.get("property"))
    if error:
        return error
    rows = billing_selectors.arrears(m, today)
    if prop is not None:
        rows = [r for r in rows if r.lease.unit.property_id == prop.pk]
    min_days = max(int(args.get("min_days_overdue") or 0), 0)
    rows = [r for r in rows if r.days_overdue(today) >= min_days]
    rows.sort(key=lambda r: (-r.days_overdue(today), -r.balance))
    prefetch_related_objects([r.lease for r in rows[:LIST_LIMIT]], "lease_tenants__tenant", "unit__property")
    total = sum((r.balance for r in rows), 0)
    return {
        "leases_owing": len(rows), "total_owed": _money(m, total), "shown": min(len(rows), LIST_LIMIT),
        "rows": [{"tenants": tenant_names(m, r.lease), "unit": r.lease.unit.code,
                  "property": r.lease.unit.property.name, "owed": _money(m, r.balance),
                  "days_overdue": r.days_overdue(today),
                  "url": reverse("leases:detail", args=[r.lease.public_id])} for r in rows[:LIST_LIMIT]],
    }


def find_lease(m: Membership, ref, property_name) -> tuple[Lease | None, dict | None]:
    """The member's lease with this number, or the latest on this unit code, or an error to hand back."""
    ref = _text(ref, 40)
    if not ref:
        return None, {"error": "give a lease number or a unit code"}
    prop, error = _property(m, property_name)
    if error:
        return None, error
    leases = visible_leases(m).exclude(status=Lease.Status.DRAFT).select_related("unit__property")
    if prop is not None:
        leases = leases.filter(unit__property=prop)
    found = list(leases.filter(number__iexact=ref)[:1])
    if not found:
        on_unit = list(leases.filter(unit__code__iexact=ref).order_by("-start_date", "-pk")[:6])
        if len({x.unit_id for x in on_unit}) > 1:
            return None, {"error": "that unit code is used at more than one property; ask which",
                          "properties": sorted({x.unit.property.name for x in on_unit})}
        found = on_unit[:1]
    if not found:
        return None, {"error": f"no lease this member can see matches {ref!r}"}
    return found[0], None


def lease(m: Membership, args: dict, today: datetime.date) -> dict:
    if not can(m, "leases.view"):
        return NOT_ALLOWED
    lease, error = find_lease(m, args.get("lease_number_or_unit"), args.get("property"))
    if error:
        return error
    prefetch_related_objects([lease], "lease_tenants__tenant")
    out = {"number": lease.number, "status": lease.get_status_display(), "unit": lease.unit.code,
           "property": lease.unit.property.name, "tenants": tenant_names(m, lease),
           "rent": _money(m, lease.current_rent), "billing": lease.get_billing_frequency_display(),
           "start": lease.start_date.isoformat(), "end": lease.end_date.isoformat() if lease.end_date else None,
           "url": reverse("leases:detail", args=[lease.public_id])}
    if can(m, "invoices.view", lease.unit.property):
        balance = lease_balance(lease)
        out["balance"] = _money(m, balance)
        out["balance_means"] = "owed" if balance > 0 else "in credit" if balance < 0 else "settled"
    if can(m, "payments.view", lease.unit.property):
        out["last_payments"] = [
            {"date": p.paid_at.isoformat(), "amount": _money(m, p.amount), "method": p.get_method_display(),
             "status": p.get_status_display()}
            for p in visible_payments(m).filter(lease=lease).order_by("-paid_at", "-pk")[:5]]
    return out


def summary(m: Membership, args: dict, today: datetime.date) -> dict:
    month = today
    if args.get("month"):
        try:
            month = datetime.date.fromisoformat(_text(args["month"], 10)[:7] + "-01")
        except ValueError:
            return {"error": "give the month as YYYY-MM"}
    prop, error = _property(m, args.get("property"))
    if error:
        return error
    try:
        board = metrics.dashboard(m, month=month, property=prop, today=today)
    except PermissionDenied:
        return NOT_ALLOWED
    occ, cur = board.occupancy, board.current
    return {"month": cur.label, "property": prop.name if prop else "all this member can see",
            "units": occ.rentable, "occupied": occ.occupied, "vacant": occ.vacant, "occupancy": _pct(occ.rate),
            "rent_expected": _money(m, cur.expected), "rent_collected": _money(m, cur.collected),
            "collection_rate": _pct(cur.rate), "cash_received": _money(m, cur.cash),
            "overdue_today": {str(label): _money(m, amount) for _key, label, amount in board.arrears.buckets},
            "overdue_total": _money(m, board.arrears.total), "leases_overdue": board.arrears.leases,
            "label": "Dashboard", "url": reverse("reports:dashboard")}


def _pct(fraction) -> str | None:
    return None if fraction is None else f"{fraction * 100:.0f}%"


def vacant_units(m: Membership, args: dict, today: datetime.date) -> dict:
    if not can(m, "units.list_vacant"):
        return NOT_ALLOWED
    prop, error = _property(m, args.get("property"))
    if error:
        return error
    units = property_selectors.filter_units(property_selectors.visible_units(m), status="vacant")
    if prop is not None:
        units = units.filter(property=prop)
    units = units.select_related("property").order_by("property__name", "code")
    rows = list(units[:LIST_LIMIT])
    return {"vacant": units.count(), "shown": len(rows),
            "rows": [{"unit": u.code, "property": u.property.name, "type": u.get_unit_type_display(),
                      "rent": _money(m, u.list_rent) if u.list_rent else None,
                      "url": reverse("properties:unit", args=[u.public_id])} for u in rows]}


def open_repairs(m: Membership, args: dict, today: datetime.date) -> dict:
    if not (can(m, "maintenance.view") or can(m, "maintenance.view_assigned")):
        return NOT_ALLOWED
    prop, error = _property(m, args.get("property"))
    if error:
        return error
    reqs = visible_requests(m).filter(status__in=MaintenanceRequest.OPEN).select_related("property", "unit")
    if prop is not None:
        reqs = reqs.filter(property=prop)
    if args.get("only_late"):
        now = timezone.now()
        reqs = [r for r in reqs.order_by("due_at") if r.fix_state(now) == "late" or r.response_state(now) == "late"]
    else:
        reqs = list(reqs.order_by("due_at")[:200])
    now = timezone.now()
    return {"open": len(reqs), "shown": min(len(reqs), LIST_LIMIT),
            "rows": [{"number": r.number, "title": r.title, "property": r.property.name, "where": r.where,
                      "priority": r.get_priority_display(), "status": r.get_status_display(),
                      "late_to_start": r.response_state(now) == "late", "late_to_fix": r.fix_state(now) == "late",
                      "fix_by": timezone.localtime(r.due_at).date().isoformat(),
                      "url": reverse("maintenance:detail", args=[r.public_id])} for r in reqs[:LIST_LIMIT]]}


def how_to(m: Membership, args: dict, today: datetime.date) -> dict:
    """A help page's text, for questions on using the app; every member can read the help (D-063 item 2)."""
    slug = _text(args.get("topic"), 40)
    found = help_topics.plain_text(slug)
    if found is None:
        return {"error": f"there is no help topic {slug!r}", "topics": sorted(help_topics.SLUGS)}
    text, page_links = found
    return {"topic": str(help_topics.topic(slug)[1]), "text": text,
            "label": str(help_topics.topic(slug)[1]), "url": reverse("support:help_topic", args=[slug]),
            "pages": [{"label": label, "url": url} for label, url in page_links if url.startswith("/")]}


_PROPERTY = {"type": "string", "description": "Optional: a property name or code, to look at one property only."}

TOOLS: tuple[Tool, ...] = (
    Tool("find", "Find tenants, properties, units, leases, invoices and payments by name, code or number.",
         {"type": "object", "properties": {"text": {"type": "string", "description": "What to look for."}},
          "required": ["text"]}, find),
    Tool("who_owes", "Leases with money overdue today: tenants, unit, amount owed and days overdue, most overdue "
         "first.", {"type": "object", "properties": {
             "property": _PROPERTY,
             "min_days_overdue": {"type": "integer", "description": "Optional: only leases at least this late."}}},
         who_owes),
    Tool("lease", "One lease: tenants, unit, rent, dates, balance and its last payments.",
         {"type": "object", "properties": {
             "lease_number_or_unit": {"type": "string", "description": "A lease number or a unit code."},
             "property": _PROPERTY}, "required": ["lease_number_or_unit"]}, lease),
    Tool("summary", "One month's figures: occupancy, rent expected and collected, collection rate, cash received "
         "and what is overdue by age.", {"type": "object", "properties": {
             "month": {"type": "string", "description": "Optional: YYYY-MM. Default: this month."},
             "property": _PROPERTY}}, summary),
    Tool("vacant_units", "Units available to let, with their asking rent.",
         {"type": "object", "properties": {"property": _PROPERTY}}, vacant_units),
    Tool("open_repairs", "Unfinished repair requests with priority, status and whether they are late.",
         {"type": "object", "properties": {
             "property": _PROPERTY,
             "only_late": {"type": "boolean", "description": "Optional: only requests late to start or fix."}}},
         open_repairs),
    Tool("how_to", "The app's help on one topic: how to do something in the app, what a feature does, its limits "
         "and which page to use. Topics: " + "; ".join(f"{slug} ({desc})" for slug, _t, desc in help_topics.TOPICS)
         + ".", {"type": "object", "properties": {
             "topic": {"type": "string", "enum": [slug for slug, _t, _d in help_topics.TOPICS]}},
             "required": ["topic"]}, how_to),
)
BY_NAME = {t.name: t for t in TOOLS}


def run(m: Membership, name: str, args, today: datetime.date | None = None, registry: dict | None = None) -> dict:
    tool = (BY_NAME if registry is None else registry).get(name)
    if tool is None:
        return {"error": f"there is no tool called {name!r}"}
    try:
        return tool.run(m, args if isinstance(args, dict) else {}, today or timezone.localdate())
    except PermissionDenied:
        return NOT_ALLOWED
    except (TypeError, ValueError):
        return {"error": "the tool input was not understood"}


def links(result: dict) -> list[tuple[str, str]]:
    """(label, url) pairs found in a tool result, for "Pages used" under the answer (item 5)."""
    found = []

    def walk(value):
        if isinstance(value, dict):
            url = value.get("url")
            if isinstance(url, str) and url.startswith("/"):
                label = value.get("label") or value.get("number") or value.get("unit") or value.get("title") or ""
                found.append((str(label) or url, url))
            for v in value.values():
                walk(v)
        elif isinstance(value, list):
            for v in value:
                walk(v)

    walk(result)
    return found
