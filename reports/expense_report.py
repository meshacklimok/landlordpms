"""Expenses report: what was spent in a period, and on what.

Approved expenses by the date paid, as the profit and loss counts them (D-067). Waiting expenses are only
counted apart, as they are not yet money spent. Only the properties the member can see are included.
"""

import datetime
from dataclasses import dataclass, field
from decimal import Decimal

from django.db.models import Count, Sum
from django.db.models.functions import TruncMonth

from accounts.models import Membership
from accounts.permissions import require, visible_properties
from core.money import ZERO
from expenses import services as expense_services
from expenses.models import Expense
from properties.models import Property

from .metrics import rate

LIST_LIMIT = 300  # lines shown on the page
DOWNLOAD_LIMIT = LIST_LIMIT * 10  # lines in the download; it says so when there are more


@dataclass
class ExpenseReport:
    start: datetime.date
    end: datetime.date
    total: Decimal
    count: int
    waiting: int
    waiting_amount: Decimal
    # (label, count, amount, share of the total), largest first.
    by_category: list = field(default_factory=list)
    by_property: list = field(default_factory=list)
    by_supplier: list = field(default_factory=list)
    by_method: list = field(default_factory=list)
    months: list = field(default_factory=list)  # (first day, label, count, amount)
    lines: list = field(default_factory=list)  # the expenses themselves, newest first
    lines_total: int = 0
    previous_total: Decimal | None = None  # the same number of days just before

    @property
    def average(self) -> Decimal | None:
        return None if not self.count else self.total / self.count


def _groups(qs, field_name: str, total: Decimal, labels: dict | None = None) -> list:
    rows = qs.values(field_name).annotate(n=Count("pk"), a=Sum("amount")).order_by("-a", field_name)
    out = []
    for r in rows:
        key = r[field_name]
        label = (labels or {}).get(key, key) if labels is not None else key
        out.append((label, r["n"], r["a"], rate(r["a"], total)))
    return out


def expenses(membership: Membership, start: datetime.date, end: datetime.date,
             prop: Property | None = None) -> ExpenseReport:
    require(membership, "reports.view_financial")
    org = membership.organization
    props = visible_properties(membership, Property.all_objects.all())
    if prop is not None:
        props = props.filter(pk=prop.pk)
    ok = expense_services.approved(org, props, start, end)
    agg = ok.aggregate(n=Count("pk"), a=Sum("amount"))
    total = agg["a"] or ZERO
    waiting = (Expense.objects.for_org(org).filter(status=Expense.Status.SUBMITTED, property__in=props,
                                                   paid_on__gte=start, paid_on__lte=end)
               .aggregate(n=Count("pk"), a=Sum("amount")))
    method_labels = {k: str(v) for k, v in Expense.Method.choices}
    none = "—"
    by_supplier = [(label or none, n, a, share) for label, n, a, share in
                   _groups(ok, "supplier__name", total)]
    days = (end - start).days + 1
    before_end = start - datetime.timedelta(days=1)
    before = expense_services.approved(org, props, before_end - datetime.timedelta(days=days - 1), before_end)
    months = [(r["m"], f"{r['m']:%b %Y}", r["n"], r["a"]) for r in
              ok.annotate(m=TruncMonth("paid_on")).values("m").annotate(n=Count("pk"), a=Sum("amount")).order_by("m")]
    listed = list(ok.select_related("property", "category", "supplier").order_by("-paid_on", "-pk")[:DOWNLOAD_LIMIT])
    return ExpenseReport(
        start=start, end=end, total=total, count=agg["n"], waiting=waiting["n"], waiting_amount=waiting["a"] or ZERO,
        by_category=_groups(ok, "category__name", total),
        by_property=_groups(ok, "property__name", total),
        by_supplier=by_supplier,
        by_method=_groups(ok, "method", total, method_labels),
        months=months, lines=listed, lines_total=agg["n"],
        previous_total=before.aggregate(a=Sum("amount"))["a"] or ZERO)
