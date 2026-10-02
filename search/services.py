"""Global search (D-054): one box that finds tenants, properties, units, leases, invoices, payments
and M-Pesa transactions by name, phone, code, number or reference.

Every group follows the same rules as its own list page: the member needs that list's view
capability, and sees only rows on the properties they can see. Exact and leading matches come
before matches in the middle of a word.
"""

import re
from dataclasses import dataclass, field
from urllib.parse import urlencode

from django.db.models import Case, IntegerField, Q, Value, When
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from accounts.models import Membership
from accounts.permissions import accessible_property_ids, can, visible_properties
from billing.selectors import visible_invoices
from core.money import format_money
from core.phone import InvalidPhoneNumber, normalize_phone
from leases.models import Lease, LeaseTenant
from leases.services import visible_leases
from mpesa.inbox import visible_transactions
from payments.selectors import visible_payments
from properties.models import Property, Unit
from tenants.models import Tenant
from tenants.services import visible_tenants

MIN_LENGTH = 2
MAX_LENGTH = 60
SUGGEST_LIMIT = 5  # per group, in the box
PAGE_LIMIT = 20  # per group, on the results page


@dataclass
class Hit:
    label: str
    detail: str
    url: str


@dataclass
class Group:
    key: str
    title: str
    hits: list[Hit] = field(default_factory=list)
    more: bool = False  # there are more than shown
    list_url: str = ""  # the group's own list, filtered by the same words


@dataclass
class Results:
    q: str
    groups: list[Group] = field(default_factory=list)

    @property
    def count(self) -> int:
        return sum(len(g.hits) for g in self.groups)

    @property
    def too_short(self) -> bool:
        return len(self.q) < MIN_LENGTH


def clean_query(raw) -> str:
    return re.sub(r"\s+", " ", str(raw or "")).strip()[:MAX_LENGTH]


def phone_digits(q: str) -> str:
    """The national part of something that looks like a phone number, for a partial match.

    "0712 34" and "+254 712 34" both give "71234". Fewer than four digits, or any letters, give "".
    """
    if re.search(r"[^\d\s+\-().]", q):
        return ""
    digits = re.sub(r"\D", "", q)
    if digits.startswith("254"):
        digits = digits[3:]
    elif digits.startswith("0"):
        digits = digits[1:]
    return digits if len(digits) >= 4 else ""


def _phone_match(q: str, *fields: str) -> Q:
    """An exact match on the normalised number, or the digits anywhere in it."""
    match = Q(pk__in=[])
    try:
        phone = normalize_phone(q)
        for f in fields:
            match |= Q(**{f: phone})
    except InvalidPhoneNumber:
        pass
    digits = phone_digits(q)
    if digits:
        for f in fields:
            match |= Q(**{f"{f}__contains": digits})
    return match


def _rank(field_name: str, q: str) -> Case:
    """0 for an exact match, 1 for a leading one, 2 for anything else."""
    return Case(When(**{f"{field_name}__iexact": q}, then=Value(0)),
                When(**{f"{field_name}__istartswith": q}, then=Value(1)),
                default=Value(2), output_field=IntegerField())


def _take(qs, limit: int) -> tuple[list, bool]:
    rows = list(qs[:limit + 1])
    return rows[:limit], len(rows) > limit


def _list_url(name: str, q: str) -> str:
    return f"{reverse(name)}?{urlencode({'q': q})}"


def _tenant_names(leases, show: bool) -> dict[int, str]:
    if not show:
        return {}
    names = {}
    for link in (LeaseTenant.objects.filter(lease__in=[lease.pk for lease in leases], is_primary=True)
                 .select_related("tenant")):
        names[link.lease_id] = link.tenant.name
    return names


def _join(*parts) -> str:
    return " · ".join(str(p) for p in parts if p)


# ---------------------------------------------------------------------------
# Groups
# ---------------------------------------------------------------------------


def _tenants(m: Membership, q: str, limit: int) -> Group | None:
    if not can(m, "tenants.view"):
        return None
    match = Q(name__icontains=q) | Q(contact_person__icontains=q) | _phone_match(q, "phone", "alt_phone")
    if can(m, "tenants.view_sensitive"):
        match |= Q(id_number__iexact=q.replace(" ", ""))
    qs = visible_tenants(m, Tenant.objects.all()).filter(match).annotate(rank=_rank("name", q))
    rows, more = _take(qs.order_by("rank", "name", "pk"), limit)
    return Group("tenants", _("Tenants"), more=more, list_url=_list_url("tenants:list", q), hits=[
        Hit(t.name, _join(t.phone, t.get_status_display()), reverse("tenants:detail", args=[t.public_id]))
        for t in rows])


def _properties(m: Membership, q: str, limit: int) -> Group | None:
    if not can(m, "properties.view"):
        return None
    qs = (visible_properties(m, Property.objects.all()).filter(Q(name__icontains=q) | Q(code__iexact=q))
          .annotate(rank=_rank("name", q)))
    rows, more = _take(qs.order_by("rank", "name", "pk"), limit)
    return Group("properties", _("Properties"), more=more, list_url=_list_url("properties:list", q), hits=[
        Hit(p.name, _join(p.code, p.area), reverse("properties:detail", args=[p.public_id])) for p in rows])


def _units(m: Membership, q: str, limit: int) -> Group | None:
    if not can(m, "units.view"):
        return None
    props = visible_properties(m, Property.objects.all())
    qs = (Unit.objects.filter(organization=m.organization, property__in=props)
          .filter(Q(code__icontains=q) | Q(payment_reference__iexact=q.replace(" ", "")))
          .select_related("property").annotate(rank=_rank("code", q)))
    rows, more = _take(qs.order_by("rank", "property__name", "code", "pk"), limit)
    return Group("units", _("Units"), more=more, list_url=_list_url("properties:units", q), hits=[
        Hit(u.code, _join(u.property.name, u.type_label), reverse("properties:unit", args=[u.public_id]))
        for u in rows])


def _leases(m: Membership, q: str, limit: int) -> Group | None:
    if not can(m, "leases.view"):
        return None
    show_names = can(m, "tenants.view")
    match = Q(number__icontains=q) | Q(unit__code__iexact=q)
    if show_names:
        match |= Q(pk__in=LeaseTenant.objects.filter(tenant__name__icontains=q).values("lease_id"))
    qs = (visible_leases(m, Lease.objects.all()).filter(match).select_related("unit__property")
          .annotate(rank=_rank("number", q)))
    rows, more = _take(qs.order_by("rank", "-start_date", "-pk"), limit)
    names = _tenant_names(rows, show_names)
    return Group("leases", _("Leases"), more=more, list_url=_list_url("leases:list", q), hits=[
        Hit(str(lease), _join(names.get(lease.pk), f"{lease.unit.property.name} {lease.unit.code}",
                              lease.get_status_display()),
            reverse("leases:detail", args=[lease.public_id]))
        for lease in rows])


def _invoices(m: Membership, q: str, limit: int) -> Group | None:
    if not can(m, "invoices.view"):
        return None
    qs = visible_invoices(m).filter(number__icontains=q).annotate(rank=_rank("number", q))
    rows, more = _take(qs.order_by("rank", "-issue_date", "-pk"), limit)
    return Group("invoices", _("Invoices"), more=more, list_url=_list_url("billing:invoices", q), hits=[
        Hit(i.number, _join(f"{i.lease.unit.property.name} {i.lease.unit.code}", format_money(i.total, i.currency),
                            i.get_status_display()),
            reverse("billing:invoice", args=[i.public_id]))
        for i in rows])


def _payments(m: Membership, q: str, limit: int) -> Group | None:
    if not can(m, "payments.view"):
        return None
    qs = (visible_payments(m).filter(Q(reference__icontains=q) | Q(receipt__number__icontains=q))
          .annotate(rank=_rank("reference", q)))
    rows, more = _take(qs.order_by("rank", "-paid_at", "-pk"), limit)
    receipt = lambda p: getattr(p, "receipt", None)  # noqa: E731
    return Group("payments", _("Payments"), more=more, list_url=_list_url("payments:list", q), hits=[
        Hit(p.reference or (receipt(p).number if receipt(p) else str(_("Payment"))),
            _join(format_money(p.amount, p.lease.currency), f"{p.paid_at:%d %b %Y}",
                  f"{p.lease.unit.property.name} {p.lease.unit.code}", p.get_status_display()),
            reverse("payments:detail", args=[p.public_id]))
        for p in rows])


def _mpesa(m: Membership, q: str, limit: int) -> Group | None:
    if not can(m, "mpesa.view_transactions"):
        return None
    match = Q(trans_id__iexact=q.replace(" ", "")) | Q(trans_id__istartswith=q.replace(" ", ""))
    match |= Q(bill_ref__icontains=q) | _phone_match(q, "payer_phone")
    qs = visible_transactions(m).filter(match).annotate(rank=_rank("trans_id", q.replace(" ", "")))
    rows, more = _take(qs.order_by("rank", "-paid_at", "-pk"), limit)
    return Group("mpesa", _("M-Pesa"), more=more, list_url=_list_url("mpesa:transactions", q), hits=[
        Hit(t.trans_id, _join(format_money(t.amount, m.organization.currency), f"{t.paid_at:%d %b %Y}", t.bill_ref,
                              t.get_status_display()),
            reverse("mpesa:transaction", args=[t.trans_id]))
        for t in rows])


GROUPS = (_tenants, _properties, _units, _leases, _invoices, _payments, _mpesa)


def search(membership: Membership, raw, *, limit: int = SUGGEST_LIMIT) -> Results:
    """Groups with at least one hit, in a fixed order. Nothing for fewer than two characters."""
    results = Results(q=clean_query(raw))
    if results.too_short or membership is None:
        return results
    for build in GROUPS:
        group = build(membership, results.q, limit)
        if group and group.hits:
            results.groups.append(group)
    return results


def scoped(membership: Membership) -> bool:
    """Whether the member sees only some properties, for a note on the results page."""
    return accessible_property_ids(membership) is not None
