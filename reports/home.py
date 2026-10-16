"""The home page for each member (D-056): what needs doing today, then the figures they may see.

Roles are data (doc 13), so nothing here names a role. Each item appears only when the member
holds the capability of the page it opens, so a link never ends on "not allowed", and each count
is built from that page's own visibility helper, so it covers only the member's properties and
matches what the page then lists.
"""

import datetime
import functools
from dataclasses import dataclass, field

from django.db.models import Prefetch
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import ngettext

from accounts.models import Membership
from accounts.permissions import can
from billing import followups, late_fees
from billing import selectors as billing_selectors
from documents import services as document_services
from expenses import services as expense_services
from inspections.models import ConditionReport
from inspections.services import visible_reports
from leases.models import EXPIRING_WITHIN_DAYS, Lease, LeaseTenant
from leases.services import visible_leases
from maintenance import services as maintenance
from meters import services as meter_services
from mpesa import codes
from mpesa.inbox import visible_transactions
from mpesa.models import MpesaTransaction
from payments.models import Payment
from payments.selectors import pending_review, visible_payments
from properties import selectors as property_selectors
from prospects import services as prospects
from reports import metrics, owners

# Rows listed on the home page before "see all".
SHORT_LIST = 5
TREND_MIN = 6
# How far ahead a move-out under notice becomes a home task.
MOVE_OUT_WITHIN_DAYS = 14

# (icon, urgent) for each task key: urgent ones are drawn in the late colour and listed first (D-153).
TASK_LOOK = {
    "confirm": ("check2-square", False), "match": ("phone", True), "codes": ("upc-scan", False),
    "readings": ("droplet", False), "expenses": ("receipt-cutoff", False), "late_fees": ("percent", False),
    "statements": ("send", False), "call": ("telephone", True), "promised": ("calendar-check", True),
    "repairs_new": ("tools", False), "repairs_mine": ("wrench", False), "repairs_late": ("exclamation-triangle", True),
    "prospects_call": ("person-lines-fill", False), "viewings": ("eye", False), "holds": ("hourglass-bottom", False),
    "drafts": ("file-earmark-text", False), "move_outs": ("box-arrow-right", False),
    "documents": ("folder-x", False), "vacant": ("door-open", False),
}


@dataclass
class Task:
    """One thing waiting for the member: a count, what it means and the page that deals with it."""

    key: str
    count: int
    label: str
    url: str
    detail: str = ""
    icon: str = "dot"
    urgent: bool = False


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
    # D-153: what the financial home adds under the figures, each only with the capability of the page it opens.
    debtors: list[metrics.Debtor] = field(default_factory=list)
    payments: list = field(default_factory=list)
    collection: list[metrics.PropertyCollection] = field(default_factory=list)
    # The 12-month bars from the first month with rent in them (at least six), so a new landlord sees no empty year.
    trend: list[metrics.MonthFigures] = field(default_factory=list)
    # False until anything has been billed or paid: a new account sees its setup steps, not rows of KES 0.00.
    started: bool = False

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


def _late_fees_to_approve(m: Membership) -> Task | None:
    if not can(m, "invoices.generate"):
        return None
    n = late_fees.to_approve(m).count()
    return Task("late_fees", n, ngettext("late fee to approve", "late fees to approve", n),
                reverse("billing:late_fees"))


def _readings_to_approve(m: Membership) -> Task | None:
    if not can(m, "meters.approve"):
        return None
    n = meter_services.to_approve(m).count()
    return Task("readings", n, ngettext("meter reading to approve", "meter readings to approve", n),
                reverse("meters:approvals"))


def _expenses_to_approve(m: Membership) -> Task | None:
    if not can(m, "expenses.approve"):
        return None
    n = expense_services.to_approve(m).count()
    return Task("expenses", n, ngettext("expense to approve", "expenses to approve", n),
                reverse("expenses:list") + "?status=SUBMITTED")


def _maintenance(m: Membership) -> list[Task | None]:
    """New requests to assign, the member's own jobs and jobs past their due time (D-068 item 10)."""
    if not (can(m, "maintenance.view") or can(m, "maintenance.view_assigned")):
        return []
    url = reverse("maintenance:list")
    n_new, n_mine, n_late = (maintenance.to_assign(m).count(), maintenance.my_jobs(m).count(),
                             maintenance.overdue(m).count())
    return [
        Task("repairs_new", n_new, ngettext("repair to assign", "repairs to assign", n_new), url + "?status=NEW"),
        Task("repairs_mine", n_mine, ngettext("repair job assigned to you", "repair jobs assigned to you", n_mine),
             url + "?status=open&mine=1"),
        Task("repairs_late", n_late, ngettext("repair past its due time", "repairs past their due time", n_late),
             url + "?overdue=1"),
    ]


def _documents_expiring(m: Membership, today: datetime.date) -> Task | None:
    """Insurance, permits and the like, expired or expiring within 30 days (D-071 item 6)."""
    if not can(m, "documents.manage"):
        return None
    n = document_services.expiring(m, today).count()
    return Task("documents", n, ngettext("document expired or expiring soon", "documents expired or expiring soon",
                                         n), reverse("documents:list") + "?expiry=expiring")


def _statements_to_send(m: Membership, today: datetime.date) -> Task | None:
    n, month = owners.to_send(m, today)
    if not n:
        return None
    return Task("statements", n, owners.to_send_label(n), reverse("reports:owner_statement"),
                f"{month:%B %Y}")


def _calls(m: Membership, today: datetime.date, arrears_rows) -> list[Task]:
    # Calls are work only for those who record them; a read-only viewer can still open the list.
    if not (can(m, "invoices.view") and can(m, "arrears.follow_up")):
        return []
    board = followups.call_list(m, today, arrears_rows=arrears_rows())
    url = reverse("billing:call_list")
    broken = sum(1 for row in board.to_call if row.promise == followups.BROKEN)
    due = sum(1 for row in board.promised if row.follow_up.promised_on == today)
    detail = ngettext("%(n)d broken promise", "%(n)d broken promises", broken) % {"n": broken} if broken else ""
    return [
        Task("call", len(board.to_call), ngettext("tenant to call", "tenants to call", len(board.to_call)), url,
             detail),
        Task("promised", due, ngettext("promise to pay due today", "promises to pay due today", due), url),
    ]


def _prospects(m: Membership, today: datetime.date) -> list[Task]:
    """People to call, viewings today and holds past their date (D-072 item 8)."""
    if not can(m, "prospects.manage"):
        return []
    url = reverse("prospects:list")
    n_call, n_view, n_lapsed = (prospects.follow_ups_due(m, today).count(), prospects.viewings_on(m, today).count(),
                                prospects.lapsed_holds(m, today).count())
    return [
        Task("prospects_call", n_call, ngettext("prospect to call", "prospects to call", n_call), url + "?due=1"),
        Task("viewings", n_view, ngettext("viewing today", "viewings today", n_view), url),
        Task("holds", n_lapsed, ngettext("unit hold past its date", "unit holds past their date", n_lapsed),
             url + "?lapsed=1"),
    ]


def _draft_leases(m: Membership) -> Task | None:
    if not can(m, "leases.activate"):
        return None
    n = visible_leases(m).filter(status=Lease.Status.DRAFT).count()
    return Task("drafts", n, ngettext("draft lease to check and activate", "draft leases to check and activate", n),
                reverse("leases:list") + "?status=" + Lease.Status.DRAFT)


def _move_outs(m: Membership, today: datetime.date) -> Task | None:
    """Tenants under notice whose move-out day is within two weeks or has passed with the lease still open (D-106)."""
    if not can(m, "leases.terminate"):
        return None
    n = visible_leases(m).filter(status=Lease.Status.ACTIVE,
                                 move_out_on__lte=today + datetime.timedelta(days=MOVE_OUT_WITHIN_DAYS)).count()
    return Task("move_outs", n, ngettext("tenant moving out soon", "tenants moving out soon", n),
                reverse("leases:list") + "?notice=1")


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


def _recent_payments(m: Membership) -> list:
    """The last confirmed payments, newest first, for a member who may open the payments page."""
    if not can(m, "payments.view"):
        return []
    return list(visible_payments(m).filter(status=Payment.Status.CONFIRMED)
                .order_by("-paid_at", "-confirmed_at", "-pk")[:SHORT_LIST])


def home(membership: Membership, today: datetime.date | None = None) -> Home:
    today = today or timezone.localdate()
    out = Home()
    # The call list and the dashboard both need the arrears, the page's slowest read: work it out once.
    arrears_rows = functools.cache(lambda: billing_selectors.arrears(membership, today))
    tasks = [_payments_to_confirm(membership), _mpesa_to_match(membership), _codes_to_check(membership),
             _readings_to_approve(membership), _expenses_to_approve(membership), _late_fees_to_approve(membership),
             _statements_to_send(membership, today), *_calls(membership, today, arrears_rows),
             *_maintenance(membership), *_prospects(membership, today),
             _draft_leases(membership), _move_outs(membership, today), _documents_expiring(membership, today)]
    # Vacancies are work for someone who can let them, and context for everyone else.
    if can(membership, "leases.draft") or can(membership, "prospects.manage"):
        tasks.append(_vacant_units(membership))
    out.tasks = [t for t in tasks if t is not None and t.count]
    for t in out.tasks:
        t.icon, t.urgent = TASK_LOOK.get(t.key, ("dot", False))
        t.urgent = t.urgent or bool(t.detail and t.key == "call")
    out.tasks.sort(key=lambda t: not t.urgent)  # stable: urgent first, the rest in their usual order
    out.expiring, out.expiring_count = _expiring(membership, today)
    out.reports = _reports_to_finish(membership)
    if can(membership, "dashboard.view_financial"):
        out.board = board = metrics.dashboard(membership, today=today, arrears_rows=arrears_rows())
        if can(membership, "invoices.view"):
            out.debtors = metrics.largest_debts(board.scope, today, arrears_rows(), count=SHORT_LIST)
        if len(board.by_property) > 1:
            out.collection = [row for row in metrics.collection_by_property(board.scope, board.month)
                              if row.expected][:SHORT_LIST]
        out.payments = _recent_payments(membership)
        used = [i for i, f in enumerate(board.trend) if f.expected or f.collected]
        out.started = bool(used or board.arrears.total or out.payments or board.occupancy.occupied)
        out.trend = board.trend[min(used[0], len(board.trend) - TREND_MIN):] if used else []
    elif can(membership, "dashboard.view_summary"):
        sc = metrics.scope(membership, capability="dashboard.view_summary")
        out.summary = Summary(
            occupancy=metrics.occupancy(sc, today),
            overdue_leases=billing_selectors.overdue_lease_count(membership, today),
        )
    return out
