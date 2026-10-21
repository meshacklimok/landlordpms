"""Figures for the M-Pesa pages: the tab badges, the money summary and whether each account is receiving."""
import datetime
from decimal import Decimal

from django.db.models import Count, Max, Q, Sum
from django.utils import timezone
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy

from accounts.permissions import can

from . import codes, inbox
from .models import MpesaTransaction

Status = MpesaTransaction.Status

STALE_AFTER = datetime.timedelta(days=14)


def nav_counts(membership) -> dict:
    """The badges on the M-Pesa tabs."""
    if not can(membership, "mpesa.match"):
        return {"inbox_count": 0, "codes_count": 0}
    return {
        "inbox_count": inbox.inbox(membership).count(),
        "codes_count": len(codes.review(codes.for_member(membership)).flagged),
    }


def summary(qs) -> dict:
    rows = {r["status"]: r for r in qs.order_by().values("status").annotate(n=Count("pk"), total=Sum("amount"))}

    def of(*statuses):
        picked = [rows[s] for s in statuses if s in rows]
        return (sum(r["n"] for r in picked), sum((r["total"] or Decimal(0) for r in picked), Decimal(0)))

    count, total = of(*Status.values)
    placed_n, placed = of(Status.MATCHED)
    waiting_n, waiting = of(Status.UNMATCHED, Status.FLAGGED, Status.RECEIVED)
    ignored_n, ignored = of(Status.IGNORED)
    return {"count": count, "total": total, "placed_n": placed_n, "placed": placed,
            "waiting_n": waiting_n, "waiting": waiting, "ignored_n": ignored_n, "ignored": ignored,
            "placed_pct": round(placed / total * 100) if total else None}


def last_received(org) -> dict:
    """{payment account id: when money last came in}"""
    return dict(MpesaTransaction.objects.filter(organization=org).order_by().values("payment_account_id")
                .annotate(last=Max("paid_at")).values_list("payment_account_id", "last"))


def is_quiet(last, registered_at) -> bool:
    """True when an account that is connected has not received anything for a while."""
    since = last or registered_at
    return since is not None and timezone.now() - since > STALE_AFTER


def add_months(day: datetime.date, n: int) -> datetime.date:
    index = day.year * 12 + day.month - 1 + n
    return datetime.date(index // 12, index % 12 + 1, 1)


def _share(part, whole):
    if whole:
        return Decimal(part) / Decimal(whole)
    return None


def _median(values):
    values = sorted(values)
    if not values:
        return None
    middle = len(values) // 2
    if len(values) % 2:
        return values[middle]
    return (values[middle - 1] + values[middle]) / 2


def _waited(delta) -> str:
    if delta is None:
        return "–"

    def trim(x):
        if x >= 10:
            return f"{x:.0f}"
        return f"{x:.1f}".removesuffix(".0")

    minutes = delta.total_seconds() / 60
    if minutes < 60:
        return _("%(n)d min") % {"n": max(1, round(minutes))}
    if minutes < 2880:
        return _("%(n)s h") % {"n": trim(minutes / 60)}
    return _("%(n)s days") % {"n": trim(minutes / 1440)}


PLACEMENT = (
    ("reference", gettext_lazy("By account number"), "1"),
    ("stk", gettext_lazy("From a payment request"), "2"),
    ("manual", gettext_lazy("Placed by hand"), "3"),
    ("waiting", gettext_lazy("Still waiting"), "late-2"),
)
CHANNELS = (
    (MpesaTransaction.Source.C2B, "1"),
    (MpesaTransaction.Source.STK, "2"),
    (MpesaTransaction.Source.STATEMENT, "4"),
)
PATTERN_DAYS = 90
HAND_MATCH_DAYS = 90


def _placement(status, matched_by) -> str:
    if status != Status.MATCHED:
        return "waiting"
    return {MpesaTransaction.MatchedBy.MANUAL: "manual",
            MpesaTransaction.MatchedBy.STK: "stk"}.get(matched_by, "reference")


def dashboard(membership, org) -> dict:
    from django.db.models.functions import ExtractDay, TruncDate, TruncMonth

    from reports.metrics import Change

    visible = inbox.visible_transactions(membership).exclude(status=Status.IGNORED)
    qs = MpesaTransaction.objects.filter(pk__in=visible.values("pk")).order_by()
    today = timezone.localdate()
    month = today.replace(day=1)
    last_month = add_months(month, -1)
    # The same days of last month, so a part-way month is compared like with like
    same_days_end = min(last_month + datetime.timedelta(days=today.day), month)

    def figures(since, until):
        agg = qs.filter(paid_at__date__gte=since, paid_at__date__lt=until).aggregate(
            n=Count("pk"), total=Sum("amount"),
            payers=Count("msisdn_hash", distinct=True, filter=~Q(msisdn_hash="")))
        return agg["n"], agg["total"] or Decimal(0), agg["payers"]

    tomorrow = today + datetime.timedelta(days=1)
    n_now, now, payers = figures(month, tomorrow)
    n_before, before, _payers_before = figures(last_month, same_days_end)
    _n_last, last_month_total, _p = figures(last_month, month)
    last_same_day = (same_days_end - datetime.timedelta(days=1)).day
    if last_same_day > 1:
        same_days_label = f"1–{last_same_day} {last_month:%b}"
    else:
        same_days_label = f"1 {last_month:%b}"

    this_month = qs.filter(paid_at__date__gte=month, paid_at__date__lt=tomorrow)
    amounts = list(this_month.values_list("amount", flat=True))

    placed = {key: {"key": key, "label": label, "color": color, "n": 0, "total": Decimal(0)}
              for key, label, color in PLACEMENT}
    for row in this_month.values("status", "matched_by").annotate(n=Count("pk"), total=Sum("amount")):
        slot = placed[_placement(row["status"], row["matched_by"])]
        slot["n"] += row["n"]
        slot["total"] += row["total"] or Decimal(0)
    placed_n = sum(placed[k]["n"] for k in ("reference", "stk", "manual"))
    by_itself_n = placed["reference"]["n"] + placed["stk"]["n"]
    for slot in placed.values():
        slot["share"] = _share(slot["total"], now)

    by_source = dict(this_month.values("source").annotate(total=Sum("amount")).values_list("source", "total"))
    by_source_n = dict(this_month.values("source").annotate(n=Count("pk")).values_list("source", "n"))
    channels = [{"label": source.label, "color": color, "total": by_source.get(source.value) or Decimal(0),
                 "n": by_source_n.get(source.value, 0),
                 "share": _share(by_source.get(source.value) or 0, now)}
                for source, color in CHANNELS]

    waits = [matched - paid for paid, matched in qs.filter(
        matched_by=MpesaTransaction.MatchedBy.MANUAL, matched_at__isnull=False,
        matched_at__gte=timezone.now() - datetime.timedelta(days=HAND_MATCH_DAYS),
    ).values_list("paid_at", "matched_at") if matched >= paid]
    hand_wait = _median(waits)

    first = add_months(month, -11)
    monthly = {}
    for row in (qs.filter(paid_at__date__gte=first).annotate(m=TruncMonth("paid_at")).values("m")
                .annotate(n=Count("pk"), total=Sum("amount"),
                          placed=Count("pk", filter=Q(status=Status.MATCHED)),
                          auto=Count("pk", filter=Q(status=Status.MATCHED)
                                     & ~Q(matched_by=MpesaTransaction.MatchedBy.MANUAL)))):
        key = row["m"].date() if isinstance(row["m"], datetime.datetime) else row["m"]
        monthly[key] = row
    months = []
    for i in range(12):
        start = add_months(first, i)
        row = monthly.get(start, {})
        months.append({"month": start, "n": row.get("n", 0), "total": row.get("total") or Decimal(0),
                       "auto": _share(row.get("auto", 0), row.get("placed", 0))})

    start = today - datetime.timedelta(days=29)
    per_day = dict(qs.filter(paid_at__date__gte=start - datetime.timedelta(days=6))
                   .annotate(d=TruncDate("paid_at")).values("d")
                   .annotate(total=Sum("amount")).values_list("d", "total"))
    days = [start + datetime.timedelta(days=i) for i in range(30)]

    def average(day):
        week = [per_day.get(day - datetime.timedelta(days=k)) or Decimal(0) for k in range(7)]
        return sum(week, Decimal(0)) / 7

    pattern = dict(qs.filter(paid_at__date__gte=today - datetime.timedelta(days=PATTERN_DAYS - 1))
                   .annotate(d=ExtractDay("paid_at")).values("d")
                   .annotate(total=Sum("amount")).values_list("d", "total"))
    month_days = [pattern.get(d) or Decimal(0) for d in range(1, 32)]
    pattern_total = sum(month_days, Decimal(0))
    by_day = {}
    running = Decimal(0)
    for d, amount in enumerate(month_days, start=1):
        running += amount
        for mark in (Decimal("0.5"), Decimal("0.9")):
            if not pattern_total:
                continue
            if mark not in by_day and running >= pattern_total * mark:
                by_day[mark] = d

    waiting = inbox.inbox(membership) if can(membership, "mpesa.match") else MpesaTransaction.objects.none()
    oldest = waiting.order_by("paid_at").values_list("paid_at", flat=True).first()

    from . import services

    last = last_received(org)
    per_account = {row["payment_account_id"]: row for row in
                   this_month.values("payment_account_id").annotate(n=Count("pk"), total=Sum("amount"))}
    accounts = []
    for account in services.mpesa_accounts(org):
        creds = getattr(account, "daraja", None)
        at = last.get(account.pk)
        row = per_account.get(account.pk, {})
        accounts.append({
            "account": account, "creds": creds, "last": at,
            "month_n": row.get("n", 0),
            "month_total": row.get("total") or Decimal(0),
            "share": _share(row.get("total") or 0, now),
            "quiet": bool(creds and creds.urls_registered_at and is_quiet(at, creds.urls_registered_at)),
        })

    return {
        "month": now, "month_n": n_now, "month_start": month, "today": today,
        "before": before, "before_n": n_before, "same_days_label": same_days_label,
        "last_month": last_month, "last_month_total": last_month_total,
        "change": Change(now, before, same_days_label), "change_n": Change(n_now, n_before, same_days_label),
        "payers": payers, "median": _median(amounts),
        "average": now / n_now if n_now else None,
        "largest": max(amounts) if amounts else None,
        "placed_n": placed_n, "by_itself_n": by_itself_n,
        "by_itself": _share(by_itself_n, placed_n),
        "by_itself_pct": round(by_itself_n / placed_n * 100) if placed_n else None,
        "placement": list(placed.values()), "channels": channels,
        "hand_wait": _waited(hand_wait), "hand_n": len(waits),
        "months": months, "days": days,
        "per_day": [per_day.get(d) or Decimal(0) for d in days],
        "per_day_avg": [average(d) for d in days],
        "month_days": month_days, "pattern_total": pattern_total,
        "half_by": by_day.get(Decimal("0.5")), "most_by": by_day.get(Decimal("0.9")),
        "waiting_n": waiting.count(),
        "waiting_total": waiting.aggregate(t=Sum("amount"))["t"] or Decimal(0),
        "oldest_days": (today - timezone.localdate(oldest)).days if oldest else None,
        "accounts": accounts,
        "not_connected": [a for a in accounts if not (a["creds"] and a["creds"].urls_registered_at)],
        "quiet": [a for a in accounts if a["quiet"]],
    }
