"""The daily M-Pesa job, `mpesa_daily` (D-045 item 10).

1. Retries transactions stuck in RECEIVED (processing failed, or the server stopped mid-way).
2. Asks Safaricom about payment requests still waiting after 10 minutes; gives up on ones that
   Safaricom has not answered within a day.
3. Sends yesterday's reconciliation summary per organization, one line per account, in-app to
   organization-wide holders of `mpesa.view_transactions`.
4. Tells `mpesa.match` holders about hand-typed codes Safaricom never confirmed (D-066).

Safe to run more than once a day: retries skip what has been processed, and the summary has a
dedupe key per organization, day and person; so do the code alerts.
"""

import datetime
import logging

from django.core.exceptions import ValidationError
from django.db.models import Count, Q, Sum
from django.utils import timezone

from accounts.models import Membership, Organization
from accounts.permissions import accessible_property_ids, can
from core.money import ZERO, format_money

from . import c2b, codes, stk
from .models import MpesaTransaction, StkRequest

logger = logging.getLogger(__name__)

Status = MpesaTransaction.Status
MatchedBy = MpesaTransaction.MatchedBy
# A callback being processed right now is not a stuck one.
RETRY_AFTER = datetime.timedelta(minutes=5)
MAX_ATTEMPTS = 10
QUERY_AFTER = datetime.timedelta(minutes=10)
GIVE_UP_AFTER = datetime.timedelta(days=1)


def retry_received(now: datetime.datetime) -> dict[str, int]:
    stuck = MpesaTransaction.objects.filter(status=Status.RECEIVED, created_at__lte=now - RETRY_AFTER,
                                            attempts__lt=MAX_ATTEMPTS).order_by("pk")
    counts = {"retried": 0, "retry_matched": 0}
    for tx in stuck:
        c2b.safe_process(tx)
        counts["retried"] += 1
        tx.refresh_from_db(fields=["status"])
        if tx.status == Status.MATCHED:
            counts["retry_matched"] += 1
    return counts


def check_pending_requests(now: datetime.datetime) -> dict[str, int]:
    """Queries requests whose callback has not come. Ones Safaricom never sent on, or never answered
    within a day, are closed as not paid, unless Safaricom said they were paid."""
    counts = {"stk_queried": 0, "stk_failed": 0}
    pending = StkRequest.objects.filter(status=StkRequest.Status.PENDING, created_at__lte=now - QUERY_AFTER)
    for req in pending.select_related("payment_account").order_by("pk"):
        if req.checkout_request_id and req.result_code != "0":
            counts["stk_queried"] += 1
            try:
                req = stk.check_status(req)
            except ValidationError:
                pass  # still processing, or Safaricom could not be asked; next run tries again
            except Exception:
                logger.exception("STK request %s could not be checked", req.pk)
        if req.status == StkRequest.Status.PENDING and req.result_code != "0" and (
                not req.checkout_request_id or req.created_at <= now - GIVE_UP_AFTER):
            closed = StkRequest.objects.filter(pk=req.pk, status=StkRequest.Status.PENDING).update(
                status=StkRequest.Status.FAILED, completed_at=now, updated_at=now,
                result_desc="No answer from Safaricom.")
            req.status = StkRequest.Status.FAILED if closed else req.status
        if req.status == StkRequest.Status.FAILED:
            counts["stk_failed"] += 1
    return counts


# ---------------------------------------------------------------------------
# Reconciliation summary
# ---------------------------------------------------------------------------


def summarize(org: Organization, day: datetime.date) -> list[dict]:
    """Per account, the payments made on `day` (Nairobi time) and what became of them so far."""
    start = datetime.datetime.combine(day, datetime.time.min, tzinfo=c2b.NAIROBI)
    txs = MpesaTransaction.objects.filter(organization=org, paid_at__gte=start,
                                          paid_at__lt=start + datetime.timedelta(days=1))
    auto = Q(status=Status.MATCHED, matched_by__in=[MatchedBy.REFERENCE, MatchedBy.STK])
    rows = (txs.values("payment_account_id", "payment_account__display_name")
            .annotate(count=Count("pk"), total=Sum("amount"),
                      auto=Count("pk", filter=auto),
                      manual=Count("pk", filter=Q(status=Status.MATCHED, matched_by=MatchedBy.MANUAL)),
                      ignored=Count("pk", filter=Q(status=Status.IGNORED)),
                      waiting=Count("pk", filter=Q(status__in=[Status.RECEIVED, Status.UNMATCHED,
                                                               Status.FLAGGED])))
            .order_by("payment_account__display_name"))
    return [{"account": r["payment_account__display_name"], "count": r["count"], "total": r["total"] or ZERO,
             "auto": r["auto"], "manual": r["manual"], "ignored": r["ignored"], "waiting": r["waiting"]}
            for r in rows]


def summary_text(org: Organization, rows: list[dict]) -> str:
    return " ".join(
        f"{r['account']}: {r['count']} received, {format_money(r['total'], org.currency)}; "
        f"{r['auto']} matched automatically, {r['manual']} by hand, {r['ignored']} ignored, "
        f"{r['waiting']} still waiting." for r in rows)


def send_summaries(day: datetime.date) -> int:
    from notifications.delivery import notify

    sent = 0
    for org in Organization.objects.filter(pk__in=MpesaTransaction.objects.values("organization_id")):
        rows = summarize(org, day)
        if not rows:
            continue
        context = {"day": f"{day.day} {day:%b %Y}",
                   "summary": summary_text(org, rows)}
        members = (Membership.objects.filter(organization=org, is_active=True, user__is_active=True)
                   .select_related("user", "organization", "role"))
        for m in members:
            if not can(m, "mpesa.view_transactions") or accessible_property_ids(m) is not None:
                continue
            if notify(org, "mpesa_daily_summary", user=m.user, context=context,
                      dedupe_key=f"mpesa_summary:{day.isoformat()}:{m.user.pk}"):
                sent += 1
    return sent


def run_daily(now: datetime.datetime | None = None) -> dict[str, int]:
    now = now or timezone.now()
    counts = retry_received(now)
    counts.update(check_pending_requests(now))
    yesterday = now.astimezone(c2b.NAIROBI).date() - datetime.timedelta(days=1)
    counts["summaries_sent"] = send_summaries(yesterday)
    counts["code_alerts_sent"] = codes.send_alerts(now)
    return counts
