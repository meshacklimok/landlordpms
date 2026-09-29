"""The home page for each member (D-056): what needs doing today, then the figures they may see.

Roles are data (doc 13), so nothing here names a role. Each item appears only when the member
holds the capability of the page it opens, so a link never ends on "not allowed", and each count
is built from that page's own visibility helper, so it covers only the member's properties and
matches what the page then lists.
"""

import datetime
from dataclasses import dataclass, field

from django.db.models import Prefetch
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import ngettext

from accounts.models import Membership
from accounts.permissions import can
from billing import followups
from billing import selectors as billing_selectors
from inspections.models import ConditionReport
from inspections.services import visible_reports
from leases.models import EXPIRING_WITHIN_DAYS, Lease, LeaseTenant
from leases.services import visible_leases
from meters import services as meter_services
from mpesa import codes
from mpesa.inbox import visible_transactions
from mpesa.models import MpesaTransaction
from payments.selectors import pending_review
from properties import selectors as property_selectors
from reports import metrics, owners

# Rows listed on the home page before "see all".
SHORT_LIST = 5


@dataclass
class Task:
    """One thing waiting for the member: a count, what it means and the page that deals with it."""

    key: str
    count: int
    label: str
    url: str
    detail: str = ""


@dataclass
class Summary:
    """Figures for members without financial figures: counts only, no amounts (doc 10)."""

    occupancy: metrics.Occupancy
    overdue_leases: int | None


@dataclass
class Home:
    tasks: list[Task] = field(default_factory=list)
    expiring: list[Lease] = field(default_factory=list)
    expiring_count: int = 0
    reports: list[ConditionReport] = field(default_factory=list)
    summary: Summary | None = None
    board: metrics.Dashboard | None = None

    @property
    def has_work(self) -> bool:
        return bool(self.tasks or self.expiring or self.reports)


def _payments_to_confirm(m: Membership) -> Task | None:
    if not can(m, "payments.confirm"):
        return None
    n = pending_review(m).count()
    return Task("confirm", n, ngettext("payment to confirm", "payments to confirm", n), reverse("payments:review"))


def _mpesa_to_match(m: Membership) -> Task | None:
    if not can(m, "mpesa.match"):
        return None
    n = (visible_transactions(m, "mpesa.match").filter(status=MpesaTransaction.Status.UNMATCHED)
         .order_by().values("pk").distinct().count())
    return Task("match", n, ngettext("M-Pesa payment to match", "M-Pesa payments to match", n), reverse("mpesa:inbox"))


def _codes_to_check(m: Membership) -> Task | None:
    if not can(m, "mpesa.match"):
        return None
    n = len(codes.review(codes.for_member(m)).flagged)
    return Task("codes", n, ngettext("M-Pesa code to check", "M-Pesa codes to check", n), reverse("mpesa:codes"))


def _readings_to_approve(m: Membership) -> Task | None:
    if not can(m, "meters.approve"):
        return None
    n = meter_services.to_approve(m).count()
    return Task("readings", n, ngettext("water reading to approve", "water readings to approve", n),
                reverse("meters:approvals"))


def _statements_to_send(m: Membership, today: datetime.date) -> Task | None:
    n, month = owners.to_send(m, today)
    if not n:
        return None
    return Task("statements", n, owners.to_send_label(n), reverse("reports:owner_statement"),
                f"{month:%B %Y}")


def _calls(m: Membership, today: datetime.date) -> list[Task]:
    # Calls are work only for those who record them; a read-only viewer can still open the list.
    if not (can(m, "invoices.view") and can(m, "arrears.follow_up")):
        return []
    board = followups.call_list(m, today)
    url = reverse("billing:call_list")
    broken = sum(1 for row in board.to_call if row.promise == followups.BROKEN)
    due = sum(1 for row in board.promised if row.follow_up.promised_on == today)
    detail = ngettext("%(n)d broken promise", "%(n)d broken promises", broken) % {"n": broken} if broken else ""
    return [
        Task("call", len(board.to_call), ngettext("tenant to call", "tenants to call", len(board.to_call)), url,
             detail),
        Task("promised", due, ngettext("promise to pay due today", "promises to pay due today", due), url),
    ]


def _draft_leases(m: Membership) -> Task | None:
    if not can(m, "leases.activate"):
        return None
    n = visible_leases(m).filter(status=Lease.Status.DRAFT).count()
    return Task("drafts", n, ngettext("draft lease to check and activate", "draft leases to check and activate", n),
                reverse("leases:list") + "?status=" + Lease.Status.DRAFT)


def _vacant_units(m: Membership) -> Task | None:
    if not (can(m, "units.view") and can(m, "units.list_vacant")):
        return None
    units = property_selectors.filter_units(property_selectors.visible_units(m), status="vacant")
    n = units.count()
    return Task("vacant", n, ngettext("unit available to let", "units available to let", n),
                reverse("properties:units") + "?status=vacant")


def _expiring(m: Membership, today: datetime.date) -> tuple[list[Lease], int]:
    """Active leases whose contract ends within the window (`Lease.is_expiring`), soonest first."""
    if not can(m, "leases.view"):
        return [], 0
    qs = visible_leases(m).filter(status=Lease.Status.ACTIVE, end_date__gte=today,
                                  end_date__lte=today + datetime.timedelta(days=EXPIRING_WITHIN_DAYS))
    rows = list(qs.select_related("unit__property").prefetch_related(
        Prefetch("lease_tenants", LeaseTenant.objects.select_related("tenant")))
        .order_by("end_date", "pk")[:SHORT_LIST])
    return rows, qs.count()


def _reports_to_finish(m: Membership) -> list[ConditionReport]:
    """Condition reports started and not completed, which only a recorder can finish."""
    if not can(m, "inspections.record"):
        return []
    return list(visible_reports(m).filter(status=ConditionReport.Status.DRAFT)
                .select_related("unit__property").order_by("inspected_on", "pk")[:SHORT_LIST])


def home(membership: Membership, today: datetime.date | None = None) -> Home:
    today = today or timezone.localdate()
    out = Home()
    tasks = [_payments_to_confirm(membership), _mpesa_to_match(membership), _codes_to_check(membership),
             _readings_to_approve(membership),
             _statements_to_send(membership, today), *_calls(membership, today),
             _draft_leases(membership)]
    # Vacancies are work for someone who can let them, and context for everyone else.
    if can(membership, "leases.draft") or can(membership, "prospects.manage"):
        tasks.append(_vacant_units(membership))
    out.tasks = [t for t in tasks if t is not None and t.count]
    out.expiring, out.expiring_count = _expiring(membership, today)
    out.reports = _reports_to_finish(membership)
    if can(membership, "dashboard.view_financial"):
        out.board = metrics.dashboard(membership, today=today)
    elif can(membership, "dashboard.view_summary"):
        sc = metrics.scope(membership, capability="dashboard.view_summary")
        out.summary = Summary(
            occupancy=metrics.occupancy(sc, today),
            overdue_leases=billing_selectors.overdue_lease_count(membership, today),
        )
    return out
