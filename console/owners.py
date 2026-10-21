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
from django.db import transaction
from django.db.models import Count, Max, Q
from django.utils import timezone
from django.utils.translation import gettext as _

from accounts.models import Membership, Organization, TOTPDevice
from audit import services as audit
from audit.models import AuditEvent
from billing.models import Invoice
from core import charts
from core.kenya import County
from leases.models import Lease
from notifications.email_html import render_html
from notifications.providers.mail import MESSAGES, mailer_for
from payments.models import Payment, PaymentAccount
from properties.models import Property, Unit
from subscriptions.models import Plan, Subscription
from tenants.models import Tenant

from .models import OwnerEmail, OwnerEmailRecipient, OwnerNote

State = OwnerEmailRecipient.State

logger = logging.getLogger(__name__)

MAX_RECIPIENTS = 500
INSTANT_MAX = 20  # a list this short is sent while the page loads; a longer one waits for the job
DELIVER_BUDGET = datetime.timedelta(minutes=4)  # one job run stays inside the five minutes between runs
STUCK_AFTER = datetime.timedelta(minutes=30)
ACTIVITY = {"active": _("Active in the last 30 days"), "idle": _("Quiet for 30 days or more"),
            "never": _("Never used it")}
# Groups worth acting on, each with the question it answers (D-179). A row can be in several.
SEGMENTS = {
    "trial_ending": _("Trial ends within 7 days"),
    "unpaid": _("Subscription past due or lapsed"),
    "quiet": _("Quiet for 30 days or more"),
    "never": _("Never used it"),
    "near_limit": _("Using 80% or more of the plan's units"),
    "no_mfa": _("No two-step login"),
    "no_email": _("No email on file"),
    "follow_up": _("Follow-up due"),
    "risk": _("Health score below 40"),
    "setup": _("Setup not finished after 3 days"),
}
# The first things a landlord does, in order. The first one not done is where they are stuck.
STEPS = {
    "property": _("Add a property"),
    "unit": _("Add units"),
    "tenant": _("Add a tenant"),
    "lease": _("Start a lease"),
    "invoice": _("Send an invoice"),
    "payment": _("Receive a payment"),
    "account": _("Set up where money arrives"),
}
BANDS = {"healthy": _("Healthy"), "watch": _("Watch"), "risk": _("At risk")}
SORTS = {"health": _("Needs attention first (health score)"),
         "name": _("Name"), "units": _("Most units"), "team": _("Biggest team"), "joined": _("Newest first"),
         "quiet": _("Quietest first"), "value": _("Highest monthly value")}


def _org_counts(model, org_ids, **filters) -> dict:
    return dict(model.objects.filter(organization__in=org_ids, **filters).values_list("organization")
                .annotate(n=Count("pk")))


def health_score(*, idle, steps_done, sub, units, limit, team, has_mfa) -> tuple[int, list[tuple[str, int, int]]]:
    """0-100 and where the points came from: recent use (30), setup (30), subscription (20), size (10), safety (10)."""
    if idle is None:
        use = 0
    else:
        use = 30 if idle < 7 else 22 if idle < 14 else 12 if idle < 30 else 4 if idle < 60 else 0
    setup = round(30 * steps_done / len(STEPS))
    status = sub.status if sub else None
    money = {Subscription.Status.ACTIVE: 20, Subscription.Status.TRIAL: 12,
             Subscription.Status.PAST_DUE: 5}.get(status, 0)
    size = 0 if not units else 10 if not limit or units < 0.95 * limit else 6
    safety = (5 if has_mfa else 0) + (5 if team else 0)
    parts = [(_("Recent use"), use, 30), (_("Setup"), setup, 30), (_("Subscription"), money, 20),
             (_("Size"), size, 10), (_("Safety and team"), safety, 10)]
    return sum(p for _l, p, _m in parts), parts


def band(score: int) -> str:
    return "healthy" if score >= 70 else "watch" if score >= 40 else "risk"


def locations(all_rows: list[dict], top: int = 15) -> dict:
    """Where owners' properties are: by county, and by town (the area or sub-county they typed), counting each
    organization once per place however many properties it has there."""
    org_ids = {r["org"].pk for r in all_rows}
    counties = dict(County.choices)
    by_county: dict[str, dict] = {}
    by_town: dict[tuple[str, str], dict] = {}
    unit_counts = dict(Unit.objects.filter(organization__in=org_ids).values_list("property").annotate(n=Count("pk")))
    props = Property.objects.filter(organization__in=org_ids).values_list(
        "pk", "organization", "county", "area", "sub_county")
    for pk, org, county, area, sub in props:
        n = unit_counts.get(pk, 0)
        c = by_county.setdefault(county, {"orgs": set(), "properties": 0, "units": 0})
        c["orgs"].add(org)
        c["properties"] += 1
        c["units"] += n
        town = (area or sub or "").strip().title()
        if town:
            t = by_town.setdefault((town, county), {"orgs": set(), "properties": 0, "units": 0})
            t["orgs"].add(org)
            t["properties"] += 1
            t["units"] += n
    county_rows = sorted(
        ({"key": k, "name": counties.get(k) or _("Not given"), "orgs": len(v["orgs"]), "properties": v["properties"],
          "units": v["units"]} for k, v in by_county.items()),
        key=lambda r: (-r["orgs"], -r["units"], r["name"]))
    town_rows = sorted(
        ({"name": t, "county": counties.get(c, ""), "orgs": len(v["orgs"]), "properties": v["properties"],
          "units": v["units"]} for (t, c), v in by_town.items()),
        key=lambda r: (-r["orgs"], -r["units"], r["name"]))
    placed = {o for v in by_county.values() for o in v["orgs"]}
    shown = county_rows[:10]
    return {
        "counties": county_rows, "towns": town_rows[:top], "town_count": len(town_rows),
        "no_property": len(org_ids - placed),
        "chart": charts.chart("bar", [r["name"] for r in shown],
                              [charts.series(_("Organizations"), [r["orgs"] for r in shown], "1")], axis="count"),
    }


def monthly_value(sub) -> int:
    """What a paying subscription brings in a month (a yearly one divided by 12). Trials and free plans: 0."""
    if sub is None or sub.status not in (Subscription.Status.ACTIVE, Subscription.Status.PAST_DUE):
        return 0
    price = sub.plan.price(sub.interval)
    if not price or price <= 0:
        return 0
    span = {"MONTHLY": 1, "YEARLY": 12}.get(sub.interval, 12 * sub.plan.one_off_years)
    return int(price / span)


def segment_counts(all_rows: list[dict]) -> list[dict]:
    """How many owners are in each group, for the strip of links above the list."""
    return [{"key": key, "label": label, "count": sum(1 for r in all_rows if key in r["flags"])}
            for key, label in SEGMENTS.items()]


def joined_chart(all_rows: list[dict], count: int = 12) -> dict:
    """Owners who joined each month, newest last, and how many of them were active in the last 30 days."""
    ms, cur = [], timezone.localdate().replace(day=1)
    for _i in range(count):
        ms.append(cur)
        cur = (cur - datetime.timedelta(days=1)).replace(day=1)
    ms.reverse()
    joined = Counter(timezone.localtime(r["joined"]).date().replace(day=1) for r in all_rows)
    active = Counter(timezone.localtime(r["joined"]).date().replace(day=1) for r in all_rows
                     if r["idle_days"] is not None and r["idle_days"] < 30)
    labels = [m.strftime("%b %Y") for m in ms]
    table = [(lab, joined.get(m, 0), active.get(m, 0)) for lab, m in zip(labels, ms, strict=True)]
    return {"labels": labels, "table": table,
            "chart": charts.chart("bar", labels,
                                  [charts.series(_("Joined"), [joined.get(m, 0) for m in ms], "1-soft"),
                                   charts.series(_("Still active"), [active.get(m, 0) for m in ms], "1")],
                                  axis="count")}


def rows(*, query: str = "", status: str = "", plan: str = "", org_type: str = "", activity: str = "",
         email: str = "", segment: str = "", step: str = "", county: str = "",
         sort: str = "name", pks=None,
         today: datetime.date | None = None) -> list[dict]:
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
    if county:
        qs = qs.filter(organization__in=Property.objects.filter(county=county).values("organization"))
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
    mfa_on = set(TOTPDevice.objects.filter(user__in={m.user_id for m in members}, confirmed_at__isnull=False)
                 .values_list("user", flat=True))
    invoices = _org_counts(Invoice, org_ids)
    payments = _org_counts(Payment, org_ids)
    accounts = _org_counts(PaymentAccount, org_ids, type__in=["PAYBILL", "TILL", "BANK", "MPESA_PERSONAL"])
    due = set(OwnerNote.objects.filter(organization__in=org_ids, done_at__isnull=True, follow_up_on__lte=today)
              .values_list("organization", flat=True))
    now = timezone.now()
    out = []
    for m in members:
        org, user = m.organization, m.user
        seen = last_seen.get(org.pk)
        idle = (now - seen).days if seen else None
        sub = getattr(org, "subscription", None)
        n_units = units.get(org.pk, 0)
        limit = sub.plan.unit_limit if sub else None
        flags = []
        if (sub and sub.status == Subscription.Status.TRIAL and sub.trial_ends_on
                and 0 <= (sub.trial_ends_on - today).days <= 7):
            flags.append("trial_ending")
        if sub and sub.status in (Subscription.Status.PAST_DUE, Subscription.Status.LAPSED):
            flags.append("unpaid")
        if idle is not None and idle >= 30:
            flags.append("quiet")
        if idle is None:
            flags.append("never")
        if limit and n_units >= 0.8 * limit:
            flags.append("near_limit")
        if user.pk not in mfa_on:
            flags.append("no_mfa")
        if not user.email:
            flags.append("no_email")
        if org.pk in due:
            flags.append("follow_up")
        have = {"property": properties.get(org.pk, 0), "unit": n_units, "tenant": tenants.get(org.pk, 0),
                "lease": leases.get(org.pk, 0), "invoice": invoices.get(org.pk, 0),
                "payment": payments.get(org.pk, 0), "account": accounts.get(org.pk, 0)}
        steps = [(key, label, bool(have[key])) for key, label in STEPS.items()]
        steps_done = sum(1 for _k, _l, ok in steps if ok)
        stuck_at = next((k for k, _l, ok in steps if not ok), "")
        score, parts = health_score(idle=idle, steps_done=steps_done, sub=sub, units=n_units, limit=limit,
                                    team=team.get(org.pk, 0), has_mfa=user.pk in mfa_on)
        if score < 40:
            flags.append("risk")
        if stuck_at and (now - m.created_at).days >= 3:
            flags.append("setup")
        out.append({
            "steps": steps, "steps_done": steps_done, "steps_total": len(STEPS), "stuck_at": stuck_at,
            "stuck_label": STEPS.get(stuck_at, ""), "score": score, "score_parts": parts, "band": band(score),
            "band_label": BANDS[band(score)],
            "flags": flags, "tags": [SEGMENTS[f] for f in flags], "value": monthly_value(sub), "limit": limit,
            "pk": m.public_id, "user": user, "org": org, "email": user.email or "",
            "plan": sub.plan.name if sub else "—",
            "sub_status": sub.get_status_display() if sub else "—", "units": units.get(org.pk, 0),
            "properties": properties.get(org.pk, 0), "tenants": tenants.get(org.pk, 0), "leases": leases.get(org.pk, 0),
            "team": team.get(org.pk, 0), "last_seen": seen, "idle_days": idle, "joined": m.created_at,
            "signed_in": user.last_login,
        })
    if segment in SEGMENTS:
        out = [r for r in out if segment in r["flags"]]
    if step in STEPS:
        out = [r for r in out if r["stuck_at"] == step]
    if activity == "active":
        out = [r for r in out if r["idle_days"] is not None and r["idle_days"] < 30]
    elif activity == "idle":
        out = [r for r in out if r["idle_days"] is not None and r["idle_days"] >= 30]
    elif activity == "never":
        out = [r for r in out if r["idle_days"] is None]
    keys = {"health": lambda r: (r["score"], r["org"].name.lower()),
            "name": lambda r: (r["user"].full_name.lower(), r["org"].name.lower()),
            "units": lambda r: (-r["units"], r["org"].name.lower()),
            "team": lambda r: (-r["team"], r["org"].name.lower()),
            "joined": lambda r: -r["joined"].timestamp(),
            "quiet": lambda r: (r["idle_days"] is not None, -(r["idle_days"] or 0)),
            "value": lambda r: (-r["value"], r["org"].name.lower())}
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
    ranked = sorted(all_rows, key=lambda r: -r["value"])
    value_total = sum(r["value"] for r in all_rows)
    top5 = sum(r["value"] for r in ranked[:5])
    return {
        "avg_score": round(sum(r["score"] for r in all_rows) / n) if n else None,
        "health_bands": [(BANDS[b], sum(1 for r in all_rows if r["band"] == b)) for b in BANDS],
        "stuck": [(label, sum(1 for r in all_rows if r["stuck_at"] == key)) for key, label in STEPS.items()],
        "value_total": value_total, "paying_owners": sum(1 for r in all_rows if r["value"]),
        "top5_share": round(100 * top5 / value_total) if value_total else None,
        "top_value": [(r["org"].name, r["value"]) for r in ranked[:5] if r["value"]],
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
    """Queues one email for each owner and returns at once; `run_due` (the send_owner_emails job) sends them one
    at a time, so no one sees another's address and a slow mail server cannot time out the page. A short list
    (INSTANT_MAX or fewer) is sent here and now. A failure on one address is kept against it and does not stop
    the rest."""
    targets = [r for r in targets if r["email"]][:MAX_RECIPIENTS]
    with transaction.atomic():
        log = OwnerEmail.objects.create(subject=subject, body=body, audience=audience, recipients=len(targets),
                                        sent_by=actor)
        OwnerEmailRecipient.objects.bulk_create([
            OwnerEmailRecipient(email_log=log, organization=row["org"], user=row["user"], address=row["email"])
            for row in targets])
        audit.record("console.owner_email", actor=actor, obj=log, request=request,
                     changes={"subject": [None, subject], "recipients": [None, len(targets)]})
    if len(targets) <= INSTANT_MAX:
        deliver(log)
        log.refresh_from_db()
    return log


def deliver(log: OwnerEmail, *, budget: datetime.timedelta = DELIVER_BUDGET) -> int:
    """Sends the waiting addresses of one email, one at a time on one open connection, until none are left or the
    time is up. Returns how many were tried. Safe to run twice at once: an address is claimed before it is sent,
    so no one gets it twice."""
    stop = timezone.now() + budget
    mailer = mailers[mailer_for(MESSAGES)]
    tried = 0
    try:
        mailer.open()
    except Exception:  # noqa: BLE001 - each address then fails on its own below, with the reason
        logger.exception("Owner email %s: could not open the mail connection", log.pk)
    try:
        while timezone.now() < stop:
            row = _claim(log)
            if row is None:
                break
            error = ""
            try:
                mailer.send_messages([_message(log.subject, log.body, {
                    "user": row.user, "org": row.organization, "email": row.address})])
            except Exception as exc:  # noqa: BLE001 - one bad address must not stop the others
                error = (str(exc) or exc.__class__.__name__)[:300]
            OwnerEmailRecipient.objects.filter(pk=row.pk).update(
                ok=not error, error=error, state=State.FAILED if error else State.SENT)
            tried += 1
    finally:
        try:
            mailer.close()
        except Exception:  # noqa: BLE001 - the mails are already sent or failed; closing is housekeeping
            logger.warning("Owner email: could not close the mail connection", exc_info=True)
    _tally(log)
    return tried


def _claim(log: OwnerEmail) -> OwnerEmailRecipient | None:
    """Takes the next waiting address for this run; another run at the same time gets a different one."""
    with transaction.atomic():
        row = (OwnerEmailRecipient.objects.select_for_update(skip_locked=True, of=("self",))
               .select_related("organization", "user")
               .filter(email_log=log, state=State.PENDING).order_by("pk").first())
        if row is None:
            return None
        row.state, row.claimed_at = State.SENDING, timezone.now()
        row.save(update_fields=["state", "claimed_at"])
    return row


def _tally(log: OwnerEmail) -> None:
    """Counts the results onto the email; when nothing is waiting any more it is done."""
    counts = dict(OwnerEmailRecipient.objects.filter(email_log=log).values_list("state").annotate(n=Count("pk")))
    log.sent, log.failed = counts.get(State.SENT, 0), counts.get(State.FAILED, 0)
    open_ = counts.get(State.PENDING, 0) + counts.get(State.SENDING, 0)
    log.done_at = None if open_ else (log.done_at or timezone.now())
    log.save(update_fields=["sent", "failed", "done_at", "updated_at"])


def requeue_failed(log: OwnerEmail, actor, request=None) -> int:
    """Puts the addresses that failed back in the queue, for the job to try again. Only addresses marked failed
    are touched, so no one who got the email gets it twice. Returns how many were put back."""
    with transaction.atomic():
        n = OwnerEmailRecipient.objects.filter(email_log=log, state=State.FAILED).update(
            state=State.PENDING, ok=False, error="", claimed_at=None)
        if n:
            log.done_at = None
            log.save(update_fields=["done_at", "updated_at"])
            audit.record("console.owner_email_resend", actor=actor, obj=log, request=request,
                         changes={"addresses": [None, n]})
    return n


def run_due(budget: datetime.timedelta = DELIVER_BUDGET) -> dict:
    """The job: sends waiting emails, oldest first, within the time given, and closes ones with nothing left.

    An address left "sending" by a crash is marked failed, not sent again: the mail may already have gone out, and
    two copies is worse than one the person can resend by hand."""
    now = timezone.now()
    stuck = OwnerEmailRecipient.objects.filter(state=State.SENDING, claimed_at__lt=now - STUCK_AFTER)
    interrupted = stuck.update(
        state=State.FAILED, ok=False,
        error="Interrupted while sending; it may or may not have gone out. Check, then resend by hand.")
    tried = 0
    stop = now + budget
    for log in OwnerEmail.objects.filter(done_at__isnull=True).order_by("created_at"):
        left = stop - timezone.now()
        if left <= datetime.timedelta(0):
            break
        tried += deliver(log, budget=left)
    return {"tried": tried, "interrupted": interrupted}


# ---------------------------------------------------------------------------
# Create, change and notes (D-179)
# ---------------------------------------------------------------------------


def create_owner(actor, *, full_name, phone, email, organization, org_type, request=None) -> Membership:
    """A new owner account made by the platform owner: phone vouched for, no password until the owner sets one.

    The owner signs in by choosing "Forgot password" on their phone number, which proves the phone is theirs.
    """
    from django.core.exceptions import ValidationError
    from django.db import transaction

    from accounts import services as account_services
    from accounts.models import User

    email = (email or "").strip().lower() or None
    if User.objects.filter(phone=phone).exists():
        raise ValidationError({"phone": _("An account with this phone number already exists.")})
    if email and User.objects.filter(email__iexact=email).exists():
        raise ValidationError({"email": _("An account with this email already exists.")})
    with transaction.atomic():
        user = User(phone=phone, full_name=full_name.strip(), email=email, phone_verified_at=timezone.now())
        user.set_unusable_password()
        user.save()
        membership = account_services.create_organization(user=user, name=organization, org_type=org_type,
                                                          request=request)
        audit.record("console.owner_create", actor=actor, organization=membership.organization, obj=user,
                     request=request, changes={"phone": [None, phone], "organization": [None, organization]})
    return membership


def update_owner(actor, membership, *, full_name, phone, email, organization, request=None) -> dict:
    """Changes the owner's name, phone and email and the organization's name; returns what changed."""
    from django.core.exceptions import ValidationError

    from accounts.models import User

    user, org = membership.user, membership.organization
    email = (email or "").strip().lower() or None
    if User.objects.filter(phone=phone).exclude(pk=user.pk).exists():
        raise ValidationError({"phone": _("Another account uses this phone number.")})
    if email and User.objects.filter(email__iexact=email).exclude(pk=user.pk).exists():
        raise ValidationError({"email": _("Another account uses this email.")})
    new = {"full_name": full_name.strip(), "phone": phone, "email": email}
    changes = {k: [getattr(user, k), v] for k, v in new.items() if getattr(user, k) != v}
    if org.name != organization.strip():
        changes["organization"] = [org.name, organization.strip()]
    if not changes:
        return {}
    if "email" in changes:
        user.email_verified_at = None
    for k, v in new.items():
        setattr(user, k, v)
    user.save()
    if "organization" in changes:
        org.name = organization.strip()
        org.save(update_fields=["name", "updated_at"])
    audit.record("console.owner_update", actor=actor, organization=org, obj=user, request=request, changes=changes)
    return changes


def add_notes(actor, memberships, *, body, follow_up_on=None, request=None) -> int:
    """The same note on each owner's organization, one audit entry each."""
    for m in memberships:
        add_note(actor, m.organization, body=body, follow_up_on=follow_up_on, request=request)
    return len(memberships)


def add_note(actor, org, *, body, follow_up_on=None, request=None):
    note = OwnerNote.objects.create(organization=org, body=body.strip(), follow_up_on=follow_up_on, author=actor)
    audit.record("console.owner_note", actor=actor, organization=org, obj=note, request=request,
                 changes={"added": [None, note.body[:120]]})
    return note


def edit_note(actor, note, *, body, follow_up_on=None, request=None):
    note.body, note.follow_up_on = body.strip(), follow_up_on
    note.save(update_fields=["body", "follow_up_on", "updated_at"])
    audit.record("console.owner_note", actor=actor, organization=note.organization, obj=note, request=request,
                 changes={"edited": [None, note.body[:120]]})
    return note


def mark_note(actor, note, *, done: bool, request=None):
    note.done_at = timezone.now() if done else None
    note.save(update_fields=["done_at", "updated_at"])
    audit.record("console.owner_note", actor=actor, organization=note.organization, obj=note, request=request,
                 changes={"done": [not done, done]})
    return note


def delete_note(actor, note, request=None) -> None:
    audit.record("console.owner_note", actor=actor, organization=note.organization, obj=note, request=request,
                 changes={"deleted": [note.body[:120], None]})
    note.delete()


# ---------------------------------------------------------------------------
# Follow-ups across owners, and one owner's history (D-179)
# ---------------------------------------------------------------------------


def follow_ups(today=None) -> dict:
    """Every open note, grouped by when it is due: overdue, today, coming up, and the ones with no date."""
    today = today or timezone.localdate()
    notes = list(OwnerNote.objects.filter(done_at__isnull=True)
                 .select_related("organization", "author").order_by("follow_up_on", "created_at"))
    groups = {"overdue": [], "today": [], "later": [], "undated": []}
    for n in notes:
        if n.follow_up_on is None:
            groups["undated"].append(n)
        elif n.follow_up_on < today:
            groups["overdue"].append(n)
        elif n.follow_up_on == today:
            groups["today"].append(n)
        else:
            groups["later"].append(n)
    owner_pks = dict(Membership.objects.filter(is_active=True, role__is_owner_role=True,
                                               organization__in={n.organization_id for n in notes})
                     .values_list("organization", "public_id"))
    for n in notes:
        n.owner_pk = owner_pks.get(n.organization_id)
    return {**groups, "due": len(groups["overdue"]) + len(groups["today"]), "open": len(notes)}


def due_count(today=None) -> int:
    today = today or timezone.localdate()
    return OwnerNote.objects.filter(done_at__isnull=True, follow_up_on__lte=today).count()


def timeline(org, limit: int = 60) -> list[dict]:
    """One list, newest first, of what happened in an organization and what we did about it."""
    events = []
    recent = AuditEvent.objects.filter(organization=org).exclude(action="console.owner_note")
    for e in recent.select_related("actor")[:limit]:
        who = e.actor.full_name if e.actor else ""
        events.append({"when": e.created_at, "kind": "activity", "title": e.action,
                       "detail": e.object_repr, "who": who})
    for n in OwnerNote.objects.filter(organization=org).select_related("author"):
        events.append({"when": n.created_at, "kind": "note", "title": _("Note"), "detail": n.body[:200],
                       "who": n.author.full_name if n.author else ""})
    for x in OwnerEmailRecipient.objects.filter(organization=org).select_related("email_log__sent_by"):
        log = x.email_log
        events.append({"when": log.created_at, "kind": "email",
                       "title": _("Email sent") if x.ok else _("Email failed"), "detail": log.subject,
                       "who": log.sent_by.full_name if log.sent_by else ""})
    events.sort(key=lambda e: e["when"], reverse=True)
    return events[:limit]
