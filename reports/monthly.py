"""The monthly property report, emailed on the 1st (D-194).

Written for the month that has just ended, from the same figures the dashboard and the profit and loss show.
Three parts are kept apart so a landlord can tell them apart: what the records show (facts), what stands out
(comparisons against the month before) and what to do (suggestions, not facts). Nothing here is estimated except
the empty units' rent, which is labelled as the asking rent.
"""

import datetime
from dataclasses import dataclass, field
from decimal import Decimal

from django.conf import settings
from django.core import signing
from django.db import IntegrityError, transaction
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _
from django.utils.translation import ngettext

from accounts.models import Membership
from accounts.permissions import can
from core.money import format_money
from leases.models import Lease
from leases.services import visible_leases
from maintenance.models import MaintenanceRequest
from notifications.email_html import send_branded
from notifications.providers.mail import MESSAGES, mailer_for

from . import finance, metrics
from .models import MonthlyReportSent

SALT = "reports.monthly-stop"
ENDING_DAYS = 60
EXPENSE_JUMP = Decimal("0.20")  # a rise of a fifth on the month before is worth a sentence


@dataclass
class Report:
    month: datetime.date
    currency: str
    facts: list[str] = field(default_factory=list)
    observations: list[str] = field(default_factory=list)
    actions: list[str] = field(default_factory=list)


def _pct(v) -> str:
    return f"{v * 100:.0f}%"


def build(membership: Membership, month: datetime.date, today: datetime.date) -> Report:
    """The report for `month` as `membership` may see it. Needs `reports.view_financial`."""
    m = membership
    cur = m.organization.currency
    board = metrics.dashboard(m, month=month, today=today, analysis=True)
    c, prev = board.current, board.previous
    r = Report(month=board.month, currency=cur)
    last = metrics.add_months(board.month, -1)
    money = finance.money_report(m, board.month, board.month).total
    before = finance.money_report(m, last, last).total

    r.facts.append(_("Rent billed: %(a)s") % {"a": format_money(c.expected, cur)})
    r.facts.append(_("Rent collected: %(a)s (%(p)s of rent billed)") % {
        "a": format_money(c.collected, cur), "p": _pct(c.rate) if c.rate is not None else "—"})
    r.facts.append(_("Cash received in the month: %(a)s") % {"a": format_money(c.cash, cur)})
    r.facts.append(_("Overdue today: %(a)s on %(n)s") % {
        "a": format_money(board.arrears.total, cur),
        "n": ngettext("%(n)s lease", "%(n)s leases", board.arrears.leases) % {"n": board.arrears.leases}})
    r.facts.append(_("Expenses: %(a)s") % {"a": format_money(money.expenses, cur)})
    r.facts.append(_("Net operating income (cash basis): %(a)s") % {"a": format_money(money.noi, cur)})
    occ = board.occupancy
    r.facts.append(_("Occupied units: %(o)s of %(t)s; vacant: %(v)s") % {
        "o": occ.occupied, "t": occ.rentable, "v": occ.vacant})

    if prev is not None and c.rate is not None and prev.rate is not None:
        d = c.rate - prev.rate
        if abs(d) >= Decimal("0.02"):
            r.observations.append((_("Collection improved from %(a)s to %(b)s in %(m)s.") if d > 0 else
                                   _("Collection fell from %(a)s to %(b)s in %(m)s.")) % {
                "a": _pct(prev.rate), "b": _pct(c.rate), "m": c.label})
    ratio = (money.expenses - before.expenses) / before.expenses if before.expenses else None
    if ratio is not None and ratio >= EXPENSE_JUMP:
        r.observations.append(_("Expenses rose %(p)s on %(m)s (%(a)s to %(b)s).") % {
            "p": _pct(ratio), "m": f"{last:%b %Y}",
            "a": format_money(before.expenses, cur), "b": format_money(money.expenses, cur)})

    if can(m, "maintenance.view"):
        end = metrics.month_end(board.month)
        mine = MaintenanceRequest.objects.filter(organization=m.organization, property__in=board.scope.properties())
        opened = list(mine.filter(status__in=MaintenanceRequest.OPEN))
        r.facts.append(_("Repairs: %(got)s reported and %(done)s finished in the month; %(open)s still open") % {
            "got": mine.filter(created_at__date__range=(board.month, end)).count(),
            "done": mine.filter(done_at__date__range=(board.month, end)).count(), "open": len(opened)})
        late = [x for x in opened if x.is_overdue()]
        if late:
            r.observations.append(ngettext("%(n)s repair is past its fix-by time.",
                                           "%(n)s repairs are past their fix-by time.", len(late)) % {"n": len(late)})

    ending = visible_leases(m).filter(status=Lease.Status.ACTIVE, end_date__gte=today,
                                      end_date__lte=today + datetime.timedelta(days=ENDING_DAYS)).count()
    if ending:
        r.observations.append(ngettext("%(n)s lease ends within %(d)s days.", "%(n)s leases end within %(d)s days.",
                                       ending) % {"n": ending, "d": ENDING_DAYS})

    if board.debtors:
        top = board.debtors[0]
        r.actions.append(_("Follow up on overdue accounts, starting with %(unit)s (%(a)s, %(d)s days).") % {
            "unit": top.lease.unit.code, "a": format_money(top.overdue, cur), "d": top.days})
    if board.vacancy.units:
        text = _("Review the %(n)s empty units. At their asking rent they would bring %(a)s a month.") % {
            "n": board.vacancy.units, "a": format_money(board.vacancy.amount, cur)}
        if board.vacancy.unpriced:
            text += " " + _("%(n)s of them have no asking rent set.") % {"n": board.vacancy.unpriced}
        r.actions.append(text)
    if ratio is not None and ratio >= EXPENSE_JUMP:
        r.actions.append(_("Look at what drove the rise in expenses."))
    if ending:
        r.actions.append(_("Talk to the tenants whose leases are ending about renewing."))
    return r


def body(report: Report, name: str, url: str, stop_url: str) -> str:
    lines = [_("Dear %(name)s,") % {"name": name}, "",
             _("Here is your report for %(m)s, from your NyumbaPMS records.") % {"m": f"{report.month:%B %Y}"}, "",
             _("What the records show")]
    lines += [f"- {x}" for x in report.facts]
    if report.observations:
        lines += ["", _("What stands out")] + [f"- {x}" for x in report.observations]
    if report.actions:
        lines += ["", _("Suggested next steps (suggestions, not facts)")]
        lines += [f"{i}. {x}" for i, x in enumerate(report.actions, 1)]
    lines += ["", _("Open the full figures:"), url, "", _("To stop this email: %(u)s") % {"u": stop_url}]
    return "\n".join(lines)


def stop_token(membership) -> str:
    return signing.dumps(membership.pk, salt=SALT)


def membership_for_token(token: str):
    try:
        pk = signing.loads(token, salt=SALT)
    except signing.BadSignature:
        return None
    return Membership.objects.filter(pk=pk).select_related("user", "organization").first()


def send_all(today: datetime.date | None = None) -> dict:
    """On the 1st: email last month's report to each member who can see financial reports. Once per member per
    month. A member who stopped it, has no email, or whose organization is not active is skipped. Figures about the
    business go only to an address its owner confirmed (D-223): a typo must not mail them to a stranger."""
    today = today or timezone.localdate()
    month = metrics.add_months(metrics.month_start(today), -1)
    counts = {"sent": 0, "skipped": 0, "failed": 0}
    members = (Membership.objects.filter(is_active=True, monthly_report=True, user__is_active=True)
               .exclude(user__email="").filter(user__email_verified_at__isnull=False).select_related("user", "organization", "role"))
    for m in members:
        org = m.organization
        if not org.is_operational or not can(m, "reports.view_financial"):
            counts["skipped"] += 1
            continue
        try:
            with transaction.atomic():
                MonthlyReportSent.objects.create(membership=m, month=month)
        except IntegrityError:
            counts["skipped"] += 1
            continue
        try:
            if not metrics.scope(m).properties().exists():
                raise LookupError("no properties")
            report = build(m, month, today)
            send_branded(
                _("Your %(m)s property report") % {"m": f"{month:%B %Y}"},
                body(report, m.user.get_short_name(), settings.SITE_URL + reverse("reports:dashboard"),
                     settings.SITE_URL + reverse("monthly_report_stop", args=[stop_token(m)])),
                [m.user.email], using=mailer_for(MESSAGES), org_name=org.display_name, org=org,
                button_label=_("Open your dashboard"))
        except LookupError:
            MonthlyReportSent.objects.filter(membership=m, month=month).delete()
            counts["skipped"] += 1
        except Exception:  # noqa: BLE001 - one bad address must not stop the rest; the row goes so it can retry
            MonthlyReportSent.objects.filter(membership=m, month=month).delete()
            counts["failed"] += 1
        else:
            counts["sent"] += 1
    return counts
