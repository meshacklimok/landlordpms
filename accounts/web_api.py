"""JSON for the Next.js screens (D-168): the signed-in member's own session, not an API key.

`/api/v1/` is for outside programs and takes a Bearer key. These views are for our own front end, which
runs on the same domain and so sends the same session cookie as the Django pages. Each screen gets one
read-only view here that returns what the Django page builds, through the same helpers, so a member sees
exactly the figures and links their capabilities allow. Amounts are sent already formatted so the browser
never re-implements the money rules.
"""

import datetime

from django.http import JsonResponse
from django.urls import reverse
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.utils.translation import gettext as _
from django.views import View
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import ensure_csrf_cookie

from core.money import format_money
from reports import home as reports_home

from . import demo, mfa, setup
from .navigation import nav_groups
from .permissions import can, is_membership_usable


def _num(value) -> float | None:
    return None if value is None else float(value)


def _greeting() -> str:
    hour = timezone.localtime().hour
    return _("Good morning") if hour < 12 else _("Good afternoon") if hour < 17 else _("Good evening")


@method_decorator(never_cache, name="dispatch")
class MemberJSONView(View):
    """A JSON view for a signed-in, verified member of an organization; plain status codes, never a redirect."""

    def dispatch(self, request, *args, **kwargs):
        user = request.user
        if not user.is_authenticated or not user.phone_verified:
            return JsonResponse({"error": "signed_out", "login_url": reverse("accounts:login")}, status=401)
        m = getattr(request, "membership", None)
        if m is None or not is_membership_usable(m):
            return JsonResponse({"error": "no_organization"}, status=403)
        if mfa.gate(request) is not None:
            return JsonResponse({"error": "two_step", "login_url": reverse("accounts:login_mfa")}, status=403)
        return super().dispatch(request, *args, **kwargs)


def _chrome(request, m, org, user) -> dict:
    """What `base.html` shows around every page besides the menu: the bell, the notices, the Account menu."""
    from console.access import allowed as has_console
    from notifications.services import unread_count
    from subscriptions.services import banner
    from support.legal import needs_acceptance

    from .models import Organization

    banner_row = banner(org)
    notices = []
    if org.is_demo:
        notices.append({"level": "info", "text": _("You are looking at a demo with sample data. Nothing here is real and nothing can be changed."),
                        "url": demo.trial_url(), "link": _("Start your free trial"),
                        "people": demo.people(request)})
    if org.status == Organization.Status.READ_ONLY:
        notices.append({"level": "warning", "text": _(
            "Your subscription has lapsed. Data stays visible and exportable, but nothing new can be created.")})
    elif org.status == Organization.Status.FROZEN:
        notices.append({"level": "danger", "text": _("This organization is frozen. Contact support.")})
    elif banner_row:
        notices.append({"level": banner_row["level"], "text": str(banner_row["text"]),
                        "url": reverse("subscriptions:page"), "link": _("Subscription")})
    return {
        "unread_count": unread_count(user, org),
        "notices": notices,
        "email_notice": None if user.email_verified_at else {
            "has_email": bool(user.email), "email": user.email,
            "url": reverse("accounts:verify_email" if user.email else "accounts:email"),
        },
        "terms_notice": needs_acceptance(user),
        "has_console": bool(has_console(user)),
        "memberships": [{"id": str(x.organization.public_id), "name": x.organization.name,
                         "current": x.organization_id == org.pk}
                        for x in getattr(request, "user_memberships", [])] if len(getattr(request, "user_memberships", [])) > 1 else [],
        "urls": {
            "help": reverse("support:help"), "contact": reverse("support:contact"), "security": reverse("accounts:security"),
            "console": reverse("console:overview"), "inbox": reverse("notifications:inbox"),
            "search": reverse("search:results"), "suggest": reverse("search:suggest"),
            "switch_org": reverse("accounts:switch_org"), "accept_terms": reverse("support:accept_terms"),
            "terms": reverse("support:terms"), "privacy": reverse("support:privacy"),
        },
    }


class SessionView(MemberJSONView):
    """Who is signed in and which organization: what the shell (header, menu) needs."""

    def get(self, request):
        from ai_assistant.client import enabled as assistant_set_up  # the assistant app imports accounts
        from console import viewas

        m, org, user = request.membership, request.membership.organization, request.user
        return JsonResponse({
            # The side menu by capability, as the Django menu builds it. "active" is left to the page, which knows its path.
            "nav": [{"label": str(g["label"]), "items": [
                {"label": str(i["label"]), "url": i["url"], "icon": i["icon"], "badge": i["badge"]}
                for i in g["items"]]} for g in nav_groups(request, m, assistant_set_up())],
            "user": {"name": user.full_name or user.phone, "phone": user.phone,
                     "initial": (user.full_name or user.phone or "?")[:1].upper()},
            "organization": {"name": org.name, "currency": org.currency},
            "role": m.role.name,
            "logout_url": reverse("accounts:logout"),
            # The Ask button and Security link are left out where the page behind them is not open (the demo, no assistant).
            "show_ask": bool(assistant_set_up() and (org.assistant_enabled or can(m, "organization.manage"))
                             and not demo.is_demo(request)),
            "in_demo": demo.is_demo(request),
            # A superuser looking at this account as one of its people (console view as): the banner and its Stop link.
            "viewing": ({"label": viewas.label(request), "stop_url": reverse("console:view_as_stop")}
                        if getattr(request, "viewing_as", False) else None),
            **_chrome(request, m, org, user),
        })


def _quick_actions(m, vocab) -> list[dict]:
    tenant = str(vocab["tenant"]).lower()
    rows = [
        ("payments.record", "record", _("Record a payment"), reverse("payments:pick_lease")),
        ("invoices.generate", "invoices", _("Send invoices"), reverse("billing:generate")),
        ("tenants.manage", "tenant", _("Add a %(t)s") % {"t": tenant}, reverse("tenants:create")),
        ("messages.send", "message", _("Message a %(t)s") % {"t": tenant}, reverse("notifications:compose")),
        ("maintenance.create", "repair", _("Report a repair"), reverse("maintenance:create")),
        ("expenses.submit", "expense", _("Add an expense"), reverse("expenses:create")),
    ]
    allowed = [(key, label, url) for cap, key, label, url in rows if can(m, cap)]
    return [{"key": key, "label": str(label), "url": url, "main": i == 0}
            for i, (key, label, url) in enumerate(allowed)]


def _date(value) -> str:
    return value.date().isoformat() if isinstance(value, datetime.datetime) else value.isoformat()


class HomeDataView(MemberJSONView):
    """The signed-in home (D-153), as data: the same `reports.home.home()` the Django page draws from."""

    def get(self, request):
        m = request.membership
        org = m.organization
        cur = org.currency
        home = reports_home.home(m)
        board = home.board

        def money(value):
            return format_money(value, cur)

        see_tenants = can(m, "tenants.view")
        out = {
            "date": timezone.localdate().isoformat(),
            "greeting": _greeting(),
            "user": request.user.full_name or request.user.phone,
            "organization": org.name,
            "role": m.role.name,
            "currency": cur,
            "vocab": {"tenant": str(org.vocab["tenant"]), "rent": str(org.vocab["rent"]), "units": str(org.vocab["units"])},
            "dashboard_url": reverse("reports:centre") if can(m, "dashboard.view_financial") else None,
            "actions": _quick_actions(m, org.vocab),
            "suggest_mfa": bool(mfa.suggest(m)),
            "security_url": reverse("accounts:security"),
            "tasks": [{"key": t.key, "count": t.count, "label": t.label, "url": t.url, "detail": t.detail,
                       "icon": t.icon, "urgent": t.urgent} for t in home.tasks],
            "reports": [{"label": f"{r.get_kind_display()} · {r.unit.code} · {r.unit.property.name}",
                         "url": reverse("inspections:report", args=[r.public_id]),
                         "date": r.inspected_on.isoformat()} for r in home.reports],
            "expiring_count": home.expiring_count,
            "expiring": [{"label": f"{lease.unit.code} · {lease.unit.property.name}",
                          "tenant": lease.primary_tenant.name if see_tenants and lease.primary_tenant else None,
                          "ends": lease.end_date.isoformat(),
                          "url": reverse("leases:detail", args=[lease.public_id])} for lease in home.expiring],
            "started": home.started,
            "checklist": None,
            "figures": None,
            "debtors": None,
            "call_list_url": None,
            "payments": None,
            "payments_url": None,
            "collection": [],
            "due_soon": None,
            "vacancy": None,
            "methods": [],
            "pace": None,
            "net": None,
            "nudges": [],
            "summary": None,
        }
        if can(m, "properties.manage") and not org.is_demo:  # a demo has nothing to set up
            steps = setup.checklist(org)
            required = [s for s in steps if not s.optional]
            done = sum(s.done for s in required)
            if done < len(required):
                first = next((s.key for s in required if not s.done), None)
                out["checklist"] = {
                    "done": done, "total": len(required), "next": first,
                    "minutes": sum(s.minutes for s in required if not s.done),
                    "steps": [{"key": s.key, "label": str(s.label), "url": s.url, "done": s.done,
                               "optional": s.optional, "hint": str(s.hint), "icon": s.icon,
                               "minutes": s.minutes} for s in steps],
                    "help_url": reverse("support:help_topic", args=["getting-started"]),
                }
        if board is not None and home.started:
            c, p = board.current, board.previous
            total = board.arrears.total
            out["figures"] = {
                "month_label": c.label,
                "collected": money(c.collected), "expected": money(c.expected), "rate": _num(c.rate),
                "outstanding": money(max(c.outstanding, 0)), "has_outstanding": c.outstanding > 0,
                "previous": {"label": p.label, "rate": _num(p.rate)} if p and p.rate is not None else None,
                "overdue": money(total), "overdue_leases": board.arrears.leases,
                "aging": [{"key": key, "label": str(label), "amount": money(amount),
                           "share": _num(amount / total) if total else 0}
                          for key, label, amount in board.arrears.buckets if amount],
                "arrears_url": reverse("billing:arrears") if can(m, "invoices.view") else None,
                "occupancy": {"rate": _num(board.occupancy.rate), "occupied": board.occupancy.occupied,
                              "rentable": board.occupancy.rentable, "vacant": board.occupancy.vacant},
                "trend": [{"label": row.label, "month": row.month.strftime("%b"), "rate": _num(row.rate),
                           "collected": money(row.collected)} for row in home.trend],
            }
            ds = home.due_soon
            out["due_soon"] = {"count": ds[0], "amount": money(ds[1]), "days": reports_home.DUE_SOON_DAYS,
                               "url": reverse("billing:invoices"),
                               "remind_url": (reverse("notifications:announcement_new")
                                              + f"?template=rent_due&days={reports_home.DUE_SOON_DAYS}")
                               if can(m, "messages.send_bulk") else None} if ds and ds[0] else None
            vac = home.vacancy
            out["vacancy"] = {
                "units": vac.units, "amount": money(vac.amount), "unpriced": vac.unpriced,
                "url": reverse("properties:units") + "?status=vacant" if can(m, "units.view") else None,
            } if vac and vac.units else None
            if home.pace:
                now, before, label = home.pace
                out["pace"] = {"now": money(now), "before": money(before), "label": label,
                               "change": _num((now - before) / before) if before else None}
            if home.net:
                cash, spent = home.net
                out["net"] = {"cash": money(cash), "expenses": money(spent), "net": money(cash - spent),
                              "negative": cash < spent, "url": reverse("expenses:list")}
            out["nudges"] = home.nudges
            total_cash = sum((x.amount for x in home.methods), 0)
            out["methods"] = [{"method": x.method, "label": x.label, "count": x.count, "amount": money(x.amount),
                               "share": _num(x.amount / total_cash) if total_cash else 0} for x in home.methods]
            if can(m, "invoices.view"):
                out["debtors"] = [{
                    "unit": d.lease.unit.code,
                    "who": d.lease.primary_tenant.name if see_tenants and d.lease.primary_tenant else d.lease.number,
                    "property": d.lease.unit.property.name, "days": d.days, "overdue": money(d.overdue),
                    "phone": d.lease.primary_tenant.phone if see_tenants and d.lease.primary_tenant else None,
                    "url": reverse("leases:detail", args=[d.lease.public_id]),
                } for d in home.debtors]
            if can(m, "arrears.follow_up"):
                out["call_list_url"] = reverse("billing:call_list")
            if can(m, "payments.view"):
                out["payments_url"] = reverse("payments:list")
                out["payments"] = [{
                    "label": f"{pay.lease.unit.code} · {pay.lease.unit.property.name}",
                    "method": pay.method, "method_label": pay.get_method_display(),
                    "date": _date(pay.paid_at), "amount": money(pay.amount)} for pay in home.payments]
            out["collection"] = [{"label": r.label, "rate": _num(r.rate)} for r in home.collection]
        elif home.summary is not None:
            occ = home.summary.occupancy
            out["summary"] = {"occupied": occ.occupied, "rentable": occ.rentable, "vacant": occ.vacant,
                              "reserved": occ.reserved, "maintenance": occ.maintenance,
                              "overdue_leases": home.summary.overdue_leases}
        return JsonResponse(out)


@method_decorator([never_cache, ensure_csrf_cookie], name="dispatch")
class CsrfView(View):
    """Sets the CSRF cookie in the browser and returns its token, for the Next.js screens' own POSTs.

    Open to a visitor too: the sign-in form needs it before there is a session.
    """

    def get(self, request):
        from django.middleware.csrf import get_token

        return JsonResponse({"token": get_token(request)})


# ---------------------------------------------------------------------------
# Settings (the organization's own details)
# ---------------------------------------------------------------------------


def _form_field(bound) -> dict:
    """One Django form field as plain data, so the Next.js form is drawn from the same form Django validates with."""
    from django import forms as dj

    field, widget = bound.field, bound.field.widget
    if isinstance(widget, dj.CheckboxSelectMultiple):
        kind = "multicheck"
    elif isinstance(widget, dj.CheckboxInput):
        kind = "checkbox"
    elif isinstance(widget, dj.FileInput):
        kind = "image"
    elif isinstance(widget, dj.Select):
        kind = "select"
    elif isinstance(widget, dj.Textarea):
        kind = "textarea"
    elif isinstance(widget, dj.NumberInput):
        kind = "number"
    elif isinstance(widget, dj.EmailInput):
        kind = "email"
    else:
        kind = "text"
    value = bound.value()
    if kind == "multicheck":
        value = [str(v) for v in value or []]
    elif kind == "checkbox":
        value = bool(value)
    elif kind == "image":
        value = None
    else:
        value = "" if value is None else str(value)
    out = {"name": bound.name, "label": str(field.label), "kind": kind, "required": field.required,
           "help": str(field.help_text), "value": value}
    if hasattr(field, "choices") and kind in ("select", "multicheck"):
        out["choices"] = [[str(v), str(label)] for v, label in field.choices]
    attrs = widget.attrs
    for key in ("placeholder", "inputmode", "accept"):
        if attrs.get(key):
            out[key] = str(attrs[key])
    for key, attr in (("min", "min_value"), ("max", "max_value"), ("max_length", "max_length")):
        if getattr(field, attr, None) is not None:
            out[key] = float(getattr(field, attr)) if key != "max_length" else getattr(field, attr)
    return out


def _file_url(f) -> str | None:
    try:
        return f.url if f else None
    except ValueError:
        return None


class OrganizationSettingsDataView(MemberJSONView):
    """GET the organization's details form and the other settings pages; POST (multipart, so a logo can come with
    it) to save. The rules are the Django form's and `services.update_organization`, as before (D-123)."""

    def _denied(self, request):
        if can(request.membership, "organization.manage"):
            return None
        return JsonResponse({"error": "owner_only"}, status=403)

    def _payload(self, request, form) -> dict:
        from .views import SETTINGS_PAGES

        m, org = request.membership, request.membership.organization
        return {
            "fields": [_form_field(form[name]) for name in form.fields],
            "logo_url": _file_url(org.logo), "stamp_url": _file_url(org.stamp),
            "pages": [{"label": str(label), "hint": str(hint), "url": reverse(name)}
                      for label, hint, capability, name in SETTINGS_PAGES if can(m, capability)],
            "post_url": reverse("accounts:web_organization"),
        }

    def get(self, request):
        from . import forms as account_forms
        from . import services

        if (denied := self._denied(request)) is not None:
            return denied
        org = request.organization
        form = account_forms.OrganizationSettingsForm(
            initial={f: getattr(org, f) for f in services.ORGANIZATION_FIELDS}, organization=org)
        return JsonResponse(self._payload(request, form))

    def post(self, request):
        from django.core.exceptions import ValidationError

        from . import forms as account_forms
        from . import services

        if (denied := self._denied(request)) is not None:
            return denied
        org = request.organization
        form = account_forms.OrganizationSettingsForm(request.POST, request.FILES, organization=org)
        if not form.is_valid():
            return _invalid(form)
        values = dict(form.cleaned_data)
        logo, remove_logo = values.pop("logo"), values.pop("remove_logo")
        stamp, remove_stamp = values.pop("stamp"), values.pop("remove_stamp")
        try:
            changed = services.update_organization(request.membership, request=request, **values)
            member = request.membership
            changed = services.set_logo(member, upload=logo, remove=remove_logo, request=request) or changed
            changed = services.set_stamp(member, upload=stamp, remove=remove_stamp, request=request) or changed
        except ValidationError as exc:
            form.add_error(None, " ".join(exc.messages))
            return _invalid(form)
        org.refresh_from_db()
        fresh = account_forms.OrganizationSettingsForm(
            initial={f: getattr(org, f) for f in services.ORGANIZATION_FIELDS}, organization=org)
        return JsonResponse({**self._payload(request, fresh),
                             "message": _("Organization details saved.") if changed else _("Nothing changed.")})


def _invalid(form) -> JsonResponse:
    fields = {("__all__" if k == "__all__" else k): [str(e) for e in v] for k, v in form.errors.items()}
    return JsonResponse({"error": "invalid", "fields": fields,
                         "message": " ".join(fields.get("__all__", [])) or _("Check the fields marked below.")},
                        status=422)
