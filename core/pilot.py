"""How each organization is doing against the pilot's success measures in doc 02 (D-101).

Counts only: no tenant names, phones or amounts per person leave this module.
"""

import datetime
from dataclasses import dataclass

from django.db.models import Count, Min, Q, Sum
from django.utils import timezone

from accounts.models import Membership, Organization
from banking.models import BankTransaction
from billing.models import Invoice
from leases.models import Lease
from mpesa.models import MpesaTransaction
from payments.models import Payment
from support.models import AppOpen

SETUP_MINUTES = 15  # "5 pilot landlords onboard in under 15 minutes each"
AUTO_SHARE = 90  # "90%+ of payments reconciled without manual work"
ACTIVE_DAYS = 30


@dataclass
class Score:
    organization: str
    setup_minutes: int | None  # from sign-up to the first active lease
    invoices: int  # issued for the month (not void)
    payments: int  # confirmed, paid in the month
    collected: int  # shillings
    auto: int  # M-Pesa and bank money in, matched on its own
    by_hand: int  # matched by someone
    waiting: int  # still unmatched or flagged
    staff_active: int
    staff: int
    app_opens: int = 0  # staff opening the installed app in the month (D-102)
    app_android: int = 0
    app_ios: int = 0

    @property
    def auto_share(self) -> int | None:
        total = self.auto + self.by_hand + self.waiting
        return round(100 * self.auto / total) if total else None

    @property
    def misses(self) -> list[str]:
        """The doc 02 measures this organization does not meet yet."""
        out = []
        if self.setup_minutes is None or self.setup_minutes > SETUP_MINUTES:
            out.append("set-up")
        if not (self.invoices and self.payments):
            out.append("rent cycle")
        if self.auto_share is None or self.auto_share < AUTO_SHARE:
            out.append("matching")
        return out


def month_bounds(month: datetime.date) -> tuple[datetime.date, datetime.date]:
    start = month.replace(day=1)
    return start, (start + datetime.timedelta(days=32)).replace(day=1)


def _aware(day: datetime.date) -> datetime.datetime:
    return timezone.make_aware(datetime.datetime.combine(day, datetime.time.min))


def scores(month: datetime.date, *, organizations=None, now=None) -> list[Score]:
    now = now or timezone.now()
    start, end = month_bounds(month)
    received = Q(paid_at__gte=_aware(start), paid_at__lt=_aware(end))
    orgs = organizations if organizations is not None else Organization.objects.all()
    out = []
    for org in orgs.order_by("name"):
        first = Lease.all_objects.filter(organization=org, activated_at__isnull=False).aggregate(
            at=Min("activated_at"))["at"]
        paid = Payment.objects.filter(organization=org, status=Payment.Status.CONFIRMED, paid_at__gte=start,
                                      paid_at__lt=end).aggregate(n=Count("pk"), total=Sum("amount"))
        mpesa = MpesaTransaction.objects.filter(received, organization=org).aggregate(
            auto=Count("pk", filter=Q(status="MATCHED") & ~Q(matched_by="MANUAL")),
            by_hand=Count("pk", filter=Q(status="MATCHED", matched_by="MANUAL")),
            waiting=Count("pk", filter=Q(status__in=["UNMATCHED", "FLAGGED", "RECEIVED"])))
        bank = BankTransaction.objects.filter(organization=org, posted_on__gte=start, posted_on__lt=end).aggregate(
            auto=Count("pk", filter=Q(status="MATCHED") & ~Q(matched_by="MANUAL")),
            by_hand=Count("pk", filter=Q(status="MATCHED", matched_by="MANUAL")),
            waiting=Count("pk", filter=Q(status="UNMATCHED")))
        staff = Membership.objects.filter(organization=org, is_active=True)
        opens = AppOpen.objects.filter(organization=org, audience=AppOpen.Audience.STAFF, day__gte=start, day__lt=end)
        opens = dict(opens.values_list("platform").annotate(n=Sum("count")))
        out.append(Score(
            organization=org.name,
            setup_minutes=int((first - org.created_at).total_seconds() // 60) if first else None,
            invoices=Invoice.objects.filter(organization=org, period_start__gte=start, period_start__lt=end)
            .exclude(status__in=[Invoice.Status.VOID, Invoice.Status.DRAFT]).count(),
            payments=paid["n"], collected=int(paid["total"] or 0),
            auto=mpesa["auto"] + bank["auto"], by_hand=mpesa["by_hand"] + bank["by_hand"],
            waiting=mpesa["waiting"] + bank["waiting"],
            staff_active=staff.filter(user__last_login__gte=now - datetime.timedelta(days=ACTIVE_DAYS)).count(),
            staff=staff.count(), app_opens=sum(opens.values()), app_android=opens.get(AppOpen.Platform.ANDROID, 0),
            app_ios=opens.get(AppOpen.Platform.IOS, 0)))
    return out


def opens_outside_organizations(month: datetime.date) -> dict[str, dict[str, int]]:
    """Installed-app opens by tenants and signed-out visitors in the month: {audience: {platform: count}}."""
    start, end = month_bounds(month)
    rows = (AppOpen.objects.filter(organization=None, day__gte=start, day__lt=end)
            .values_list("audience", "platform").annotate(n=Sum("count")))
    out: dict[str, dict[str, int]] = {}
    for audience, platform, n in rows:
        out.setdefault(audience, {})[platform] = n
    return out
