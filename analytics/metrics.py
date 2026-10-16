"""The platform's own figures for the developer console (D-135).

Everything here is counted across organizations; no tenant name, phone or amount per person leaves this module.
Organization names appear because the developer runs those accounts. Months are calendar months, newest last.
"""

import datetime
from collections import Counter, defaultdict
from decimal import Decimal

from django.db.models import Count, Max, Min, Q, Sum
from django.db.models.functions import ExtractDay, TruncDate, TruncMonth
from django.utils import timezone
from django.utils.translation import gettext as _

from accounts.models import Membership, Organization, User
from audit.models import AuditEvent
from banking.models import BankTransaction
from billing.models import Invoice
from core import charts, pilot
from leases.models import Lease
from mpesa.models import MpesaTransaction
from notifications.models import Message
from payments.models import Payment
from properties.models import Property, Unit
from subscriptions.models import Subscription, SubscriptionInvoice, SubscriptionPayment
from support.models import AppOpen, Incident, SupportRequest

TREND_MONTHS = 6
NOISE = ("user.", "mfa.", "console.")  # sign-in and security events are not features


def first_of(day: datetime.date) -> datetime.date:
    return day.replace(day=1)


def months(count: int = TREND_MONTHS, today: datetime.date | None = None) -> list[datetime.date]:
    """The last `count` months, each as its first day, oldest first."""
    today = today or timezone.localdate()
    out, cur = [], first_of(today)
    for _i in range(count):
        out.append(cur)
        cur = first_of(cur - datetime.timedelta(days=1))
    return out[::-1]


def label(month: datetime.date) -> str:
    return month.strftime("%b %Y")


def _aware(day: datetime.date) -> datetime.datetime:
    return timezone.make_aware(datetime.datetime.combine(day, datetime.time.min))


def _day(value) -> datetime.date:
    return value.date() if isinstance(value, datetime.datetime) else value


def _keyed(rows) -> dict:
    """(date or datetime, number) rows as {date: number}."""
    return {_day(key): n for key, n in rows}


def _share(part, whole) -> int | None:
    return round(100 * part / whole) if whole else None


def _next_month(month: datetime.date) -> datetime.date:
    return first_of(month + datetime.timedelta(days=32))


# ---------------------------------------------------------------------------
# Overview
# ---------------------------------------------------------------------------


def overview(today: datetime.date | None = None) -> dict:
    today = today or timezone.localdate()
    now = timezone.now()
    d30, d7 = now - datetime.timedelta(days=30), now - datetime.timedelta(days=7)
    month_start = first_of(today)
    prev_start = first_of(month_start - datetime.timedelta(days=1))

    orgs = Organization.objects.all()
    active_30 = (AuditEvent.objects.filter(created_at__gte=d30, organization__isnull=False)
                 .values("organization").distinct().count())
    confirmed = Payment.objects.filter(status=Payment.Status.CONFIRMED)
    this_month = confirmed.filter(paid_at__gte=month_start)
    last_month = confirmed.filter(paid_at__gte=prev_start, paid_at__lt=month_start)
    mrr = revenue_mrr()
    users = User.objects.filter(is_active=True)
    return {
        "organizations": orgs.count(),
        "new_organizations_30": orgs.filter(created_at__gte=d30).count(),
        "active_organizations_30": active_30,
        "users": users.count(),
        "new_users_7": users.filter(date_joined__gte=d7).count(),
        "staff_seats": Membership.objects.filter(is_active=True).count(),
        "properties": Property.objects.count(),
        "units": Unit.objects.count(),
        "active_leases": Lease.all_objects.filter(status=Lease.Status.ACTIVE).count(),
        "rent_through_platform": int(this_month.aggregate(t=Sum("amount"))["t"] or 0),
        "rent_through_platform_prev": int(last_month.aggregate(t=Sum("amount"))["t"] or 0),
        "payments_this_month": this_month.count(),
        "mrr": mrr["total"],
        "paying_organizations": mrr["paying"],
        "trials": Subscription.objects.filter(status=Subscription.Status.TRIAL).count(),
        "past_due": Subscription.objects.filter(status=Subscription.Status.PAST_DUE).count(),
    }


def signups(count: int = 12, today: datetime.date | None = None) -> dict:
    ms = months(count, today)
    org_rows = _keyed(Organization.objects.filter(created_at__gte=_aware(ms[0]))
                      .annotate(m=TruncMonth("created_at")).values_list("m").annotate(n=Count("pk")))
    user_rows = _keyed(User.objects.filter(date_joined__gte=_aware(ms[0]))
                       .annotate(m=TruncMonth("date_joined")).values_list("m").annotate(n=Count("pk")))
    orgs = [org_rows.get(m, 0) for m in ms]
    users = [user_rows.get(m, 0) for m in ms]
    spec = charts.chart("bar", [label(m) for m in ms],
                        [charts.series(_("New organizations"), orgs, "1"),
                         charts.series(_("New users"), users, "3", type="line")], axis="count")
    return {"months": [label(m) for m in ms], "organizations": orgs, "users": users, "chart": spec}


def funnel() -> list[dict]:
    """Organizations that got as far as each step, with the share of those who signed up."""
    total = Organization.objects.count()
    steps = [
        (_("Signed up"), total),
        (_("Added a property"), Property.objects.values("organization").distinct().count()),
        (_("Added a unit"), Unit.objects.values("organization").distinct().count()),
        (_("Started a lease"), Lease.all_objects.filter(activated_at__isnull=False)
         .values("organization").distinct().count()),
        (_("Sent an invoice"), Invoice.objects.exclude(status__in=[Invoice.Status.DRAFT, Invoice.Status.VOID])
         .values("organization").distinct().count()),
        (_("Recorded a payment"), Payment.objects.filter(status=Payment.Status.CONFIRMED)
         .values("organization").distinct().count()),
        (_("Paid us"), SubscriptionPayment.objects.values("organization").distinct().count()),
    ]
    return [{"label": name, "count": n, "share": _share(n, total)} for name, n in steps]


def funnel_chart(rows: list[dict]) -> dict:
    return charts.chart("bar", [r["label"] for r in rows],
                        [charts.series(_("Organizations"), [r["count"] for r in rows], "1")],
                        axis="count", horizontal=True)


def cohorts(count: int = 6, today: datetime.date | None = None) -> list[dict]:
    """Organizations by sign-up month: how many were active in the last 30 days, and how many have paid us."""
    now = timezone.now()
    active = set(AuditEvent.objects.filter(created_at__gte=now - datetime.timedelta(days=30),
                                           organization__isnull=False).values_list("organization", flat=True))
    paid = set(SubscriptionPayment.objects.values_list("organization", flat=True))
    out = []
    for m in months(count, today):
        ids = list(Organization.objects.filter(created_at__gte=_aware(m), created_at__lt=_aware(_next_month(m)))
                   .values_list("pk", flat=True))
        out.append({"label": label(m), "signed_up": len(ids), "active": sum(i in active for i in ids),
                    "paid": sum(i in paid for i in ids)})
    return out


# ---------------------------------------------------------------------------
# Revenue
# ---------------------------------------------------------------------------


def revenue_mrr() -> dict:
    """Monthly recurring revenue: each paying subscription at its monthly equivalent. One-off plans count too."""
    total, paying, by_plan = Decimal("0"), 0, defaultdict(lambda: [0, Decimal("0")])
    subs = Subscription.objects.filter(
        status__in=[Subscription.Status.ACTIVE, Subscription.Status.PAST_DUE]).select_related("plan")
    for sub in subs:
        price = sub.plan.price(sub.interval)
        if not price or price <= 0:
            continue
        span = {"MONTHLY": 1, "YEARLY": 12}.get(sub.interval, 12 * sub.plan.one_off_years)
        monthly = Decimal(price) / span
        total += monthly
        paying += 1
        by_plan[sub.plan.name][0] += 1
        by_plan[sub.plan.name][1] += monthly
    return {"total": int(total), "paying": paying, "arr": int(total * 12),
            "by_plan": sorted(((name, n, int(v)) for name, (n, v) in by_plan.items()), key=lambda r: -r[2])}


def revenue(count: int = 12, today: datetime.date | None = None) -> dict:
    today = today or timezone.localdate()
    ms = months(count, today)
    by_method: dict[str, dict] = defaultdict(dict)
    for m, method, total in (SubscriptionPayment.objects.filter(paid_on__gte=ms[0]).annotate(m=TruncMonth("paid_on"))
                             .values_list("m", "method").annotate(t=Sum("amount"))):
        by_method[method][_day(m)] = total
    collected = [int(sum(v.get(m, 0) for v in by_method.values())) for m in ms]
    plan_counts = Counter(Subscription.objects.values_list("plan__name", flat=True))
    status_counts = dict(Subscription.objects.values_list("status").annotate(n=Count("pk")))
    overdue = SubscriptionInvoice.objects.filter(status=SubscriptionInvoice.Status.OPEN, due_on__lt=today)
    mrr = revenue_mrr()
    series = [charts.series(method.title(), [int(by_method[method].get(m, 0)) for m in ms], str(1 + i % 5))
              for i, method in enumerate(sorted(by_method))]
    return {
        "mrr": mrr, "months": [label(m) for m in ms], "collected": collected, "collected_total": sum(collected),
        "status": status_counts, "plans": plan_counts.most_common(),
        "conversion": _share(mrr["paying"], Organization.objects.count()),
        "overdue_count": overdue.count(), "overdue_total": int(overdue.aggregate(t=Sum("total"))["t"] or 0),
        "chart": charts.chart("bar", [label(m) for m in ms],
                              series or [charts.series(_("Collected"), collected, "1")], stacked=True),
        "plan_chart": charts.doughnut(_("Organizations"), charts.fold(plan_counts.items(), _("Other")),
                                      [str(i) for i in range(1, 6)], axis="count"),
    }


# ---------------------------------------------------------------------------
# How tenants pay
# ---------------------------------------------------------------------------

BUCKETS = ["on_time", "late_7", "late_30", "late_more", "unpaid"]


def _bucket(overdue_after: datetime.date, status: str, last_paid, today: datetime.date) -> str | None:
    """Where one invoice falls, or None while it is unpaid and not yet overdue."""
    if status == Invoice.Status.PAID and last_paid:
        late = (last_paid - overdue_after).days
        return "on_time" if late <= 0 else "late_7" if late <= 7 else "late_30" if late <= 30 else "late_more"
    return "unpaid" if overdue_after < today else None


def behaviour(count: int = TREND_MONTHS, today: datetime.date | None = None) -> dict:
    """How rent is paid across every organization: on time or late, by method, by day of the month, matched
    by itself or by hand. An invoice counts once it is paid, or once it is overdue and still unpaid."""
    today = today or timezone.localdate()
    ms = months(count, today)
    start = ms[0]
    rows = (Invoice.objects.filter(due_date__gte=start, due_date__lte=today)
            .exclude(status__in=[Invoice.Status.DRAFT, Invoice.Status.VOID])
            .annotate(last_paid=Max("payment_allocations__payment__paid_at",
                                    filter=Q(payment_allocations__payment__status=Payment.Status.CONFIRMED)))
            .values_list("organization", "due_date", "overdue_after", "status", "last_paid"))
    totals: Counter = Counter()
    per_month = {m: Counter() for m in ms}
    per_org: dict[int, Counter] = defaultdict(Counter)
    for org_id, due, after, status, last_paid in rows.iterator():
        bucket = _bucket(after, status, last_paid, today)
        if bucket is None:
            continue
        totals[bucket] += 1
        per_month[first_of(due)][bucket] += 1
        per_org[org_id][bucket] += 1

    def on_time(counter):
        return _share(counter["on_time"], sum(counter.values()))

    titles = {"on_time": _("On time"), "late_7": _("1 to 7 days late"), "late_30": _("8 to 30 days late"),
              "late_more": _("Over 30 days late"), "unpaid": _("Still unpaid")}
    colors = {"on_time": "1", "late_7": "late-1", "late_30": "late-2", "late_more": "late-3", "unpaid": "late-4"}
    timeliness = charts.chart("bar", [label(m) for m in ms],
                              [charts.series(titles[b], [per_month[m][b] for m in ms], colors[b]) for b in BUCKETS],
                              axis="count", share=True, total=_("Invoices"))

    confirmed = Payment.objects.filter(status=Payment.Status.CONFIRMED)
    methods: dict[str, dict] = defaultdict(dict)
    for m, method, total in (confirmed.filter(paid_at__gte=start).annotate(m=TruncMonth("paid_at"))
                             .values_list("m", "method").annotate(t=Sum("amount"))):
        methods[method][_day(m)] = total
    names = dict(Payment.Method.choices)
    ranked = sorted(methods, key=lambda k: -sum(methods[k].values()))
    method_chart = charts.chart("bar", [label(m) for m in ms],
                                [charts.series(names.get(k, k), [int(methods[k].get(m, 0)) for m in ms],
                                               str(1 + i % 5)) for i, k in enumerate(ranked)], stacked=True)
    method_share = [(names.get(k, k), int(sum(methods[k].values()))) for k in ranked]

    window = today - datetime.timedelta(days=90)
    days = dict(confirmed.filter(paid_at__gte=window).annotate(d=ExtractDay("paid_at"))
                .values_list("d").annotate(n=Count("pk")))
    pay_days = charts.chart("bar", [str(d) for d in range(1, 32)],
                            [charts.series(_("Payments"), [days.get(d, 0) for d in range(1, 32)], "3")], axis="count")
    reversed_90 = Payment.objects.filter(status=Payment.Status.REVERSED, paid_at__gte=window).count()
    confirmed_90 = confirmed.filter(paid_at__gte=window).count()

    matching = []
    for m in ms:
        nxt = _next_month(m)
        mp = MpesaTransaction.objects.filter(paid_at__gte=_aware(m), paid_at__lt=_aware(nxt)).aggregate(
            auto=Count("pk", filter=Q(status="MATCHED") & ~Q(matched_by="MANUAL")),
            hand=Count("pk", filter=Q(status="MATCHED", matched_by="MANUAL")),
            waiting=Count("pk", filter=Q(status__in=["UNMATCHED", "FLAGGED", "RECEIVED"])))
        bk = BankTransaction.objects.filter(posted_on__gte=m, posted_on__lt=nxt).aggregate(
            auto=Count("pk", filter=Q(status="MATCHED") & ~Q(matched_by="MANUAL")),
            hand=Count("pk", filter=Q(status="MATCHED", matched_by="MANUAL")),
            waiting=Count("pk", filter=Q(status="UNMATCHED")))
        auto, hand, waiting = mp["auto"] + bk["auto"], mp["hand"] + bk["hand"], mp["waiting"] + bk["waiting"]
        matching.append({"label": label(m), "auto": auto, "hand": hand, "waiting": waiting,
                         "auto_share": _share(auto, auto + hand + waiting)})
    match_chart = charts.chart("bar", [r["label"] for r in matching],
                               [charts.series(_("Matched on its own"), [r["auto"] for r in matching], "1"),
                                charts.series(_("Matched by someone"), [r["hand"] for r in matching], "2"),
                                charts.series(_("Waiting"), [r["waiting"] for r in matching], "late-3")],
                               axis="count", stacked=True)

    org_names = dict(Organization.objects.filter(pk__in=per_org).values_list("pk", "name"))
    ranking = sorted(({"organization": org_names.get(o, "?"), "id": o, "invoices": sum(c.values()),
                       "on_time": on_time(c), "unpaid": c["unpaid"]} for o, c in per_org.items()),
                     key=lambda r: (r["on_time"] if r["on_time"] is not None else 101, -r["invoices"]))
    return {
        "judged": sum(totals.values()), "on_time_share": on_time(totals),
        "totals": [(titles[b], totals[b]) for b in BUCKETS],
        "months": [{"label": label(m), "judged": sum(per_month[m].values()), "on_time": on_time(per_month[m])}
                   for m in ms],
        "timeliness_chart": timeliness, "method_chart": method_chart, "method_share": method_share,
        "pay_days_chart": pay_days, "matching": matching, "match_chart": match_chart,
        "reversal_share": _share(reversed_90, reversed_90 + confirmed_90), "reversed_90": reversed_90,
        "ranking": ranking, "by_org": {o: on_time(c) for o, c in per_org.items()},
    }


# ---------------------------------------------------------------------------
# Usage
# ---------------------------------------------------------------------------


def engagement(days: int = 30, today: datetime.date | None = None) -> dict:
    today = today or timezone.localdate()
    now = timezone.now()
    since = now - datetime.timedelta(days=days)
    events = AuditEvent.objects.filter(created_at__gte=since)
    day_list = [today - datetime.timedelta(days=i) for i in range(days - 1, -1, -1)]
    active = _keyed(events.filter(actor__isnull=False).annotate(d=TruncDate("created_at"))
                    .values_list("d").annotate(n=Count("actor", distinct=True)))
    logins = _keyed(events.filter(action="user.login").annotate(d=TruncDate("created_at"))
                    .values_list("d").annotate(n=Count("pk")))
    dau = [active.get(d, 0) for d in day_list]
    people = AuditEvent.objects.filter(actor__isnull=False)
    wau = people.filter(created_at__gte=now - datetime.timedelta(days=7)).values("actor").distinct().count()
    mau = people.filter(created_at__gte=now - datetime.timedelta(days=30)).values("actor").distinct().count()

    usage: Counter = Counter()
    orgs_using: dict[str, set] = defaultdict(set)
    quiet = events.exclude(action__startswith=NOISE[0])
    for prefix in NOISE[1:]:
        quiet = quiet.exclude(action__startswith=prefix)
    for action, org in quiet.values_list("action", "organization").iterator():
        feature = action.split(".")[0].replace("_", " ")
        usage[feature] += 1
        if org:
            orgs_using[feature].add(org)
    features = [{"feature": f.capitalize(), "events": n, "organizations": len(orgs_using[f])}
                for f, n in usage.most_common(15)]

    opens_since = AppOpen.objects.filter(day__gte=day_list[0])
    opens_by = _keyed(opens_since.values_list("day").annotate(n=Sum("count")))
    messages = Message.objects.filter(created_at__gte=since)
    sent = {c: (n, f) for c, n, f in messages.values_list("channel").annotate(
        n=Count("pk"), f=Count("pk", filter=Q(status=Message.Status.FAILED)))}
    ids = [d.strftime("%d %b") for d in day_list]
    return {
        "days": days, "dau_today": dau[-1], "wau": wau, "mau": mau, "stickiness": _share(dau[-1], mau),
        "logins": sum(logins.values()), "features": features,
        "opens_total": dict(opens_since.values_list("platform").annotate(n=Sum("count"))),
        "audience": dict(opens_since.values_list("audience").annotate(n=Sum("count"))),
        "messages": [{"channel": c, "sent": n, "failed": f, "failed_share": _share(f, n)}
                     for c, (n, f) in sorted(sent.items())],
        "active_chart": charts.chart("line", ids, [charts.series(_("People active"), dau, "1")], axis="count"),
        "login_chart": charts.chart("bar", ids, [charts.series(_("Logins"), [logins.get(d, 0) for d in day_list],
                                                               "3")], axis="count"),
        "opens_chart": charts.chart("bar", ids, [charts.series(_("App opens"), [int(opens_by.get(d, 0))
                                                                                for d in day_list], "5")],
                                    axis="count"),
        "feature_chart": charts.chart("bar", [f["feature"] for f in features[:10]],
                                      [charts.series(_("Actions"), [f["events"] for f in features[:10]], "1")],
                                      axis="count", horizontal=True),
    }


# ---------------------------------------------------------------------------
# Organizations
# ---------------------------------------------------------------------------


def organizations(*, query: str = "", status: str = "", today: datetime.date | None = None) -> list[dict]:
    today = today or timezone.localdate()
    qs = Organization.objects.select_related("subscription__plan").order_by("name")
    if query:
        qs = qs.filter(Q(name__icontains=query) | Q(billing_email__icontains=query))
    if status:
        qs = qs.filter(status=status)
    scores = {s.organization: s for s in pilot.scores(today, organizations=qs)}
    last_seen = dict(AuditEvent.objects.filter(organization__in=qs).values_list("organization")
                     .annotate(t=Max("created_at")))
    units = dict(Unit.objects.filter(organization__in=qs).values_list("organization").annotate(n=Count("pk")))
    leases = dict(Lease.all_objects.filter(organization__in=qs, status=Lease.Status.ACTIVE)
                  .values_list("organization").annotate(n=Count("pk")))
    behave = behaviour(today=today)["by_org"]
    now = timezone.now()
    out = []
    for org in qs:
        sub = getattr(org, "subscription", None)
        seen = last_seen.get(org.pk)
        score = scores.get(org.name)
        out.append({
            "org": org, "plan": sub.plan.name if sub else "—", "sub_status": sub.get_status_display() if sub else "—",
            "units": units.get(org.pk, 0), "leases": leases.get(org.pk, 0), "last_seen": seen,
            "idle_days": (now - seen).days if seen else None, "score": score,
            "collected": score.collected if score else 0, "on_time": behave.get(org.pk),
            "auto_share": score.auto_share if score else None, "staff": score.staff if score else 0,
        })
    return out


def organization_detail(org: Organization, today: datetime.date | None = None) -> dict:
    today = today or timezone.localdate()
    ms = months(TREND_MONTHS, today)
    paid = _keyed(Payment.objects.filter(organization=org, status=Payment.Status.CONFIRMED, paid_at__gte=ms[0])
                  .annotate(m=TruncMonth("paid_at")).values_list("m").annotate(t=Sum("amount")))
    billed = _keyed(Invoice.objects.filter(organization=org, period_start__gte=ms[0])
                    .exclude(status__in=[Invoice.Status.DRAFT, Invoice.Status.VOID])
                    .annotate(m=TruncMonth("period_start")).values_list("m").annotate(t=Sum("total")))
    collected = [int(paid.get(m, 0)) for m in ms]
    invoiced = [int(billed.get(m, 0)) for m in ms]
    score = pilot.scores(today, organizations=Organization.objects.filter(pk=org.pk))[0]
    events = AuditEvent.objects.filter(organization=org)
    recent = events.filter(created_at__gte=timezone.now() - datetime.timedelta(days=30))
    top = Counter(a.split(".")[0].replace("_", " ") for a in recent.values_list("action", flat=True)
                  if not a.startswith(NOISE))
    return {
        "score": score, "subscription": getattr(org, "subscription", None),
        "units": Unit.objects.filter(organization=org).count(),
        "properties": Property.objects.filter(organization=org).count(),
        "leases": Lease.all_objects.filter(organization=org, status=Lease.Status.ACTIVE).count(),
        "last_seen": events.aggregate(t=Max("created_at"))["t"],
        "top_features": top.most_common(8), "on_time": behaviour(today=today)["by_org"].get(org.pk),
        "months": [{"label": label(m), "billed": invoiced[i], "collected": collected[i]} for i, m in enumerate(ms)],
        "chart": charts.chart("bar", [label(m) for m in ms],
                              [charts.series(_("Billed"), invoiced, "1-soft"),
                               charts.series(_("Received"), collected, "1")]),
        "open_support": SupportRequest.objects.filter(organization=org).exclude(status="CLOSED").count(),
    }


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


def health() -> dict:
    now = timezone.now()
    d7 = now - datetime.timedelta(days=7)
    messages = [{"channel": c, "total": n, "failed": f, "failed_share": _share(f, n)}
                for c, n, f in Message.objects.filter(created_at__gte=d7).values_list("channel").annotate(
                    n=Count("pk"), f=Count("pk", filter=Q(status=Message.Status.FAILED)))]
    stuck = MpesaTransaction.objects.filter(status__in=["UNMATCHED", "FLAGGED"],
                                            paid_at__lt=now - datetime.timedelta(days=2))
    support = SupportRequest.objects.exclude(status="CLOSED")
    oldest = support.aggregate(t=Min("created_at"))["t"]
    return {
        "messages": messages, "stuck_mpesa": stuck.count(),
        "stuck_orgs": stuck.values("organization").distinct().count(),
        "support_open": support.count(), "support_oldest_days": (now - oldest).days if oldest else None,
        "support_by_kind": list(support.values_list("kind").annotate(n=Count("pk")).order_by("-n")),
        "incidents": list(Incident.objects.exclude(status="RESOLVED").values_list("title", "impact")),
        "security": {
            "failed_codes_7": AuditEvent.objects.filter(action="mfa.failed", created_at__gte=d7).count(),
            "mfa_resets_30": AuditEvent.objects.filter(
                action="mfa.admin_reset", created_at__gte=now - datetime.timedelta(days=30)).count(),
            "frozen": Organization.objects.filter(status=Organization.Status.FROZEN).count(),
            "read_only": Organization.objects.filter(status=Organization.Status.READ_ONLY).count(),
        },
        "unverified_users": User.objects.filter(is_active=True, phone_verified_at__isnull=True,
                                                date_joined__lt=now - datetime.timedelta(days=1)).count(),
    }


def watchlist(today: datetime.date | None = None) -> list[dict]:
    """Organizations that look like they may leave or need a call, worst first, each with the reasons (D-136).

    Rules are plain so the developer can see why: silent, subscription trouble, a trial about to end with little
    set up, rent not being collected, money left unmatched."""
    today = today or timezone.localdate()
    out = []
    for r in organizations(today=today):
        org, sub, score = r["org"], getattr(r["org"], "subscription", None), r["score"]
        age = (today - timezone.localtime(org.created_at).date()).days
        why: list[tuple[int, str]] = []
        idle = r["idle_days"]
        if idle is None and age >= 7:
            why.append((3, "Never used since signing up"))
        elif idle is not None and idle >= 30:
            why.append((3, f"No activity for {idle} days"))
        elif idle is not None and idle >= 14:
            why.append((2, f"No activity for {idle} days"))
        if sub and sub.status == "PAST_DUE":
            why.append((3, "Subscription payment is due"))
        elif sub and sub.status == "LAPSED":
            why.append((3, "Subscription has lapsed"))
        elif sub and sub.status == "TRIAL" and sub.trial_ends_on and 0 <= (sub.trial_ends_on - today).days <= 7:
            left = (sub.trial_ends_on - today).days
            why.append((2 if r["leases"] else 3, f"Trial ends in {left} days" + ("" if r["leases"] else " with no active lease")))
        if r["leases"] and score and not score.collected and today.day > 10:
            why.append((2, "No rent collected this month"))
        if score and score.auto_share is not None and score.waiting >= 5:
            why.append((1, f"{score.waiting} payments waiting to be matched"))
        if r["leases"] == 0 and age >= 14 and idle is not None:
            why.append((1, "No active lease after two weeks"))
        if why:
            out.append({**r, "risk": sum(w for w, _ in why), "reasons": [t for _, t in why]})
    return sorted(out, key=lambda x: (-x["risk"], x["org"].name))
