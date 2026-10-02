"""Calls about money owed, promises to pay and the daily who-to-call list (D-052).

Only a lease's latest follow-up counts. A promise is pending until its date, kept once confirmed
payments made from the day it was given up to that date reach the amount (any payment when no
amount was named), and broken after that.
"""

import datetime
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from accounts.models import Membership
from accounts.permissions import can, require
from audit import services as audit
from core.money import ZERO
from leases.models import Lease
from payments.models import Payment
from reports import metrics

from . import selectors
from .models import FollowUp

PENDING, KEPT, BROKEN = "pending", "kept", "broken"


@transaction.atomic
def record_follow_up(actor: Membership, lease: Lease, *, outcome: str, note: str = "",
                     promised_on: datetime.date | None = None, promised_amount: Decimal | None = None,
                     today: datetime.date | None = None, request=None) -> FollowUp:
    require(actor, "arrears.follow_up", property=lease.unit.property)
    if lease.organization_id != actor.organization_id:
        raise ValidationError(_("That lease is not in your organization."))
    today = today or timezone.localdate()
    if outcome not in FollowUp.Outcome.values:
        raise ValidationError({"outcome": _("Choose what happened.")})
    if outcome == FollowUp.Outcome.PROMISED:
        if promised_on is None:
            raise ValidationError({"promised_on": _("Enter the date they will pay by.")})
        if promised_on < today:
            raise ValidationError({"promised_on": _("The date cannot be in the past.")})
        if promised_amount is not None and promised_amount <= 0:
            raise ValidationError({"promised_amount": _("Enter an amount above zero, or leave it empty.")})
    else:
        promised_on = promised_amount = None
    follow_up = FollowUp.objects.create(
        organization=actor.organization, lease=lease, outcome=outcome, note=note.strip()[:300],
        promised_on=promised_on, promised_amount=promised_amount, created_by=actor.user)
    audit.record("arrears.follow_up", actor=actor.user, organization=actor.organization, obj=lease, request=request,
                 changes={"outcome": [None, outcome],
                          "promised_on": [None, promised_on.isoformat() if promised_on else None],
                          "promised_amount": [None, str(promised_amount) if promised_amount else None]})
    return follow_up


def latest_follow_ups(lease_ids) -> dict[int, FollowUp]:
    latest = {}
    for f in FollowUp.objects.filter(lease_id__in=list(lease_ids)).select_related("created_by").order_by(
            "lease_id", "-created_at", "-pk"):
        latest.setdefault(f.lease_id, f)
    return latest


def promise_state(follow_up: FollowUp, paid: list[tuple[datetime.date, Decimal]], today: datetime.date) -> str | None:
    """`paid` is the lease's confirmed payments as (date, amount)."""
    if follow_up.outcome != FollowUp.Outcome.PROMISED:
        return None
    start = timezone.localdate(follow_up.created_at)
    total = sum((amount for day, amount in paid if start <= day <= follow_up.promised_on), ZERO)
    target = follow_up.promised_amount
    if (total >= target) if target else total > 0:
        return KEPT
    return PENDING if today <= follow_up.promised_on else BROKEN


def _payments_since(follow_ups: dict[int, FollowUp]) -> dict[int, list[tuple[datetime.date, Decimal]]]:
    promises = [f for f in follow_ups.values() if f.outcome == FollowUp.Outcome.PROMISED]
    if not promises:
        return {}
    since = min(timezone.localdate(f.created_at) for f in promises)
    out = defaultdict(list)
    for lease_id, paid_at, amount in Payment.objects.filter(
            lease_id__in=[f.lease_id for f in promises], status=Payment.Status.CONFIRMED,
            paid_at__gte=since).values_list("lease_id", "paid_at", "amount"):
        out[lease_id].append((paid_at, amount))
    return out


@dataclass
class CallRow:
    arrears: selectors.LeaseArrears
    grade: metrics.Grade
    overdue: Decimal
    days: int = 0
    follow_up: FollowUp | None = None
    promise: str | None = None

    @property
    def lease(self) -> Lease:
        return self.arrears.lease


@dataclass
class CallList:
    to_call: list[CallRow] = field(default_factory=list)
    promised: list[CallRow] = field(default_factory=list)
    done_today: list[CallRow] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.to_call) + len(self.promised) + len(self.done_today)


def call_list(membership: Membership, today: datetime.date, *, property=None, grade: str = "") -> CallList:
    """Everyone with money overdue, sorted into who to call, who promised and who was reached today."""
    out = CallList()
    if not can(membership, "invoices.view"):
        return out
    rows = selectors.arrears(membership, today)
    if property is not None:
        rows = [r for r in rows if r.lease.unit.property_id == property.pk]
    if not rows:
        return out
    grades = metrics.grades([r.lease for r in rows], today)
    follow_ups = latest_follow_ups(r.lease.pk for r in rows)
    paid = _payments_since(follow_ups)
    for r in rows:
        g = grades[r.lease.pk]
        if grade and (g.grade or "new") != grade:
            continue
        f = follow_ups.get(r.lease.pk)
        row = CallRow(arrears=r, grade=g, overdue=r.balance - r.buckets["current"],
                      days=r.days_overdue(today), follow_up=f,
                      promise=promise_state(f, paid.get(r.lease.pk, []), today) if f else None)
        if row.promise == PENDING:
            out.promised.append(row)
        elif f and timezone.localdate(f.created_at) == today:
            out.done_today.append(row)
        else:
            out.to_call.append(row)
    out.to_call.sort(key=lambda row: (row.promise != BROKEN, -row.overdue))
    out.promised.sort(key=lambda row: (row.follow_up.promised_on, -row.overdue))
    out.done_today.sort(key=lambda row: row.follow_up.created_at, reverse=True)
    return out
