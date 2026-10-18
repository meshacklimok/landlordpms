"""The people who own the organizations on the platform (D-179): who they are, how big their team is, how many
units they serve, and the email sent to them.

An owner is an active member holding the organization's owner role. One person who owns two organizations shows
twice, once for each. Superuser pages only: this is the one place that lists owners' names and emails.
"""

import datetime
import logging
from collections import Counter

from django.conf import settings
from django.core.mail import EmailMultiAlternatives, mailers
from django.db.models import Count, Max, Q
from django.utils import timezone
from django.utils.translation import gettext as _

from accounts.models import Membership, Organization, TOTPDevice
from audit import services as audit
from audit.models import AuditEvent
from core import charts
from leases.models import Lease
from notifications.email_html import render_html
from notifications.providers.mail import MESSAGES, mailer_for
from properties.models import Property, Unit
from subscriptions.models import Plan
from tenants.models import Tenant

from .models import OwnerEmail, OwnerEmailRecipient

logger = logging.getLogger(__name__)

MAX_RECIPIENTS = 500
ACTIVITY = {"active": _("Active in the last 30 days"), "idle": _("Quiet for 30 days or more"),
            "never": _("Never used it")}
SORTS = {"name": _("Name"), "units": _("Most units"), "team": _("Biggest team"), "joined": _("Newest first"),
         "quiet": _("Quietest first")}


def _org_counts(model, org_ids, **filters) -> dict:
    return dict(model.objects.filter(organization__in=org_ids, **filters).values_list("organization")
                .annotate(n=Count("pk")))


def rows(*, query: str = "", status: str = "", plan: str = "", org_type: str = "", activity: str = "",
         email: str = "", sort: str = "name", pks=None, today: datetime.date | None = None) -> list[dict]:
    """One row per owner, with their organization's size. `pks` narrows to those memberships (a hand-picked list)."""
    today = today or timezone.localdate()
    qs = (Membership.objects.filter(is_active=True, role__is_owner_role=True)
          .select_related("user", "organization__subscription__plan"))
    if query:
        qs = qs.filter(Q(user__full_name__icontains=query) | Q(user__email__icontains=query)
                       | Q(user__phone__icontains=query) | Q(organization__name__icontains=query))
    if status:
        qs = qs.filter(organization__status=status)
    if org_type:
        qs = qs.filter(organization__org_type=org_type)
    if plan:
        qs = qs.filter(organization__subscription__plan__pk=plan)
    if email == "has":
        qs = qs.exclude(user__email__isnull=True).exclude(user__email="")
    elif email == "none":
        qs = qs.filter(Q(user__email__isnull=True) | Q(user__email=""))
    if pks is not None:
        qs = qs.filter(public_id__in=pks)
    members = list(qs)
    org_ids = {m.organization_id for m in members}

    units = _org_counts(Unit, org_ids)
    properties = _org_counts(Property, org_ids)
    tenants = _org_counts(Tenant, org_ids, status=Tenant.Status.ACTIVE)
    leases = dict(Lease.all_objects.filter(organization__in=org_ids, status=Lease.Status.ACTIVE)
                  .values_list("organization").annotate(n=Count("pk")))
    team = dict(Membership.objects.filter(organization__in=org_ids, is_active=True, role__is_owner_role=False)
                .values_list("organization").annotate(n=Count("pk")))
    last_seen = dict(AuditEvent.objects.filter(organization__in=org_ids).values_list("organization")
                     .annotate(t=Max("created_at")))
    now = timezone.now()
    out = []
    for m in members:
        org, user = m.organization, m.user
        seen = last_seen.get(org.pk)
        idle = (now - seen).days if seen else None
        sub = getattr(org, "subscription", None)
        out.append({
            "pk": m.public_id, "user": user, "org": org, "email": user.email or "",
            "plan": sub.plan.name if sub else "—",
            "sub_status": sub.get_status_display() if sub else "—", "units": units.get(org.pk, 0),
            "properties": properties.get(org.pk, 0), "tenants": tenants.get(org.pk, 0), "leases": leases.get(org.pk, 0),
            "team": team.get(org.pk, 0), "last_seen": seen, "idle_days": idle, "joined": m.created_at,
            "signed_in": user.last_login,
        })
    if activity == "active":
        out = [r for r in out if r["idle_days"] is not None and r["idle_days"] < 30]
    elif activity == "idle":
        out = [r for r in out if r["idle_days"] is not None and r["idle_days"] >= 30]
    elif activity == "never":
        out = [r for r in out if r["idle_days"] is None]
    keys = {"name": lambda r: (r["user"].full_name.lower(), r["org"].name.lower()),
            "units": lambda r: (-r["units"], r["org"].name.lower()),
            "team": lambda r: (-r["team"], r["org"].name.lower()),
            "joined": lambda r: -r["joined"].timestamp(),
            "quiet": lambda r: (r["idle_days"] is not None, -(r["idle_days"] or 0))}
    return sorted(out, key=keys.get(sort, keys["name"]))


def summary(all_rows: list[dict]) -> dict:
    """The figures and charts above the table, for the owners that match."""
    n = len(all_rows)
    owners = {r["user"].pk for r in all_rows}
    with_email = sum(1 for r in all_rows if r["email"])
    verified = sum(1 for r in all_rows if r["email"] and r["user"].email_verified_at)
    mfa = set(TOTPDevice.objects.filter(user__in=owners, confirmed_at__isnull=False).values_list("user", flat=True))
    month_ago = timezone.now() - datetime.timedelta(days=30)
    total_units = sum(r["units"] for r in all_rows)
    team_total = sum(r["team"] for r in all_rows)

    sizes = [(_("Just the owner"), sum(1 for r in all_rows if r["team"] == 0)),
             (_("1 to 2 helpers"), sum(1 for r in all_rows if 1 <= r["team"] <= 2)),
             (_("3 to 5"), sum(1 for r in all_rows if 3 <= r["team"] <= 5)),
             (_("6 or more"), sum(1 for r in all_rows if r["team"] >= 6))]
    top = sorted(all_rows, key=lambda r: -r["units"])[:10]
    plans = Counter(r["plan"] for r in all_rows)
    bands = [(_("Active in 7 days"), sum(1 for r in all_rows if r["idle_days"] is not None and r["idle_days"] < 7)),
             (_("8 to 30 days"), sum(1 for r in all_rows if r["idle_days"] is not None and 7 <= r["idle_days"] < 30)),
             (_("31 to 90 days"), sum(1 for r in all_rows if r["idle_days"] is not None and 30 <= r["idle_days"] < 90)),
             (_("Over 90 days"), sum(1 for r in all_rows if r["idle_days"] is not None and r["idle_days"] >= 90)),
             (_("Never"), sum(1 for r in all_rows if r["idle_days"] is None))]
    return {
        "owners": n, "people": len(owners), "with_email": with_email, "verified_email": verified,
        "without_email": n - with_email, "units": total_units, "team": team_total,
        "avg_team": round(team_total / n, 1) if n else 0, "avg_units": round(total_units / n, 1) if n else 0,
        "mfa_share": round(100 * len([o for o in owners if o in mfa]) / len(owners)) if owners else None,
        "new_30": sum(1 for r in all_rows if r["joined"] >= month_ago),
        "never_used": sum(1 for r in all_rows if r["idle_days"] is None),
        "top_units": [(r["org"].name, r["units"]) for r in top],
        "sizes": sizes, "plans": plans.most_common(), "bands": bands,
        "team_chart": charts.chart("bar", [s[0] for s in sizes],
                                   [charts.series(_("Owners"), [s[1] for s in sizes], "1")], axis="count"),
        "top_chart": charts.chart("bar", [r["org"].name for r in top],
                                  [charts.series(_("Units"), [r["units"] for r in top], "3")], axis="count",
                                  horizontal=True),
        "plan_chart": charts.doughnut(_("Owners"), charts.fold(plans.items(), _("Other")),
                                      [str(i) for i in range(1, 6)], axis="count"),
        "activity_chart": charts.chart("bar", [b[0] for b in bands],
                                       [charts.series(_("Owners"), [b[1] for b in bands], "5")], axis="count"),
    }


def plan_choices() -> list[tuple]:
    return [("", _("Any plan")), *[(str(p.pk), p.name) for p in Plan.objects.order_by("name")]]


# ---------------------------------------------------------------------------
# Email to owners
# ---------------------------------------------------------------------------

FOOTER = "-- \nYou get this because you manage {organization} on NyumbaPMS. Reply to this email if you need help."


def audience_text(params: dict, picked: int | None) -> str:
    """The audience in words, for the record."""
    if picked:
        return f"{picked} picked by hand"
    bits = [f"{k}={v}" for k, v in params.items() if v and k != "sort"]
    return ", ".join(bits)[:300] or "all owners"


def fill(text: str, row: dict) -> str:
    """Only {name} and {organization} are replaced; nothing else in the text is read as a template."""
    first = (row["user"].full_name or "").split(" ")[0] or _("there")
    return text.replace("{name}", first).replace("{organization}", row["org"].name)


def _message(subject: str, body: str, row: dict) -> EmailMultiAlternatives:
    text = fill(body, row) + "\n\n" + FOOTER.format(organization=row["org"].name)
    mail = EmailMultiAlternatives(fill(subject, row), text, settings.DEFAULT_FROM_EMAIL, [row["email"]],
                                  reply_to=[settings.SUPPORT_EMAIL] if settings.SUPPORT_EMAIL else None)
    mail.attach_alternative(render_html("NyumbaPMS", text), "text/html")
    return mail


def send_test(user, subject: str, body: str, sample: dict | None) -> None:
    """The email as one owner would get it, sent only to the person writing it."""
    row = {**(sample or {"user": user, "org": Organization(name=_("Your organization"))}), "email": user.email}
    mailers[mailer_for(MESSAGES)].send_messages([_message("[Test] " + subject, body, row)])


def send(actor, subject: str, body: str, targets: list[dict], audience: str, request=None) -> OwnerEmail:
    """Sends one email to each owner, one at a time, so no one sees another's address. A failure on one address
    is kept against that address and does not stop the rest."""
    targets = [r for r in targets if r["email"]][:MAX_RECIPIENTS]
    log = OwnerEmail.objects.create(subject=subject, body=body, audience=audience, recipients=len(targets),
                                    sent_by=actor)
    mailer = mailers[mailer_for(MESSAGES)]
    try:
        mailer.open()
    except Exception:  # noqa: BLE001 - each address then fails on its own below, with the reason
        logger.exception("Owner email: could not open the mail connection")
    sent = failed = 0
    deliveries = []
    for row in targets:
        error = ""
        try:
            mailer.send_messages([_message(subject, body, row)])
        except Exception as exc:  # noqa: BLE001 - one bad address must not stop the others
            error = (str(exc) or exc.__class__.__name__)[:300]
        sent, failed = sent + (not error), failed + bool(error)
        deliveries.append(OwnerEmailRecipient(email_log=log, organization=row["org"], user=row["user"],
                                              address=row["email"], ok=not error, error=error))
    try:
        mailer.close()
    except Exception:  # noqa: BLE001 - the mails are already sent or failed; closing is housekeeping
        logger.warning("Owner email: could not close the mail connection", exc_info=True)
    OwnerEmailRecipient.objects.bulk_create(deliveries)
    log.sent, log.failed = sent, failed
    log.save(update_fields=["sent", "failed", "updated_at"])
    audit.record("console.owner_email", actor=actor, obj=log, request=request,
                 changes={"subject": [None, subject], "recipients": [None, len(targets)], "failed": [None, failed]})
    return log
