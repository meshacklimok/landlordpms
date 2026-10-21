"""More platform figures for the console (D-138): the developer's own money, growth, where the app is used, and how
close messaging and AI are to their limits. Same rules as metrics.py: counts and shares across organizations, never a
tenant's name, phone or amount."""

import datetime
from collections import Counter, defaultdict
from decimal import Decimal

from django.conf import settings
from django.db.models import Case, CharField, Count, Q, Sum, Value, When
from django.db.models.functions import TruncMonth
from django.utils import timezone
from django.utils.translation import gettext as _

from accounts.models import Membership, Organization, User
from ai_assistant.client import cost_kes
from ai_assistant.models import Question
from core import charts
from core.kenya import County
from leases.models import Lease
from meters.models import Scan
from mpesa.models import MpesaTransaction
from notifications import catalog
from notifications.models import Message
from payments.models import Payment
from properties.models import Property, Unit
from subscriptions.models import SmsWalletEntry, Subscription, SubscriptionPayment
from support.models import SupportRequest
from tenants.models import Tenant

from .metrics import _aware, _day, _keyed, _share, first_of, label, months, revenue_mrr
from .models import PlatformExpense

SENT = [Message.Status.SENT, Message.Status.DELIVERED]


def _cumulative(base: int, per_month: list[int]) -> list[int]:
    out, total = [], base
    for n in per_month:
        total += n
        out.append(total)
    return out


def _monthly(qs, field: str, ms: list[datetime.date]) -> list[int]:
    rows = _keyed(qs.filter(**{f"{field}__gte": _aware(ms[0])}).annotate(m=TruncMonth(field))
                  .values_list("m").annotate(n=Count("pk")))
    return [rows.get(m, 0) for m in ms]


# ---------------------------------------------------------------------------
# The developer's own money
# ---------------------------------------------------------------------------

def money(count: int = 12, today: datetime.date | None = None) -> dict:
    """Subscription money received less what it costs to run, month by month."""
    today = today or timezone.localdate()
    ms = months(count, today)
    expenses = list(PlatformExpense.objects.all())
    received_rows = _keyed(SubscriptionPayment.objects.filter(paid_on__gte=ms[0]).annotate(m=TruncMonth("paid_on"))
                           .values_list("m").annotate(t=Sum("amount")))
    received = [int(received_rows.get(m, 0)) for m in ms]
    by_cat: dict[str, list[int]] = defaultdict(lambda: [0] * len(ms))
    for e in expenses:
        for i, m in enumerate(ms):
            if e.applies_in(m):
                by_cat[e.category][i] += int(e.amount)
    costs = [sum(v[i] for v in by_cat.values()) for i in range(len(ms))]
    net = [r - c for r, c in zip(received, costs, strict=False)]
    names = dict(PlatformExpense.Category.choices)
    cat_totals = sorted(((str(names[c]), sum(v)) for c, v in by_cat.items()), key=lambda r: -r[1])
    running, cumulative = 0, []
    for n in net:
        running += n
        cumulative.append(running)
    mrr_cost = costs[-1] if costs else 0
    paying = revenue_mrr()["paying"]
    return {
        "months": [label(m) for m in ms], "received": received, "costs": costs, "net": net, "cumulative": cumulative,
        "received_total": sum(received), "costs_total": sum(costs), "net_total": sum(net),
        "margin": _share(sum(net), sum(received)) if sum(received) > 0 else None,
        "this_month": {"received": received[-1], "costs": costs[-1], "net": net[-1]},
        "cost_per_paying": int(mrr_cost / paying) if paying else None, "paying": paying,
        "categories": cat_totals,
        "chart": charts.chart("bar", [label(m) for m in ms],
                              [charts.series(_("Received"), received, "1"),
                               charts.series(_("Costs"), costs, "late-3"),
                               charts.series(_("Net profit"), net, "3", type="line")]),
        "cumulative_chart": charts.chart("line", [label(m) for m in ms],
                                         [charts.series(_("Profit so far"), cumulative, "1")]),
        "category_chart": charts.doughnut(_("Costs"), charts.fold(cat_totals, _("Other")),
                                          [str(i) for i in range(1, 6)]),
        "category_by_month": {str(names[c]): v for c, v in by_cat.items()},
    }


def statement(count: int = 12, today: datetime.date | None = None) -> dict:
    """Everything that came in and everything that went out, month by month, on one page.

    Income is what landlords paid us. Expenses are what you entered, except SMS, assistant questions and photo scans,
    which are taken from what the providers charged (so they need not be entered by hand). In a month that has a
    tracked SMS or AI figure, a hand-entered SMS or AI cost for that month is left out so nothing counts twice."""
    today = today or timezone.localdate()
    ms = months(count, today)
    start, end = _aware(ms[0]), _aware(first_of(today + datetime.timedelta(days=32)))
    window = {"created_at__gte": start, "created_at__lt": end}

    def by_month(qs, field="created_at", value="t"):
        rows = _keyed(qs.annotate(m=TruncMonth(field)).values_list("m").annotate(t=Sum(value)))
        return [rows.get(m, 0) or 0 for m in ms]

    income = [int(v) for v in by_month(SubscriptionPayment.objects.filter(paid_on__gte=ms[0]), "paid_on", "amount")]
    sms = [Decimal(v) for v in by_month(Message.objects.filter(channel=catalog.SMS, status__in=SENT, **window),
                                        value="cost")]
    scans = [Decimal(v) for v in by_month(Scan.objects.filter(**window), value="cost")]
    questions = Question.objects.filter(**window).exclude(status=Question.Status.LIMITED)
    ai_in = by_month(questions, value="input_tokens")
    ai_out = by_month(questions, value="output_tokens")
    ai = [cost_kes(i, o) + s for i, o, s in zip(ai_in, ai_out, scans, strict=False)]
    names = dict(PlatformExpense.Category.choices)
    entered: dict[str, list[Decimal]] = defaultdict(lambda: [Decimal("0")] * len(ms))
    for e in PlatformExpense.objects.all():
        for i, m in enumerate(ms):
            if not e.applies_in(m):
                continue
            if ((e.category == PlatformExpense.Category.SMS and sms[i] > 0)
                    or (e.category == PlatformExpense.Category.AI and ai[i] > 0)):
                continue
            entered[e.category][i] += e.amount
    rows = [{"name": _("SMS sent (from the provider's charges)"), "tracked": True, "values": sms},
            {"name": _("Assistant and photo scans (from OpenAI use)"), "tracked": True, "values": ai}]
    rows += [{"name": str(names[c]), "tracked": False, "values": v}
             for c, v in sorted(entered.items(), key=lambda kv: -sum(kv[1])) if sum(v)]
    for r in rows:
        r["values"] = [int(round(v)) for v in r["values"]]
        r["total"] = sum(r["values"])
    expenses = [sum(r["values"][i] for r in rows) for i in range(len(ms))]
    net = [a - b for a, b in zip(income, expenses, strict=False)]
    labels = [label(m) for m in ms]
    return {
        "months": labels, "income": income, "expenses": expenses, "net": net, "rows": rows,
        "income_total": sum(income), "expenses_total": sum(expenses), "net_total": sum(net),
        "this_month": {"income": income[-1], "expenses": expenses[-1], "net": net[-1]},
        "table": [{"month": labels[i], "income": income[i], "expenses": expenses[i], "net": net[i]}
                  for i in range(len(ms))],
        "chart": charts.chart("bar", labels, [charts.series(_("Income"), income, "1"),
                                              charts.series(_("Expenses"), expenses, "late-3"),
                                              charts.series(_("Net"), net, "3", type="line")]),
    }


# ---------------------------------------------------------------------------
# Growth
# ---------------------------------------------------------------------------

LANDLORD_TYPES = [Organization.Type.INDIVIDUAL, Organization.Type.COMPANY]


def growth(count: int = 12, today: datetime.date | None = None) -> dict:
    today = today or timezone.localdate()
    ms = months(count, today)
    start = _aware(ms[0])
    orgs = Organization.all_objects
    orgs_new = _monthly(orgs.all(), "created_at", ms)
    users_new = _monthly(User.objects.all(), "date_joined", ms)
    tenants_new = _monthly(Tenant.all_objects.all(), "created_at", ms)
    units_new = _monthly(Unit.all_objects.all(), "created_at", ms)
    left_new = _monthly(orgs.filter(archived_at__isnull=False).exclude(archived_at__lt=start), "archived_at", ms)
    cum_orgs = _cumulative(orgs.filter(created_at__lt=start).count(), orgs_new)
    cum_users = _cumulative(User.objects.filter(date_joined__lt=start).count(), users_new)
    cum_tenants = _cumulative(Tenant.all_objects.filter(created_at__lt=start).count(), tenants_new)
    cum_units = _cumulative(Unit.all_objects.filter(created_at__lt=start).count(), units_new)
    pace = [_share(n * 100, c - n) if c - n else None for n, c in zip(orgs_new, cum_orgs, strict=False)]  # % growth a month

    # Who the staff are: caretakers, owners, agents and so on, counted once per person.
    role_rows = (Membership.objects.filter(is_active=True)
                 .values("role__based_on_template__name", "role__name").annotate(n=Count("user", distinct=True)))
    roles: Counter = Counter()
    for row in role_rows:
        roles[row["role__based_on_template__name"] or row["role__name"]] += row["n"]
    types = dict(Organization.objects.values_list("org_type").annotate(n=Count("pk")))
    type_names = dict(Organization.Type.choices)

    # How big organizations are, in units.
    unit_counts = dict(Unit.objects.values_list("organization").annotate(n=Count("pk")))
    all_orgs = Organization.objects.count()
    sizes = [unit_counts.get(pk, 0) for pk in Organization.objects.values_list("pk", flat=True)]
    with_units = [n for n in sizes if n]
    buckets = [(_("No units yet"), sum(1 for n in sizes if n == 0)), (_("1 to 5 units"), sum(1 for n in sizes if 1 <= n <= 5)),
               (_("6 to 20"), sum(1 for n in sizes if 6 <= n <= 20)), (_("21 to 50"), sum(1 for n in sizes if 21 <= n <= 50)),
               (_("More than 50"), sum(1 for n in sizes if n > 50))]
    with_units.sort()
    median = with_units[len(with_units) // 2] if with_units else 0

    # Who leaves: archived, frozen or a subscription that lapsed.
    ever = orgs.count()
    archived = orgs.filter(archived_at__isnull=False).count()
    lapsed = Subscription.objects.filter(status=Subscription.Status.LAPSED).count()
    frozen = Organization.objects.filter(status=Organization.Status.FROZEN).count()
    left = archived + lapsed + frozen
    # New organizations by kind each month: landlords against agencies and the rest.
    by_kind = {"landlords": [0] * len(ms), "agencies": [0] * len(ms), "others": [0] * len(ms)}
    for created, kind in orgs.filter(created_at__gte=start).values_list("created_at", "org_type"):
        i = ms.index(created.astimezone(timezone.get_current_timezone()).date().replace(day=1)) \
            if created.astimezone(timezone.get_current_timezone()).date().replace(day=1) in ms else None
        if i is None:
            continue
        key = "landlords" if kind in LANDLORD_TYPES else "agencies" if kind == Organization.Type.AGENCY else "others"
        by_kind[key][i] += 1

    paying = Subscription.objects.filter(status__in=["ACTIVE", "PAST_DUE"]).count()
    tenants_total = Tenant.all_objects.count()
    return {
        "months": [label(m) for m in ms],
        "totals": {"organizations": all_orgs, "users": User.objects.filter(is_active=True).count(),
                   "tenants": tenants_total, "units": Unit.objects.count(), "paying": paying,
                   "paying_share": _share(paying, all_orgs), "avg_units": round(sum(sizes) / len(sizes), 1) if sizes else 0,
                   "median_units": median, "tenants_per_org": round(tenants_total / all_orgs, 1) if all_orgs else 0,
                   "active_tenants": Tenant.objects.filter(status=Tenant.Status.ACTIVE).count()},
        "churn": {"ever": ever, "archived": archived, "lapsed": lapsed, "frozen": frozen, "left": left,
                  "rate": _share(left, ever), "lost_by_month": left_new, "kept_share": _share(ever - left, ever)},
        "pace": pace, "orgs_new": orgs_new, "users_new": users_new, "tenants_new": tenants_new,
        "roles": roles.most_common(), "types": [(str(type_names[k]), v) for k, v in types.items()],
        "buckets": buckets, "by_kind": by_kind,
        "growth_chart": charts.chart("line", [label(m) for m in ms],
                                     [charts.series(_("Organizations"), cum_orgs, "1"),
                                      charts.series(_("People with accounts"), cum_users, "2"),
                                      charts.series(_("Tenants"), cum_tenants, "3"),
                                      charts.series(_("Units"), cum_units, "4")], axis="count"),
        "new_chart": charts.chart("bar", [label(m) for m in ms],
                                  [charts.series(_("Landlords"), by_kind["landlords"], "1"),
                                   charts.series(_("Agencies"), by_kind["agencies"], "2"),
                                   charts.series(_("Estates, water and waste"), by_kind["others"], "3"),
                                   charts.series(_("Left"), left_new, "late-3", type="line")],
                                  axis="count", stacked=True),
        "role_chart": charts.doughnut(_("People"), charts.fold(roles.items(), _("Other")),
                                      [str(i) for i in range(1, 6)], axis="count"),
        "size_chart": charts.chart("bar", [str(n) for n, _c in buckets], [charts.series(_("Organizations"), [c for _n, c in buckets], "1")],
                                   axis="count"),
        "rows": [{"month": label(m), "orgs": orgs_new[i], "users": users_new[i], "tenants": tenants_new[i],
                  "units": units_new[i], "total_orgs": cum_orgs[i], "left": left_new[i], "pace": pace[i]}
                 for i, m in enumerate(ms)],
    }


# ---------------------------------------------------------------------------
# Where the app is used
# ---------------------------------------------------------------------------

def geography() -> dict:
    names = dict(County.choices)
    props = {c: n for c, n in Property.objects.values_list("county").annotate(n=Count("pk"))}
    orgs = {c: n for c, n in Property.objects.values_list("county").annotate(n=Count("organization", distinct=True))}
    units = {c: n for c, n in Unit.objects.values_list("property__county").annotate(n=Count("pk"))}
    leases = {c: n for c, n in Lease.all_objects.filter(status=Lease.Status.ACTIVE)
              .values_list("unit__property__county").annotate(n=Count("pk"))}
    rows = [{"county": str(names.get(c) or _("Not set")), "organizations": orgs.get(c, 0), "properties": props.get(c, 0),
             "units": units.get(c, 0), "leases": leases.get(c, 0)} for c in props]
    rows.sort(key=lambda r: (-r["units"], r["county"]))
    total_units = sum(r["units"] for r in rows)
    for r in rows:
        r["share"] = _share(r["units"], total_units)
    top = [r for r in rows if r["county"] != str(_("Not set"))][:12]
    return {"rows": rows, "counties": len([r for r in rows if r["county"] != str(_("Not set"))]),
            "missing": next((r["properties"] for r in rows if r["county"] == str(_("Not set"))), 0),
            "chart": charts.chart("bar", [r["county"] for r in top],
                                  [charts.series(_("Units"), [r["units"] for r in top], "1")],
                                  axis="count", horizontal=True)}


# ---------------------------------------------------------------------------
# Tenants' payment methods
# ---------------------------------------------------------------------------

def payment_methods(count: int = 6, today: datetime.date | None = None) -> dict:
    today = today or timezone.localdate()
    ms = months(count, today)
    names = dict(Payment.Method.choices)
    qs = Payment.objects.filter(status=Payment.Status.CONFIRMED, paid_at__gte=ms[0])
    totals = {m: (n, int(t or 0)) for m, n, t in qs.values_list("method").annotate(n=Count("pk"), t=Sum("amount"))}
    by_month: dict[str, dict] = defaultdict(dict)
    for m, method, n in qs.annotate(m=TruncMonth("paid_at")).values_list("m", "method").annotate(n=Count("pk")):
        by_month[method][_day(m)] = n
    everything = Payment.objects.filter(status=Payment.Status.CONFIRMED)
    ever = everything.aggregate(n=Count("pk"), t=Sum("amount"))
    n_all = sum(n for n, _t in totals.values())
    t_all = sum(t for _n, t in totals.values())
    rows = [{"method": str(names.get(m, m)), "payments": n, "amount": t, "share_count": _share(n, n_all),
             "share_amount": _share(t, t_all)} for m, (n, t) in sorted(totals.items(), key=lambda x: -x[1][0])]
    return {"rows": rows, "ever_payments": ever["n"] or 0, "ever_amount": int(ever["t"] or 0),
            "chart": charts.chart("bar", [label(m) for m in ms],
                                  [charts.series(str(names.get(method, method)), [by_month[method].get(m, 0) for m in ms],
                                                 str(1 + i % 5)) for i, method in enumerate(sorted(by_month))],
                                  axis="count", stacked=True),
            "count_chart": charts.doughnut(_("Payments"), charts.fold([(r["method"], r["payments"]) for r in rows], _("Other")),
                                           [str(i) for i in range(1, 6)], axis="count")}


def rent_collected(count: int = 12, today: datetime.date | None = None) -> dict:
    """Confirmed tenant rent across every organization, by how it reached us: through the site (a Paybill/Till
    payment or a payment request), from an imported statement, or recorded by hand. Amounts only, no names."""
    today = today or timezone.localdate()
    ms = months(count, today)
    live = Q(mpesa_transaction__source__in=[MpesaTransaction.Source.C2B, MpesaTransaction.Source.STK])
    statement = Q(mpesa_transaction__source=MpesaTransaction.Source.STATEMENT) | Q(bank_transaction__isnull=False)
    channel = Case(When(live, then=Value("site")), When(statement, then=Value("statement")), default=Value("hand"),
                   output_field=CharField())
    qs = Payment.objects.filter(status=Payment.Status.CONFIRMED).annotate(channel=channel)
    names = {"site": _("Through the site"), "statement": _("Statement imports"), "hand": _("Recorded by hand")}

    ever = {c: int(t or 0) for c, t in qs.values_list("channel").annotate(t=Sum("amount"))}
    this_month = {c: int(t or 0) for c, t in qs.filter(paid_at__gte=ms[-1]).values_list("channel").annotate(t=Sum("amount"))}
    by_month: dict[str, dict] = defaultdict(dict)
    for m, c, t in (qs.filter(paid_at__gte=ms[0]).annotate(m=TruncMonth("paid_at"))
                    .values_list("m", "channel").annotate(t=Sum("amount"))):
        by_month[c][_day(m)] = t
    series = {c: [int(by_month[c].get(m, 0)) for m in ms] for c in names}
    rows = [{"channel": str(names[c]), "ever": ever.get(c, 0), "this_month": this_month.get(c, 0),
             "last_12": sum(series[c])} for c in names]
    return {
        "rows": rows, "ever_total": sum(ever.values()), "month_total": sum(this_month.values()),
        "last_total": sum(r["last_12"] for r in rows), "site_share": _share(ever.get("site", 0), sum(ever.values())),
        "months": [label(m) for m in ms],
        "chart": charts.chart("bar", [label(m) for m in ms],
                              [charts.series(str(names[c]), series[c], str(1 + i)) for i, c in enumerate(names)],
                              stacked=True),
    }


# ---------------------------------------------------------------------------
# Messages and AI against their limits
# ---------------------------------------------------------------------------

CHANNELS = [catalog.EMAIL, catalog.SMS, catalog.WHATSAPP]


def _limits() -> dict:
    return getattr(settings, "MESSAGE_MONTHLY_LIMITS", {}) or {}


def channels(count: int = 6, today: datetime.date | None = None) -> dict:
    today = today or timezone.localdate()
    ms = months(count, today)
    names = dict(catalog.CHANNEL_CHOICES)
    day = today.day
    days_in_month = (ms[-1].replace(day=28) + datetime.timedelta(days=4)).replace(day=1) - datetime.timedelta(days=1)
    out, series = [], []
    for ch in CHANNELS:
        qs = Message.objects.filter(channel=ch, created_at__gte=_aware(ms[0]))
        sent_rows = _keyed(qs.filter(status__in=SENT).annotate(m=TruncMonth("created_at"))
                           .values_list("m").annotate(n=Count("pk")))
        sent = [sent_rows.get(m, 0) for m in ms]
        series.append(charts.series(str(names[ch]), sent, str(1 + len(series))))
        this = qs.filter(created_at__gte=_aware(ms[-1]))
        n_sent = this.filter(status__in=SENT).count()
        failed = this.filter(status=Message.Status.FAILED).count()
        skipped = this.filter(status=Message.Status.SKIPPED).count()
        delivered = this.filter(delivered_at__isnull=False).count()
        cost = this.aggregate(t=Sum("cost"))["t"] or Decimal("0")
        projected = round(n_sent / day * days_in_month.day) if day else n_sent
        limit = int(_limits().get(ch) or 0)
        left_days = None
        if limit and n_sent and n_sent < limit:
            left_days = int((limit - n_sent) / (n_sent / day))
        by_type = Counter(dict(this.filter(status__in=SENT).values_list("type").annotate(n=Count("pk")))).most_common(5)
        by_provider = this.filter(status__in=SENT).values_list("provider").annotate(n=Count("pk")).order_by("-n")
        out.append({
            "channel": ch, "name": str(names[ch]), "sent": n_sent, "failed": failed, "skipped": skipped,
            "failed_share": _share(failed, n_sent + failed), "delivered": delivered,
            "delivered_share": _share(delivered, n_sent) if ch != catalog.EMAIL else None,
            "cost": float(cost), "cost_each": round(float(cost) / n_sent, 2) if n_sent and cost else None,
            "projected": projected, "limit": limit, "limit_used": _share(n_sent, limit) if limit else None,
            "projected_over": bool(limit and projected > limit), "days_left": left_days,
            "by_type": by_type, "by_provider": [(p or "—", n) for p, n in by_provider], "history": sent,
            "tenants": this.filter(tenant__isnull=False, status__in=SENT).values("tenant").distinct().count(),
            "organizations": this.filter(status__in=SENT).values("organization").distinct().count(),
        })
    return {"months": [label(m) for m in ms], "channels": out,
            "chart": charts.chart("bar", [label(m) for m in ms], series, axis="count", stacked=True)}


def message_categories(today: datetime.date | None = None) -> dict:
    """Every kind of message (invoice issued, reminder, receipt...) with how many went out by email, SMS and
    WhatsApp, this month and since the start. Counts only; no recipient is named."""
    today = today or timezone.localdate()
    since = _aware(first_of(today))
    labels = {code: str(name) for code, name in catalog.CHOICES}
    sent = Message.objects.filter(status__in=SENT, channel__in=CHANNELS)
    rows: dict[str, dict] = defaultdict(lambda: {c: {"month": 0, "ever": 0} for c in CHANNELS})
    for kind, ch, n in sent.values_list("type", "channel").annotate(n=Count("pk")):
        rows[kind][ch]["ever"] = n
    for kind, ch, n in sent.filter(created_at__gte=since).values_list("type", "channel").annotate(n=Count("pk")):
        rows[kind][ch]["month"] = n
    out = [{"type": labels.get(kind, kind.replace("_", " ").capitalize()),
            "counts": [rows[kind][c] for c in CHANNELS],
            "month": sum(rows[kind][c]["month"] for c in CHANNELS),
            "ever": sum(rows[kind][c]["ever"] for c in CHANNELS)} for kind in rows]
    out.sort(key=lambda r: (-r["month"], -r["ever"]))
    names = dict(catalog.CHANNEL_CHOICES)
    return {"channels": [str(names[c]) for c in CHANNELS], "rows": out,
            "totals": [{"month": sum(r["counts"][i]["month"] for r in out),
                        "ever": sum(r["counts"][i]["ever"] for r in out)} for i in range(len(CHANNELS))]}


def support_speed(today: datetime.date | None = None) -> dict:
    """How fast the team answers and closes support requests (last 90 days), and what is waiting too long.
    Hours to the first reply only count requests answered from the console."""
    now = timezone.now()
    since = now - datetime.timedelta(days=90)
    recent = SupportRequest.objects.filter(created_at__gte=since)
    replied = [(r - c).total_seconds() / 3600 for c, r in recent.filter(first_reply_at__isnull=False)
               .values_list("created_at", "first_reply_at")]
    closed = [(r - c).total_seconds() / 86400 for c, r in recent.filter(closed_at__isnull=False)
              .values_list("created_at", "closed_at")]
    open_ = SupportRequest.objects.exclude(status=SupportRequest.Status.CLOSED)
    return {
        "received": recent.count(), "closed": len(closed),
        "avg_reply_hours": round(sum(replied) / len(replied), 1) if replied else None,
        "avg_close_days": round(sum(closed) / len(closed), 1) if closed else None,
        "open": open_.count(),
        "unanswered_24h": open_.filter(first_reply_at__isnull=True, created_at__lt=now - datetime.timedelta(hours=24)).count(),
        "older_than_3_days": open_.filter(created_at__lt=now - datetime.timedelta(days=3)).count(),
    }


def ai_usage(count: int = 6, today: datetime.date | None = None) -> dict:
    today = today or timezone.localdate()
    ms = months(count, today)
    qs = Question.objects.filter(created_at__gte=_aware(ms[0]))
    names = dict(Question.Channel.choices)
    per_channel: dict[str, dict] = defaultdict(dict)
    for m, ch, n in qs.annotate(m=TruncMonth("created_at")).values_list("m", "channel").annotate(n=Count("pk")):
        per_channel[ch][_day(m)] = n
    tokens = _keyed(qs.annotate(m=TruncMonth("created_at")).values_list("m").annotate(i=Sum("input_tokens")))
    out_tokens = _keyed(qs.annotate(m=TruncMonth("created_at")).values_list("m").annotate(o=Sum("output_tokens")))
    this = qs.filter(created_at__gte=_aware(ms[-1]))
    answered = this.filter(status=Question.Status.ANSWERED).count()
    n = this.count()
    in_tok = int(tokens.get(ms[-1], 0) or 0)
    out_tok = int(out_tokens.get(ms[-1], 0) or 0)
    spend = sum(int(e.amount) for e in PlatformExpense.objects.filter(category=PlatformExpense.Category.AI)
                if e.applies_in(ms[-1]))
    on = Organization.objects.filter(assistant_enabled=True).count()
    return {
        "this_month": n, "answered": answered, "failed": this.filter(status=Question.Status.FAILED).count(),
        "limited": this.filter(status=Question.Status.LIMITED).count(), "answered_share": _share(answered, n),
        "input_tokens": in_tok, "output_tokens": out_tok,
        "tokens_each": round((in_tok + out_tok) / n) if n else None,
        "organizations": this.values("organization").distinct().count(), "enabled_organizations": on,
        "tenant_assistant_organizations": Organization.objects.filter(tenant_assistant_enabled=True).count(),
        "spend": spend, "cost_each": round(spend / answered, 2) if answered and spend else None,
        "total_ever": Question.objects.count(),
        "chart": charts.chart("bar", [label(m) for m in ms],
                              [charts.series(str(names.get(ch, ch)), [per_channel[ch].get(m, 0) for m in ms], str(1 + i % 5))
                               for i, ch in enumerate(sorted(per_channel))] or
                              [charts.series(_("Questions"), [0] * len(ms), "1")], axis="count", stacked=True),
        "token_chart": charts.chart("line", [label(m) for m in ms],
                                    [charts.series(_("Tokens in"), [int(tokens.get(m, 0) or 0) for m in ms], "1"),
                                     charts.series(_("Tokens out"), [int(out_tokens.get(m, 0) or 0) for m in ms], "2")],
                                    axis="count"),
    }


def costs_by_organization(month: datetime.date) -> dict:
    """What each organization cost us in the month (D-145): SMS (parts, and what the provider charged), photo scans
    and assistant questions (counted, with OpenAI's price worked out from the tokens), and what we billed back for
    SMS. Rows with nothing in the month are left out; most expensive first."""
    start = month.replace(day=1)
    since, until = _aware(start), _aware((start + datetime.timedelta(days=32)).replace(day=1))
    window = {"created_at__gte": since, "created_at__lt": until}
    rows: dict[int, dict] = defaultdict(lambda: {
        "sms_parts": 0, "sms_cost": Decimal("0"), "sms_billed": Decimal("0"), "scans": 0, "scan_cost": Decimal("0"),
        "questions": 0, "tokens_in": 0, "tokens_out": 0, "ai_cost": Decimal("0")})
    for org, parts, billed in (SmsWalletEntry.objects.filter(kind=SmsWalletEntry.Kind.MESSAGE, **window)
                               .values_list("organization").annotate(p=Sum("segments"), b=Sum("amount"))):
        rows[org]["sms_parts"] = parts or 0
        rows[org]["sms_billed"] = -(billed or 0)
    for org, cost in (Message.objects.filter(channel=catalog.SMS, status__in=SENT, **window)
                      .values_list("organization").annotate(c=Sum("cost"))):
        rows[org]["sms_cost"] = cost or Decimal("0")
    for org, n, cost in (Scan.objects.filter(**window).values_list("organization")
                         .annotate(n=Count("pk", filter=Q(status__in=[Scan.Status.READ, Scan.Status.UNCLEAR])),
                                   c=Sum("cost"))):
        rows[org]["scans"], rows[org]["scan_cost"] = n, cost or Decimal("0")
    for org, n, tin, tout in (Question.objects.filter(**window).exclude(status=Question.Status.LIMITED)
                              .values_list("organization")
                              .annotate(n=Count("pk"), i=Sum("input_tokens"), o=Sum("output_tokens"))):
        rows[org].update(questions=n, tokens_in=tin or 0, tokens_out=tout or 0, ai_cost=cost_kes(tin or 0, tout or 0))
    paid = dict(SubscriptionPayment.objects.filter(paid_on__gte=start, paid_on__lt=until.date())
                .values_list("organization").annotate(t=Sum("amount")))
    for pk in paid:
        rows[pk]  # noqa: B018 - a landlord who paid but used nothing still belongs in the list
    names = Organization.objects.in_bulk(list(rows))
    out = []
    for pk, r in rows.items():
        r["org"] = names.get(pk)
        r["total"] = r["sms_cost"] + r["scan_cost"] + r["ai_cost"]
        r["income"] = paid.get(pk) or Decimal("0")
        r["margin"] = r["income"] - r["total"]
        if r["org"] is not None:
            out.append(r)
    out.sort(key=lambda r: (-r["total"], r["org"].name.lower()))
    keys = ("sms_parts", "sms_cost", "sms_billed", "scans", "scan_cost", "questions", "tokens_in", "tokens_out",
            "ai_cost", "total", "income", "margin")
    totals = {k: sum(r[k] for r in out) for k in keys}
    return {"month": start, "rows": out, "totals": totals,
            "prices": {"input": settings.AI_PRICE_INPUT_PER_M, "output": settings.AI_PRICE_OUTPUT_PER_M,
                       "usd": settings.USD_TO_KES}}
