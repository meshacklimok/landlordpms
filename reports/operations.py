"""Maintenance and messaging analytics (D-132).

Maintenance, for the requests reported in the period (by `created_at`, in local time):
- Resolved: the request has a done time (or, failing that, a closed time) and was not cancelled.
  Resolution time is that time less the time reported, averaged over resolved requests only; a request still
  open is never counted in it, even if its period ended long ago.
- Open: still new, assigned, in progress or on hold *now*, whenever it was reported. Overdue: open and past its
  fix-by time now.
- Fixed in time: resolved on or before the fix-by time, out of the resolved ones.
- Cost: approved or waiting expenses linked to the request (cash basis, as in the profit and loss), by the
  request's property and kind. Voided and rejected expenses are left out.
- Repeated problems: three or more requests of one kind for the same unit (or the common areas of a property)
  in the period.
Only requests for properties the member can see are counted.
"""

import datetime
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal

from django.db.models import Avg, Count, DurationField, Exists, ExpressionWrapper, F, Min, OuterRef, Q, Sum
from django.db.models.functions import Coalesce, TruncMonth
from django.utils import timezone
from django.utils.translation import gettext as _

from accounts.models import Membership
from accounts.permissions import can, require, visible_properties
from core.money import ZERO
from expenses.models import Expense
from leases.models import Lease, LeaseRentChange
from maintenance.models import MaintenanceRequest as Request
from meters.models import Meter
from notifications.models import Message
from payments.models import Payment
from properties.models import Property

from .metrics import rate

REPEAT_AT = 3
PAYMENT_LINE_LIMIT = 3000


def bounds(start: datetime.date, end: datetime.date) -> tuple[datetime.datetime, datetime.datetime]:
    """Aware datetimes from the start of `start` up to (not including) the day after `end`, in local time."""
    tz = timezone.get_current_timezone()
    return (timezone.make_aware(datetime.datetime.combine(start, datetime.time.min), tz),
            timezone.make_aware(datetime.datetime.combine(end + datetime.timedelta(days=1), datetime.time.min), tz))


def _hours(value: datetime.timedelta | None) -> Decimal | None:
    return None if value is None else (Decimal(value.total_seconds()) / 3600).quantize(Decimal("0.1"))


def _scope(membership: Membership, prop: Property | None):
    props = visible_properties(membership, Property.all_objects.all())
    return props.filter(pk=prop.pk) if prop is not None else props


@dataclass
class Row:
    """One line of a breakdown: how many requests, how many are done, the average hours, the cost."""

    key: str
    label: str
    total: int = 0
    resolved: int = 0
    open: int = 0
    overdue: int = 0
    hours: Decimal | None = None
    cost: Decimal = ZERO
    link: dict = field(default_factory=dict)

    @property
    def resolved_share(self):
        return rate(self.resolved, self.total)


@dataclass
class MaintenanceReport:
    start: datetime.date
    end: datetime.date
    total: int
    resolved: int
    cancelled: int
    open_now: int
    overdue_now: int
    waiting_to_start: int
    avg_hours: Decimal | None
    fixed_in_time: int
    cost: Decimal
    by_priority: list[Row]
    by_kind: list[Row]
    by_property: list[Row]
    by_supplier: list[Row]
    months: list[dict]
    repeats: list[dict]
    generated_at: datetime.datetime

    @property
    def resolved_share(self):
        return rate(self.resolved, self.total - self.cancelled)

    @property
    def fixed_in_time_share(self):
        return rate(self.fixed_in_time, self.resolved)


def _group(qs, field_name: str, labels, now, cost_by) -> list[Row]:
    done = Coalesce("done_at", "closed_at")
    rows = (qs.values(field_name)
            .annotate(total=Count("pk"),
                      resolved=Count("pk", filter=Q(done_at__isnull=False) | Q(closed_at__isnull=False), distinct=True),
                      open=Count("pk", filter=Q(status__in=Request.OPEN)),
                      overdue=Count("pk", filter=Q(status__in=Request.OPEN, due_at__lt=now)),
                      hours=Avg(ExpressionWrapper(done - F("created_at"), output_field=DurationField()),
                                filter=Q(done_at__isnull=False) | Q(closed_at__isnull=False)))
            .order_by())
    out = []
    for r in rows:
        key = r[field_name]
        out.append(Row(key=str(key), label=str(labels(key)), total=r["total"], resolved=r["resolved"], open=r["open"],
                       overdue=r["overdue"], hours=_hours(r["hours"]), cost=cost_by.get(key, ZERO)))
    return sorted(out, key=lambda row: (-row.total, row.label))


def maintenance(membership: Membership, start: datetime.date, end: datetime.date,
                prop: Property | None = None, *, now: datetime.datetime | None = None) -> MaintenanceReport:
    require(membership, "maintenance.view")
    now = now or timezone.now()
    first, after = bounds(start, end)
    props = _scope(membership, prop)
    base = (Request.objects.for_org(membership.organization).filter(property__in=props)
            .filter(created_at__gte=first, created_at__lt=after))
    live = base.exclude(status=Request.Status.CANCELLED)
    done = Coalesce("done_at", "closed_at")
    resolved_q = Q(done_at__isnull=False) | Q(closed_at__isnull=False)
    expenses = (Expense.objects.for_org(membership.organization)
                .filter(status__in=Expense.LIVE, maintenance_request__in=live))

    def costs(key: str) -> dict:
        return {r[key]: r["sum"] for r in
                expenses.values(key).annotate(sum=Sum("amount")).order_by()}

    cost_property = costs("maintenance_request__property")
    cost_kind = costs("maintenance_request__kind")
    cost_priority = costs("maintenance_request__priority")
    cost_supplier = costs("maintenance_request__supplier")

    head = live.aggregate(
        total=Count("pk"), resolved=Count("pk", filter=resolved_q),
        open=Count("pk", filter=Q(status__in=Request.OPEN)),
        overdue=Count("pk", filter=Q(status__in=Request.OPEN, due_at__lt=now)),
        waiting=Count("pk", filter=Q(status=Request.Status.NEW)),
        hours=Avg(ExpressionWrapper(done - F("created_at"), output_field=DurationField()), filter=resolved_q),
        in_time=Count("pk", filter=Q(done_at__isnull=False, done_at__lte=F("due_at"))
                      | Q(done_at__isnull=True, closed_at__isnull=False, closed_at__lte=F("due_at"))),
    )
    names = dict(props.values_list("pk", "name"))
    suppliers = dict(Request.objects.filter(pk__in=live.values("pk"), supplier__isnull=False)
                     .values_list("supplier", "supplier__name"))
    priorities, kinds = dict(Request.Priority.choices), dict(Request.Kind.choices)

    by_priority = _group(live, "priority", lambda k: priorities.get(k, k), now, cost_priority)
    order = list(priorities)
    by_priority.sort(key=lambda row: order.index(row.key) if row.key in order else len(order))
    for row in by_priority:
        row.link = {"priority": row.key}
    by_kind = _group(live, "kind", lambda k: kinds.get(k, k), now, cost_kind)
    by_property = _group(live, "property", lambda k: names.get(k, "—"), now, cost_property)
    by_supplier = _group(live.filter(supplier__isnull=False), "supplier", lambda k: suppliers.get(k, "—"), now,
                         cost_supplier)

    created = {r["m"].date(): r["n"] for r in
               live.annotate(m=TruncMonth("created_at")).values("m").annotate(n=Count("pk")).order_by()}
    finished = {r["m"].date(): r["n"] for r in
                live.filter(resolved_q).annotate(m=TruncMonth(done)).values("m").annotate(n=Count("pk")).order_by()}
    months = [{"month": m, "label": f"{m:%b %Y}", "reported": created.get(m, 0), "resolved": finished.get(m, 0)}
              for m in sorted(set(created) | set(finished))]

    repeats = []
    groups = (live.values("property", "unit", "unit__code", "kind").annotate(n=Count("pk"))
              .filter(n__gte=REPEAT_AT).order_by("-n", "property"))
    for g in groups:
        repeats.append({"property": names.get(g["property"], "—"), "unit": g["unit__code"] or _("Common areas"),
                        "kind": kinds.get(g["kind"], g["kind"]), "count": g["n"]})

    return MaintenanceReport(
        start=start, end=end, total=head["total"], resolved=head["resolved"],
        cancelled=base.filter(status=Request.Status.CANCELLED).count(), open_now=head["open"],
        overdue_now=head["overdue"], waiting_to_start=head["waiting"], avg_hours=_hours(head["hours"]),
        fixed_in_time=head["in_time"], cost=sum(cost_property.values(), ZERO), by_priority=by_priority,
        by_kind=by_kind, by_property=by_property, by_supplier=by_supplier, months=months, repeats=repeats,
        generated_at=now)


# ---------------------------------------------------------------------------
# Messages: how many reached people
# ---------------------------------------------------------------------------


@dataclass
class ChannelRow:
    channel: str
    label: str
    counts: dict[str, int]

    @property
    def total(self) -> int:
        return sum(self.counts.values())

    @property
    def attempted(self) -> int:
        """Messages that were tried: all but those skipped on purpose (opted out, no consent, no address...)."""
        return self.total - self.counts.get(Message.Status.SKIPPED, 0)

    @property
    def failure_rate(self):
        return rate(self.counts.get(Message.Status.FAILED, 0), self.attempted)

    @property
    def delivery_rate(self):
        reached = self.counts.get(Message.Status.SENT, 0) + self.counts.get(Message.Status.DELIVERED, 0)
        return rate(reached, self.attempted)


@dataclass
class DeliveryReport:
    start: datetime.date
    end: datetime.date
    channels: list[ChannelRow]
    skipped: list[tuple[str, int]]
    errors: list[tuple[str, int]]
    cost: Decimal
    generated_at: datetime.datetime

    @property
    def total(self) -> int:
        return sum(c.total for c in self.channels)

    @property
    def failed(self) -> int:
        return sum(c.counts.get(Message.Status.FAILED, 0) for c in self.channels)

    @property
    def attempted(self) -> int:
        return sum(c.attempted for c in self.channels)

    @property
    def failure_rate(self):
        return rate(self.failed, self.attempted)


def delivery(membership: Membership, start: datetime.date, end: datetime.date,
             *, now: datetime.datetime | None = None) -> DeliveryReport:
    """Messages made in the period, by channel and outcome; failures by the reason given by the provider.

    Messages are the organization's, not a property's, so this needs the organization-wide message right.
    """
    require(membership, "messages.view")
    first, after = bounds(start, end)
    qs = Message.objects.for_org(membership.organization).filter(created_at__gte=first, created_at__lt=after)
    labels = dict(Message.Status.choices)
    channel_labels = dict(Message._meta.get_field("channel").choices)
    counts: dict[str, dict[str, int]] = defaultdict(lambda: dict.fromkeys(labels, 0))
    for r in qs.values("channel", "status").annotate(n=Count("pk")).order_by():
        counts[r["channel"]][r["status"]] = r["n"]
    channels = [ChannelRow(c, str(channel_labels.get(c, c)), v) for c, v in sorted(counts.items())]
    skip_labels = dict(Message.SkipReason.choices)
    skipped = [(str(skip_labels.get(r["skip_reason"], r["skip_reason"] or "—")), r["n"]) for r in
               qs.filter(status=Message.Status.SKIPPED).values("skip_reason").annotate(n=Count("pk"))
               .order_by("-n")]
    errors = [(r["error"] or "—", r["n"]) for r in
              qs.filter(status=Message.Status.FAILED).values("error").annotate(n=Count("pk")).order_by("-n")[:10]]
    cost = qs.aggregate(c=Sum("cost"))["c"] or ZERO
    return DeliveryReport(start, end, channels, skipped, errors, cost, now or timezone.now())


# ---------------------------------------------------------------------------
# Payment transactions: what came in, what is waiting, what was undone
# ---------------------------------------------------------------------------


@dataclass
class PaymentsReport:
    start: datetime.date
    end: datetime.date
    states: list[tuple[str, str, int, Decimal]]  # (key, label, count, amount), by date paid
    methods: list[tuple[str, int, Decimal, object]]  # confirmed only: (label, count, amount, share)
    months: list[tuple[datetime.date, str, int, Decimal]]  # confirmed only, by date paid
    accounts: list[tuple[str, int, Decimal]]  # confirmed only
    waiting: int  # pending review now, whatever the date paid
    waiting_amount: Decimal
    oldest_waiting: datetime.date | None
    generated_at: datetime.datetime
    # The payments themselves, dated in the period, newest first (the payment statement); LINE_LIMIT at most.
    lines: list = field(default_factory=list)
    lines_total: int = 0

    @property
    def confirmed(self) -> Decimal:
        return next((a for k, _l, _n, a in self.states if k == "confirmed"), ZERO)

    @property
    def confirmed_count(self) -> int:
        return next((n for k, _l, n, _a in self.states if k == "confirmed"), 0)

    @property
    def undone(self) -> Decimal:
        return sum((a for k, _l, _n, a in self.states if k in ("reversed", "rejected")), ZERO)

    @property
    def total(self) -> int:
        return sum(n for _k, _l, n, _a in self.states)


def payments(membership: Membership, start: datetime.date, end: datetime.date,
             prop: Property | None = None, *, now: datetime.datetime | None = None) -> PaymentsReport:
    """Payments by date paid in the period, by state.

    Only confirmed payments are money received: they feed the method, month and account tables. A payment
    reversed after confirming and one turned down in review are shown apart ("reversed", "rejected") and never
    counted as received. Pending payments are also counted as they are now, whatever their date.
    """
    require(membership, "payments.view")
    scoped = Payment.objects.for_org(membership.organization).filter(
        lease__unit__property__in=_scope(membership, prop))
    period = scoped.filter(paid_at__gte=start, paid_at__lte=end)
    S = Payment.Status
    keyed = {"pending": Q(status=S.PENDING_REVIEW),
             "confirmed": Q(status=S.CONFIRMED),
             "reversed": Q(status=S.REVERSED, confirmed_at__isnull=False),
             "rejected": Q(status=S.REVERSED, confirmed_at__isnull=True)}
    agg = period.aggregate(**{f"{k}_n": Count("pk", filter=q) for k, q in keyed.items()},
                           **{f"{k}_a": Sum("amount", filter=q) for k, q in keyed.items()})
    states = [(k, str(Payment.STATES[k]), agg[f"{k}_n"], agg[f"{k}_a"] or ZERO) for k in keyed]
    ok = period.filter(status=S.CONFIRMED)
    method_labels = dict(Payment.Method.choices)
    raw = list(ok.values("method").annotate(n=Count("pk"), a=Sum("amount")).order_by("-a"))
    total = sum((r["a"] for r in raw), ZERO)
    methods = [(str(method_labels.get(r["method"], r["method"])), r["n"], r["a"], rate(r["a"], total)) for r in raw]
    months = [(r["m"], f"{r['m']:%b %Y}", r["n"], r["a"]) for r in
              ok.annotate(m=TruncMonth("paid_at")).values("m").annotate(n=Count("pk"), a=Sum("amount")).order_by("m")]
    accounts = [(r["payment_account__display_name"] or _("No account recorded"), r["n"], r["a"]) for r in
                ok.values("payment_account__display_name").annotate(n=Count("pk"), a=Sum("amount")).order_by("-a")]
    wait = scoped.filter(status=S.PENDING_REVIEW).aggregate(n=Count("pk"), a=Sum("amount"),
                                                            oldest=Min("paid_at"))
    lines = list(period.select_related("lease__unit__property", "tenant", "payment_account")
                 .order_by("-paid_at", "-pk")[:PAYMENT_LINE_LIMIT])
    return PaymentsReport(start, end, states, methods, months, accounts, wait["n"], wait["a"] or ZERO, wait["oldest"],
                          now or timezone.now(), lines=lines, lines_total=sum(v for k, v in agg.items()
                                                                              if k.endswith("_n")))


# ---------------------------------------------------------------------------
# Leases and tenants: who is coming, going and staying
# ---------------------------------------------------------------------------

EXPIRY_WINDOWS = (30, 60, 90)


@dataclass
class LeaseLine:
    lease: object
    days: int  # days until the end date (negative: already past)
    tenant: object = None  # only for members who may see tenants


@dataclass
class LeasesReport:
    start: datetime.date
    end: datetime.date
    active_now: int
    move_ins: int
    move_outs: int
    renewals: int
    in_force: int  # leases in force on at least one day of the period
    average_months: Decimal | None  # of leases ended in the period
    windows: list[tuple[int, int]]  # (days, leases ending within that many days from today)
    expiring: list[LeaseLine]
    holding_over: list[LeaseLine]
    show_tenants: bool
    today: datetime.date
    generated_at: datetime.datetime
    stays: list[tuple[str, int]] = field(default_factory=list)  # leases that ended in the period by length of stay
    tenure: list[tuple[str, int]] = field(default_factory=list)  # leases active now by time since they began
    increases: int = 0  # rent increases that took effect in the period
    average_increase: Decimal | None = None  # their average size, as a fraction of the old rent
    relet_days: Decimal | None = None  # average empty days before a unit let in the period was let again
    relets: int = 0
    stale: list[LeaseLine] = field(default_factory=list)  # active leases whose rent has not risen in a year or more
    stale_total: int = 0

    @property
    def turnover(self):
        """Move-outs as a share of the leases in force at some point in the period."""
        return rate(self.move_outs, self.in_force)


def leases(membership: Membership, start: datetime.date, end: datetime.date,
           prop: Property | None = None, *, today: datetime.date | None = None,
           now: datetime.datetime | None = None) -> LeasesReport:
    """Lease movement in the period and the leases about to end.

    - Move-in: a lease that started in the period and is not a draft. Move-out: a lease ended or terminated with
      an ended day in the period. Renewal: a lease marked renewed with an ended day in the period (the tenant
      stays, so it is not a move-out).
    - In force: not a draft, started by the period's last day and not ended before its first.
    - Expiring: active leases with an end date from today to 90 days on (the windows count those within 30, 60
      and 90 days). Holding over: active leases whose end date has passed, which need renewing or ending.
    """
    require(membership, "leases.view")
    today = today or timezone.localdate()
    base = (Lease.objects.for_org(membership.organization).exclude(status=Lease.Status.DRAFT)
            .filter(unit__property__in=_scope(membership, prop)))
    S = Lease.Status
    in_force = base.filter(start_date__lte=end).filter(Q(ended_on__gte=start) | Q(ended_on__isnull=True,
                                                                                end_date__gte=start)
                                                       | Q(ended_on__isnull=True, end_date__isnull=True)).count()
    ended = base.filter(status__in=(S.ENDED, S.TERMINATED), ended_on__gte=start, ended_on__lte=end)
    renewed = base.filter(status=S.RENEWED, ended_on__gte=start, ended_on__lte=end).count()
    spans = [(lease.ended_on - lease.start_date).days for lease in ended.only("start_date", "ended_on")]
    average = (Decimal(sum(spans)) / len(spans) / Decimal("30.4375")).quantize(Decimal("0.1")) if spans else None
    active = base.filter(status=S.ACTIVE)
    horizon = today + datetime.timedelta(days=max(EXPIRY_WINDOWS))
    soon = active.filter(end_date__gte=today, end_date__lte=horizon)
    windows = [(d, soon.filter(end_date__lte=today + datetime.timedelta(days=d)).count()) for d in EXPIRY_WINDOWS]
    show = can(membership, "tenants.view")

    def lines(qs):
        out = []
        qs = qs.select_related("unit__property")
        if show:
            qs = qs.prefetch_related("lease_tenants__tenant")
        for lease in qs[:200]:
            out.append(LeaseLine(lease, (lease.end_date - today).days if lease.end_date else 0,
                                 lease.primary_tenant if show else None))
        return out

    more = _lease_analysis(base, active, start, end, today, lines)

    return LeasesReport(
        start=start, end=end, active_now=active.count(),
        move_ins=base.filter(start_date__gte=start, start_date__lte=end).count(), move_outs=ended.count(),
        renewals=renewed, in_force=in_force, average_months=average, windows=windows,
        expiring=lines(soon.order_by("end_date")),
        holding_over=lines(active.filter(end_date__lt=today).order_by("end_date")), show_tenants=show, today=today,
        generated_at=now or timezone.now(), **more)


STAY_BANDS = ((0, 6, "Under 6 months"), (6, 12, "6 to 12 months"), (12, 24, "1 to 2 years"), (24, None, "2 years or more"))
STALE_DAYS = 365


def _band(months: Decimal) -> int:
    months = max(months, Decimal(0))  # an active lease that starts in the future has run a negative time
    return next(i for i, (lo, hi, _label) in enumerate(STAY_BANDS) if months >= lo and (hi is None or months < hi))


def _lease_analysis(base, active, start, end, today, lines) -> dict:
    """Length of stay, rent increases and time to re-let (D-135), from the leases already in `base`.

    - Stay: days from start to ended day, for leases that ended or were terminated in the period.
    - Tenure: how long the leases active now have run.
    - Increase: a rent change in the period that follows an earlier one on the lease (the first row is the opening
      rent); the size is the new rent over the old, less one.
    - Re-let: for each lease that started in the period, the days between the previous lease on that unit ending
      and this one starting; units let for the first time are left out.
    - Stale: active leases whose latest rent took effect a year or more ago.
    """
    S = Lease.Status
    month = Decimal("30.4375")
    stays = [0] * len(STAY_BANDS)
    for began, ended_on in (base.filter(status__in=(S.ENDED, S.TERMINATED), ended_on__gte=start, ended_on__lte=end)
                            .values_list("start_date", "ended_on")):
        stays[_band(Decimal((ended_on - began).days) / month)] += 1
    tenure = [0] * len(STAY_BANDS)
    for began in active.values_list("start_date", flat=True):
        tenure[_band(Decimal((today - began).days) / month)] += 1

    changes: dict[int, list[tuple[datetime.date, Decimal]]] = defaultdict(list)
    for lease_id, day, amount in (LeaseRentChange.objects.filter(lease__in=base).order_by("effective_from")
                                  .values_list("lease_id", "effective_from", "amount")):
        changes[lease_id].append((day, amount))
    sizes = []
    for rows in changes.values():
        for before, after in zip(rows, rows[1:], strict=False):
            if start <= after[0] <= end and before[1] > 0:
                sizes.append(after[1] / before[1] - 1)
    stale = []
    for lease in active.filter(start_date__lte=today - datetime.timedelta(days=STALE_DAYS)).only("pk"):
        rows = changes.get(lease.pk)
        if rows and rows[-1][0] <= today - datetime.timedelta(days=STALE_DAYS):
            stale.append((rows[-1][0], lease.pk))
    stale.sort()

    started = list(base.filter(start_date__gte=start, start_date__lte=end).values_list("unit_id", "start_date", "pk"))
    gaps = []
    if started:
        ended_by_unit: dict[int, list[datetime.date]] = defaultdict(list)
        for unit_id, ended_on in (base.filter(unit_id__in={u for u, _d, _p in started}, ended_on__isnull=False)
                                  .values_list("unit_id", "ended_on")):
            ended_by_unit[unit_id].append(ended_on)
        for unit_id, began, _pk in started:
            before = [d for d in ended_by_unit.get(unit_id, []) if d <= began]
            if before:
                gaps.append((began - max(before)).days)
    return {
        "stays": [(label, n) for (_lo, _hi, label), n in zip(STAY_BANDS, stays, strict=False)],
        "tenure": [(label, n) for (_lo, _hi, label), n in zip(STAY_BANDS, tenure, strict=False)],
        "increases": len(sizes),
        "average_increase": (sum(sizes) / len(sizes)).quantize(Decimal("0.0001")) if sizes else None,
        "relets": len(gaps),
        "relet_days": (Decimal(sum(gaps)) / len(gaps)).quantize(Decimal("0.1")) if gaps else None,
        "stale_total": len(stale),
        "stale": _since(lines(active.filter(pk__in=[pk for _d, pk in stale[:20]])), dict((pk, d) for d, pk in stale),
                        today),
    }


def _since(found: list, last: dict, today: datetime.date) -> list:
    """The lines with `days` set to the days since the rent last changed, longest first."""
    for line in found:
        line.days = (today - last[line.lease.pk]).days
    return sorted(found, key=lambda line: -line.days)


# ---------------------------------------------------------------------------
# Arrears: who owes, how late, how it is changing
# ---------------------------------------------------------------------------

RECURRING_MONTHS = 6
RECURRING_AT = 3
TOP_LEASES = 50


@dataclass
class ArrearsLine:
    row: object  # billing.selectors.LeaseArrears
    overdue: Decimal
    days: int
    invoices: int
    tenant: object = None


@dataclass
class RecurringLine:
    lease: object
    late: int  # months paid late, part paid or unpaid after the due date
    billed: int  # months with rent due
    tenant: object = None


@dataclass
class ArrearsReport:
    today: datetime.date
    owed: Decimal  # everything unpaid, including what is not yet due
    overdue: Decimal
    leases: int  # leases with money overdue
    invoices: int  # invoices with money overdue
    buckets: list[tuple[str, str, Decimal, object]]  # (key, label, amount, share of owed)
    by_property: list[tuple[str, Decimal, int]]  # (name, overdue, leases)
    top: list[ArrearsLine]
    min_amount: Decimal | None
    history: list  # reports.snapshots.ArrearsPoint
    recurring: list[RecurringLine]
    recovered: Decimal  # late rent paid in the period
    recovered_count: int
    start: datetime.date
    end: datetime.date
    generated_at: datetime.datetime

    @property
    def over_60_share(self):
        return rate(sum((a for k, _l, a, _s in self.buckets if k in ("d61_90", "d90")), ZERO), self.owed)

    @property
    def overdue_share(self):
        return rate(self.overdue, self.owed)


def arrears(membership: Membership, start: datetime.date, end: datetime.date, prop: Property | None = None, *,
            today: datetime.date | None = None, min_amount: Decimal | None = None,
            now: datetime.datetime | None = None) -> ArrearsReport:
    """Arrears as of today, and how late rent was paid and recovered in the period.

    - Owed: the unpaid balance of each lease from the ledger (credits pay the oldest debits first); credit
      balances are not netted off. Overdue: the part past its due date plus grace days. What is not yet due is
      shown apart and is never called overdue.
    - Ageing bands are the ones aged receivables uses. Overdue invoices are the invoices still unpaid in part
      after their due date plus grace.
    - Recurring late payers: leases paid late, in part or not at all after the due date in `RECURRING_AT` or
      more of the last `RECURRING_MONTHS` months billed (months not yet due are left out).
    - Recovered: confirmed payments dated in the period applied to rent invoices that were already overdue
      on the day they were paid.
    - The trend comes from the daily snapshots; months without one are left blank, not guessed.
    """
    from billing import selectors as billing_selectors
    from billing.models import ChargeType, InvoiceLine
    from payments.models import PaymentAllocation

    from . import metrics, snapshots, units

    require(membership, "reports.view_financial")
    require(membership, "invoices.view")
    today = today or timezone.localdate()
    org = membership.organization
    rows = billing_selectors.arrears(membership, today, overdue_only=False)
    if prop is not None:
        rows = [r for r in rows if r.lease.unit.property_id == prop.pk]
    totals = billing_selectors.aging_totals(rows)
    owed = totals["total"]
    shows_tenants = can(membership, "tenants.view")

    lines, invoices, places = [], set(), defaultdict(lambda: [ZERO, 0])
    for r in rows:
        overdue = r.balance - r.buckets["current"]
        if overdue <= 0:
            continue
        mine = {d.invoice.pk for d in r.debts if d.invoice is not None and d.late_from < today}
        invoices |= mine
        place = places[r.lease.unit.property.name]
        place[0] += overdue
        place[1] += 1
        if min_amount is None or overdue >= min_amount:
            lines.append(ArrearsLine(r, overdue, r.days_overdue(today), len(mine),
                                     r.lease.primary_tenant if shows_tenants else None))
    lines.sort(key=lambda x: -x.overdue)
    overdue_total = sum((p[0] for p in places.values()), ZERO)
    buckets = [(key, str(label), totals[key], rate(totals[key], owed))
               for key, label, *_rest in billing_selectors.AGING_BUCKETS]

    sc = metrics.Scope(membership, prop)
    history = snapshots.history(sc, today)

    first = metrics.add_months(metrics.month_start(today), 1 - RECURRING_MONTHS)
    props = _scope(membership, prop)
    cells = units.rent_states(org, Q(lease__unit__property__in=props), "lease_id", first, metrics.month_start(today),
                              today)
    bad = (units.LATE, units.PART, units.UNPAID)
    flagged = []
    for lease_id, by_month in cells.items():
        states = [c.state for c in by_month.values() if c.state != units.DUE]
        late = sum(1 for s in states if s in bad)
        if late >= RECURRING_AT:
            flagged.append((lease_id, late, len(states)))
    recurring = []
    if flagged:
        leases = {x.pk: x for x in Lease.all_objects.filter(pk__in=[f[0] for f in flagged])
                  .select_related("unit__property").prefetch_related("lease_tenants__tenant")}
        recurring = [RecurringLine(leases[pk], late, billed, leases[pk].primary_tenant if shows_tenants else None)
                     for pk, late, billed in sorted(flagged, key=lambda f: (-f[1], f[0]))]

    rent_lines = InvoiceLine.objects.filter(invoice=OuterRef("invoice"), is_void=False,
                                            charge_type__category=ChargeType.Category.RENT)
    rec = (PaymentAllocation.objects.filter(
        organization=org, payment__status=Payment.Status.CONFIRMED, payment__paid_at__gte=start,
        payment__paid_at__lte=end, payment__paid_at__gt=F("invoice__overdue_after"),
        invoice__lease__unit__property__in=props, invoice__status__in=metrics.LIVE_INVOICE)
        .filter(Exists(rent_lines)).aggregate(a=Sum("amount"), n=Count("pk")))
    return ArrearsReport(
        today=today, owed=owed, overdue=overdue_total, leases=sum(p[1] for p in places.values()), invoices=len(invoices), buckets=buckets,
        by_property=sorted(((n, v[0], v[1]) for n, v in places.items()), key=lambda x: -x[1]),
        top=lines[:TOP_LEASES], min_amount=min_amount, history=history, recurring=recurring,
        recovered=rec["a"] or ZERO, recovered_count=rec["n"] or 0, start=start, end=end,
        generated_at=now or timezone.now())


# ---------------------------------------------------------------------------
# Utilities: what the meters recorded, what was billed for it, and what came in
# ---------------------------------------------------------------------------

UTILITY_TOP = 10


def price_per_unit(amount, usage):
    return None if not usage else (amount / usage).quantize(Decimal("0.01"))


@dataclass
class UtilityRow:
    name: str
    utility: str
    usage: Decimal = ZERO
    readings: int = 0
    billed: Decimal = ZERO
    invoiced: Decimal = ZERO
    collected: Decimal = ZERO

    @property
    def average_price(self):
        return price_per_unit(self.billed, self.usage)

    def add(self, other):
        self.usage += other.usage
        self.readings += other.readings
        self.billed += other.billed
        self.invoiced += other.invoiced
        self.collected += other.collected


@dataclass
class UtilitiesReport:
    start: datetime.date
    end: datetime.date
    show_money: bool
    usage: dict  # utility key -> total units used, approved readings in the period
    billed: Decimal  # charges on the approved readings of the period
    invoiced: Decimal  # of it, on a live invoice
    collected: Decimal  # of that, the share of the invoice paid (pro rata over its lines)
    flagged: int  # approved readings with a warning flag
    waiting: int  # readings waiting for approval now
    by_utility: list[UtilityRow]
    by_property: list[UtilityRow]
    by_meter: list[UtilityRow]  # biggest users first, limited
    months: list  # (month, label, {utility: usage})
    generated_at: datetime.datetime

    @property
    def uninvoiced(self):
        return self.billed - self.invoiced

    @property
    def collection_rate(self):
        return rate(self.collected, self.invoiced)


def utilities(membership: Membership, start: datetime.date, end: datetime.date, prop: Property | None = None, *,
              now: datetime.datetime | None = None) -> UtilitiesReport:
    """Meter consumption and what it was billed and collected.

    - Usage: approved, non-baseline readings dated in the period, each measured from the reading before it
      (a replaced meter starts a new count and adds no usage). Prepaid meters count here and bill nothing.
    - Billed: charges on the approved readings dated in the period (cancelled ones left out), so billed and used
      cover the same readings; they reach invoices the month after.
      Invoiced: those on a live invoice. Collected: payments applied to that invoice, spread over its lines in
      proportion to amount, so it is an estimate for invoices that carry more than the meter line.
    - Money figures need the financial reports right; without it only usage is shown.
    """
    from billing.models import InvoiceLine
    from meters.models import MeterCharge, MeterReading
    from payments.models import PaymentAllocation

    from . import metrics

    require(membership, "meters.view")
    show_money = can(membership, "reports.view_financial")
    props = _scope(membership, prop)
    org = membership.organization

    readings = (MeterReading.objects.filter(organization=org, status=MeterReading.Status.APPROVED,
                                            is_baseline=False, read_on__range=(start, end),
                                            meter__property__in=props)
                .select_related("meter__property", "previous"))
    by_meter: dict[int, UtilityRow] = {}
    place: dict[int, str] = {}
    months: dict[datetime.date, dict[str, Decimal]] = defaultdict(lambda: defaultdict(lambda: ZERO))
    usage_total: dict[str, Decimal] = defaultdict(lambda: ZERO)
    flagged = 0
    for r in readings:
        used = r.value - r.previous.value
        if used < 0:
            continue
        m = r.meter
        row = by_meter.setdefault(m.pk, UtilityRow(m.label, m.utility))
        place[m.pk] = m.property.name
        row.usage += used
        row.readings += 1
        usage_total[m.utility] += used
        months[metrics.month_start(r.read_on)][m.utility] += used
        flagged += bool(r.flags)
    waiting = MeterReading.objects.filter(organization=org, status=MeterReading.Status.SUBMITTED,
                                          meter__property__in=props).count()

    billed = invoiced = collected = ZERO
    if show_money:
        charges = list(MeterCharge.objects.filter(
            organization=org, cancelled_at__isnull=True, reading__read_on__range=(start, end),
            reading__status=MeterReading.Status.APPROVED, reading__meter__property__in=props)
            .select_related("reading__meter__property"))
        lines = {x.meter_charge_id: x for x in InvoiceLine.objects.filter(
            meter_charge__in=[c.pk for c in charges], is_void=False, invoice__status__in=metrics.LIVE_INVOICE)}
        invoice_ids = {x.invoice_id for x in lines.values()}
        whole = dict(InvoiceLine.objects.filter(invoice_id__in=invoice_ids, is_void=False)
                     .values("invoice_id").annotate(t=Sum("amount")).values_list("invoice_id", "t"))
        paid = dict(PaymentAllocation.objects.filter(invoice_id__in=invoice_ids,
                                                     payment__status=Payment.Status.CONFIRMED)
                    .values("invoice_id").annotate(t=Sum("amount")).values_list("invoice_id", "t"))
        for c in charges:
            m = c.reading.meter
            row = by_meter.setdefault(m.pk, UtilityRow(m.label, m.utility))
            place.setdefault(m.pk, m.property.name)
            row.billed += c.amount
            billed += c.amount
            line = lines.get(c.pk)
            if line is None:
                continue
            row.invoiced += c.amount
            invoiced += c.amount
            total = whole.get(line.invoice_id) or ZERO
            share = min(paid.get(line.invoice_id, ZERO) / total, Decimal(1)) if total else ZERO
            got = (c.amount * share).quantize(Decimal("0.01"))
            row.collected += got
            collected += got

    by_utility = []
    for key, label in Meter.Utility.choices:
        total = UtilityRow(str(label), key)
        for row in by_meter.values():
            if row.utility == key:
                total.add(row)
        if total.readings or total.billed:
            by_utility.append(total)
    by_prop: dict[str, UtilityRow] = {}
    for pk, row in by_meter.items():
        by_prop.setdefault(place[pk], UtilityRow(place[pk], "")).add(row)
    top = sorted(by_meter.items(), key=lambda kv: (-kv[1].usage, kv[1].name))[:UTILITY_TOP]
    for pk, row in top:
        row.name = f"{place[pk]} · {row.name}"
    return UtilitiesReport(
        start=start, end=end, show_money=show_money, usage=dict(usage_total), billed=billed, invoiced=invoiced,
        collected=collected, flagged=flagged, waiting=waiting, by_utility=by_utility,
        by_property=sorted(by_prop.values(), key=lambda r: -r.usage), by_meter=[row for _pk, row in top],
        months=[(m, m.strftime("%b %Y"), dict(v)) for m, v in sorted(months.items())],
        generated_at=now or timezone.now())
