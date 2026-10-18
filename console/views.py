"""The developer console (D-135): the platform's own analytics and the team that may see them.

Not for landlords. Anyone without a seat gets a plain 404, so the pages are not even confirmed to exist.
"""

import datetime
import logging
import uuid

from django.conf import settings
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils import timezone
from django.utils.http import urlencode
from django.utils.translation import gettext as _
from django.views.generic import TemplateView, View

from accounts import data_export, mfa
from accounts.mixins import VerifiedUserRequiredMixin
from accounts.models import DataExport, Organization
from audit import services as audit
from audit.models import AuditEvent
from core import exports, system_messages
from core.models import EmailRouting, SystemMessageSetting
from growth.models import Referral, ReferralPolicy
from notifications import catalog
from notifications.models import NotificationTypeSetting

from . import access, forms, insights, metrics, owners, services
from .models import OwnerEmail, PlatformAnnouncement, PlatformExpense, PlatformStaff

logger = logging.getLogger(__name__)


class ConsoleMixin(VerifiedUserRequiredMixin):
    """A seat on the console team, two-step login for the session, and the page's capability."""

    required_capability: str | None = None

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        seat = access.seat(request.user)
        if seat is None:
            raise Http404
        if not mfa.is_enabled(request.user):
            messages.warning(request, _("Set up two-step login to open the console."))
            return redirect("accounts:security")
        if not mfa.session_verified(request):
            return redirect(f"{reverse('accounts:login_mfa')}?{urlencode({'next': request.get_full_path()})}")
        if self.required_capability and self.required_capability not in access.allowed(request.user):
            raise PermissionDenied(self.required_capability)
        request.console_seat = seat
        if seat.pk and (seat.last_seen_at is None or timezone.now() - seat.last_seen_at > datetime.timedelta(minutes=5)):
            PlatformStaff.objects.filter(pk=seat.pk).update(last_seen_at=timezone.now())
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["allowed"] = access.allowed(self.request.user)
        ctx["seat"] = self.request.console_seat
        ctx["today"] = timezone.localdate()
        return ctx


class OverviewView(ConsoleMixin, TemplateView):
    required_capability = "overview"
    template_name = "console/overview.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        signups = metrics.signups()
        funnel = metrics.funnel()
        ctx.update(figures=metrics.overview(), signups=signups, funnel=funnel, cohorts=metrics.cohorts(),
                   specs={"signups": signups["chart"], "funnel": metrics.funnel_chart(funnel)})
        return ctx


class RevenueView(ConsoleMixin, TemplateView):
    required_capability = "revenue"
    template_name = "console/revenue.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        data = metrics.revenue()
        ctx.update(data=data, specs={"collected": data["chart"], "plans": data["plan_chart"]})
        return ctx


class BehaviourView(ConsoleMixin, TemplateView):
    required_capability = "behaviour"
    template_name = "console/behaviour.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        data, methods = metrics.behaviour(), insights.payment_methods()
        ctx.update(data=data, methods=methods,
                   specs={"timeliness": data["timeliness_chart"], "methods": data["method_chart"],
                          "days": data["pay_days_chart"], "matching": data["match_chart"],
                          "method_counts": methods["count_chart"], "method_months": methods["chart"]})
        return ctx


class UsageView(ConsoleMixin, TemplateView):
    required_capability = "engagement"
    template_name = "console/usage.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        data = metrics.engagement()
        ctx.update(data=data, specs={"active": data["active_chart"], "logins": data["login_chart"],
                                     "opens": data["opens_chart"], "features": data["feature_chart"]})
        return ctx


class OrganizationsView(ConsoleMixin, TemplateView):
    required_capability = "organizations"
    template_name = "console/organizations.html"

    def rows(self):
        form = forms.OrganizationFilterForm(self.request.GET)
        data = form.cleaned_data if form.is_valid() else {}
        return form, metrics.organizations(query=data.get("q", ""), status=data.get("status", ""))

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        form, rows = self.rows()
        ctx.update(form=form, rows=rows, query=self.request.GET.urlencode())
        return ctx


class WatchlistView(ConsoleMixin, TemplateView):
    required_capability = "organizations"
    template_name = "console/watchlist.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["rows"] = metrics.watchlist()
        return ctx


class OrganizationsExportView(OrganizationsView):
    def get(self, request, *args, **kwargs):
        _form, rows = self.rows()
        response = exports.csv_response("organizations.csv")
        out = exports.writer(response)
        out.writerow(["Organization", "Type", "Status", "Plan", "Subscription", "Signed up", "Units", "Active leases",
                      "Staff", "Collected this month", "On time %", "Matched on its own %", "Last active"])
        for r in rows:
            org = r["org"]
            out.writerow([org.name, org.get_org_type_display(), org.get_status_display(), r["plan"], r["sub_status"],
                          org.created_at.date().isoformat(), r["units"], r["leases"], r["staff"], r["collected"],
                          r["on_time"] if r["on_time"] is not None else "",
                          r["auto_share"] if r["auto_share"] is not None else "",
                          r["last_seen"].date().isoformat() if r["last_seen"] else ""])
        audit.record("console.export", actor=request.user, request=request, changes={"what": [None, "organizations"]})
        return response


class OwnerMixin(ConsoleMixin):
    """Superusers only (D-137): the controls that change things, not just show them."""

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and not request.user.is_superuser:
            if access.seat(request.user) is None:
                raise Http404
            raise PermissionDenied("owner")
        return super().dispatch(request, *args, **kwargs)


class OrganizationView(ConsoleMixin, TemplateView):
    required_capability = "organizations"
    template_name = "console/organization.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        org = get_object_or_404(Organization, public_id=self.kwargs["public_id"])
        data = metrics.organization_detail(org)
        ctx.update(org=org, data=data, specs={"money": data["chart"]})
        if self.request.user.is_superuser:
            ctx["control"] = forms.OrganizationControlForm()
            ctx["exports"] = data_export.recent(org, self.request.user, for_console=True)
        return ctx


class OrganizationDataExportView(OwnerMixin, View):
    """Asks for one organization's data as a ZIP (D-177), for a request to hand it over or for a lawyer.

    Shown on the organization's own audit log. The run_data_exports job makes it and texts the link.
    """

    def post(self, request, public_id):
        org = get_object_or_404(Organization, public_id=public_id)
        _export, queued = data_export.request_export(org, request.user, request, for_console=True)
        messages.success(request, _("The ZIP is being made. You will get an SMS when it is ready.") if queued
                         else _("A ZIP of this organization is already being made."))
        return redirect("console:organization", public_id=org.public_id)


class OrganizationDataDownloadView(OwnerMixin, View):
    def get(self, request, public_id, export_id):
        export = get_object_or_404(DataExport, public_id=export_id, organization__public_id=public_id, for_console=True)
        return data_export.serve(export, request)


class OrganizationControlView(OwnerMixin, View):
    def post(self, request, public_id):
        org = get_object_or_404(Organization.objects.select_related("subscription"), public_id=public_id)
        form = forms.OrganizationControlForm(request.POST)
        if form.is_valid():
            d = form.cleaned_data
            try:
                if d["action"] == "status":
                    services.set_organization_status(request.user, org, d["status"], d["reason"], request=request)
                    messages.success(request, _("Status changed."))
                else:
                    sub = services.extend_trial(request.user, org, d["days"] or 0, d["reason"], request=request)
                    messages.success(request, _("Trial now ends on %(d)s.") % {"d": sub.trial_ends_on})
            except ValidationError as exc:
                messages.error(request, " ".join(exc.messages))
        else:
            messages.error(request, _("Check the form: a reason is needed."))
        return redirect("console:organization", public_id=org.public_id)


class AnnouncementsView(OwnerMixin, TemplateView):
    template_name = "console/announcements.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["form"] = kwargs.get("form") or forms.AnnouncementForm()
        ctx["items"] = PlatformAnnouncement.objects.all()[:30]
        return ctx

    def post(self, request, *args, **kwargs):
        if "toggle" in request.POST:
            item = get_object_or_404(PlatformAnnouncement, pk=request.POST.get("toggle"))
            services.switch_announcement(request.user, item, not item.is_active, request=request)
            return redirect("console:announcements")
        form = forms.AnnouncementForm(request.POST)
        if form.is_valid():
            d = form.cleaned_data
            ends = d["ends_at"]
            if ends and timezone.is_naive(ends):
                ends = timezone.make_aware(ends)
            services.announce(request.user, message=d["message"], level=d["level"], ends_at=ends, request=request)
            messages.success(request, _("Shown to everyone signed in."))
            return redirect("console:announcements")
        return self.render_to_response(self.get_context_data(form=form))


class EmailRoutingView(OwnerMixin, TemplateView):
    """Which mail account (Resend or Brevo) sends which mail. The logins stay in the server's environment."""

    template_name = "console/email.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["form"] = kwargs.get("form") or forms.EmailRoutingForm(instance=EmailRouting.get())
        ctx["accounts"] = [(label, alias in settings.MAILERS) for alias, label in EmailRouting.Account.choices]
        return ctx

    def post(self, request, *args, **kwargs):
        form = forms.EmailRoutingForm(request.POST, instance=EmailRouting.get())
        if form.is_valid():
            obj = form.save()
            audit.record("platform.email_routing", actor=request.user, obj=obj, request=request,
                         changes={"system_account": obj.system_account, "messages_account": obj.messages_account})
            messages.success(request, _("Saved. New email goes out this way straight away."))
            return redirect("console:email")
        return self.render_to_response(self.get_context_data(form=form))


class ReferralPolicyView(OwnerMixin, TemplateView):
    """What a referral earns each side and when it counts (D-133). Applies to referrals still waiting as well."""

    template_name = "console/referrals.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["form"] = kwargs.get("form") or forms.ReferralPolicyForm(instance=ReferralPolicy.get())
        ctx["waiting"] = Referral.objects.filter(status=Referral.Status.SIGNED_UP).count()
        ctx["rewarded"] = Referral.objects.filter(status=Referral.Status.REWARDED).count()
        return ctx

    def post(self, request, *args, **kwargs):
        form = forms.ReferralPolicyForm(request.POST, instance=ReferralPolicy.get())
        if form.is_valid():
            obj = form.save()
            audit.record("platform.referral_policy", actor=request.user, obj=obj, request=request,
                         changes={f: str(form.cleaned_data[f]) for f in form.changed_data})
            messages.success(request, _("Saved. It applies to new and waiting referrals from now on."))
            return redirect("console:referrals")
        return self.render_to_response(self.get_context_data(form=form))


class NotificationTypesView(OwnerMixin, TemplateView):
    """What each SMS, WhatsApp and email notification is for, and its platform-wide defaults."""

    template_name = "console/notifications.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        changed = set(NotificationTypeSetting.objects.values_list("type", flat=True))
        ctx["rows"] = [(t, t.codename in changed) for t in catalog.types()]
        return ctx


class NotificationTypeView(OwnerMixin, TemplateView):
    template_name = "console/notification_form.html"

    def _base(self):
        base = catalog.BY_CODENAME.get(self.kwargs["codename"])
        if base is None:
            raise Http404
        return base

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        base = self._base()
        now = catalog.get(base.codename)
        initial = {"enabled": now.enabled, "channels": ", ".join(now.channels),
                   "offsets": ", ".join(str(o) for o in now.offsets), "mandatory": now.mandatory,
                   "urgent": now.urgent}
        ctx.update(base=base, form=kwargs.get("form") or forms.NotificationTypeForm(base, initial=initial))
        return ctx

    def post(self, request, *args, **kwargs):
        base = self._base()
        if "reset" in request.POST:
            NotificationTypeSetting.objects.filter(type=base.codename).delete()
            catalog.clear_platform_settings()
            audit.record("platform.notification_type", actor=request.user, request=request,
                         changes={"type": base.codename, "reset": True})
            messages.success(request, _("Back to the built-in defaults."))
            return redirect("console:notifications")
        form = forms.NotificationTypeForm(base, request.POST)
        if form.is_valid():
            d = form.cleaned_data
            NotificationTypeSetting.objects.update_or_create(type=base.codename, defaults={
                "enabled": d["enabled"], "channels": d["channels"], "offsets": d["offsets"],
                "mandatory": d["mandatory"], "urgent": d["urgent"], "updated_by": request.user})
            catalog.clear_platform_settings()
            audit.record("platform.notification_type", actor=request.user, request=request,
                         changes={"type": base.codename, "enabled": d["enabled"], "channels": d["channels"],
                                  "offsets": d["offsets"], "mandatory": d["mandatory"], "urgent": d["urgent"]})
            messages.success(request, _("Saved. It applies to the next message."))
            return redirect("console:notifications")
        return self.render_to_response(self.get_context_data(form=form))


class SystemMessagesView(OwnerMixin, TemplateView):
    """Switch our own messages (codes, security alerts, invitations, alerts to us) on or off, by channel."""

    template_name = "console/system_messages.html"

    def _rows(self):
        saved = {r.kind: r for r in SystemMessageSetting.objects.all()}
        rows = []
        for kind in system_messages.KINDS:
            row = saved.get(kind.key)
            rows.append({"kind": kind, "enabled": row.enabled if row else True,
                         "sms": (row.sms if row else True) if system_messages.SMS in kind.channels else None,
                         "email": (row.email if row else True) if system_messages.EMAIL in kind.channels else None})
        return rows

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["rows"] = self._rows()
        return ctx

    def post(self, request, *args, **kwargs):
        changed = []
        saved = {r.kind: r for r in SystemMessageSetting.objects.all()}
        for kind in system_messages.KINDS:
            want = {"enabled": f"{kind.key}.enabled" in request.POST,
                    "sms": f"{kind.key}.sms" in request.POST or system_messages.SMS not in kind.channels,
                    "email": f"{kind.key}.email" in request.POST or system_messages.EMAIL not in kind.channels}
            row = saved.get(kind.key)
            before = ({"enabled": row.enabled, "sms": row.sms, "email": row.email} if row
                      else {"enabled": True, "sms": True, "email": True})
            if want == before:
                continue
            SystemMessageSetting.objects.update_or_create(kind=kind.key, defaults={**want, "updated_by": request.user})
            changed.append({"kind": kind.key, **want})
        system_messages.clear_cache()
        if changed:
            audit.record("platform.system_message", actor=request.user, request=request, changes={"changed": changed})
        messages.success(request, _("Saved. It applies to the next message.") if changed else _("Nothing changed."))
        return redirect("console:system_messages")


class AuditLogView(OwnerMixin, TemplateView):
    template_name = "console/audit.html"
    per_page = 100

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        form = forms.AuditFilterForm(self.request.GET)
        data = form.cleaned_data if form.is_valid() else {}
        qs = AuditEvent.objects.select_related("actor", "organization")
        if data.get("action"):
            qs = qs.filter(action__icontains=data["action"])
        if data.get("organization"):
            qs = qs.filter(organization__name__icontains=data["organization"])
        if data.get("actor"):
            qs = qs.filter(Q(actor__full_name__icontains=data["actor"]) | Q(actor__phone__icontains=data["actor"]))
        page = Paginator(qs, self.per_page).get_page(self.request.GET.get("page"))
        params = self.request.GET.copy()
        params.pop("page", None)
        ctx.update(form=form, page=page, query=params.urlencode())
        return ctx


class GrowthView(ConsoleMixin, TemplateView):
    required_capability = "growth"
    template_name = "console/growth.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        data, geo = insights.growth(), insights.geography()
        ctx.update(data=data, geo=geo, funnel=metrics.funnel(), specs={
            "growth": data["growth_chart"], "new": data["new_chart"], "roles": data["role_chart"],
            "sizes": data["size_chart"], "counties": geo["chart"]})
        return ctx


class ChannelsView(ConsoleMixin, TemplateView):
    required_capability = "channels"
    template_name = "console/channels.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        msgs, ai = insights.channels(), insights.ai_usage()
        ctx.update(msgs=msgs, ai=ai, specs={"messages": msgs["chart"], "ai": ai["chart"], "tokens": ai["token_chart"]})
        return ctx


class CostsView(ConsoleMixin, TemplateView):
    """What each landlord cost us in a month: SMS, photo scans and assistant questions (D-145). ?format=csv."""

    required_capability = "finance"
    template_name = "console/costs.html"

    def month(self) -> datetime.date:
        try:
            return datetime.datetime.strptime(self.request.GET.get("month", ""), "%Y-%m").date()
        except ValueError:
            return timezone.localdate().replace(day=1)

    def get(self, request, *args, **kwargs):
        if request.GET.get("format") != "csv":
            return super().get(request, *args, **kwargs)
        data = insights.costs_by_organization(self.month())
        response = exports.csv_response(f"costs-{data['month']:%Y-%m}.csv")
        out = exports.writer(response)
        out.writerow(["Organization", "SMS parts", "SMS cost KES", "SMS billed KES", "Photo scans", "Scan cost KES",
                      "Assistant questions", "Tokens in", "Tokens out", "Assistant cost KES", "Total cost KES"])
        for r in data["rows"]:
            name = r["org"].name
            out.writerow([name, r["sms_parts"], r["sms_cost"], r["sms_billed"],
                          r["scans"], r["scan_cost"], r["questions"], r["tokens_in"], r["tokens_out"], r["ai_cost"],
                          r["total"]])
        audit.record("console.export", actor=request.user, request=request, changes={"what": [None, "costs"]})
        return response

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        month = self.month()
        ctx.update(data=insights.costs_by_organization(month),
                   previous=(month - datetime.timedelta(days=1)).replace(day=1),
                   next=(month + datetime.timedelta(days=32)).replace(day=1))
        if ctx["next"] > ctx["today"]:
            ctx["next"] = None
        return ctx


class MoneyView(ConsoleMixin, TemplateView):
    required_capability = "finance"
    template_name = "console/money.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        data = insights.money()
        form = kwargs.get("form") or forms.ExpenseForm(initial={"spent_on": timezone.localdate()})
        ctx.update(data=data, form=form, expenses=PlatformExpense.objects.all()[:60], editing=kwargs.get("editing"),
                   specs={"money": data["chart"], "profit": data["cumulative_chart"], "costs": data["category_chart"]})
        return ctx

    def post(self, request, *args, **kwargs):
        form = forms.ExpenseForm(request.POST)
        if form.is_valid():
            services.save_expense(request.user, form, request=request)
            messages.success(request, _("Saved."))
            return redirect("console:money")
        return self.render_to_response(self.get_context_data(form=form))


class ExpenseEditView(MoneyView):
    def expense(self):
        return get_object_or_404(PlatformExpense, pk=self.kwargs["pk"])

    def get_context_data(self, **kwargs):
        expense = self.expense()
        kwargs.setdefault("form", forms.ExpenseForm(instance=expense))
        return super().get_context_data(editing=expense, **kwargs)

    def post(self, request, *args, **kwargs):
        expense = self.expense()
        if "delete" in request.POST:
            services.delete_expense(request.user, expense, request=request)
            messages.success(request, _("Deleted."))
            return redirect("console:money")
        form = forms.ExpenseForm(request.POST, instance=expense)
        if form.is_valid():
            services.save_expense(request.user, form, request=request)
            messages.success(request, _("Saved."))
            return redirect("console:money")
        return self.render_to_response(self.get_context_data(form=form))


class HealthView(ConsoleMixin, TemplateView):
    required_capability = "health"
    template_name = "console/health.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["data"] = metrics.health()
        return ctx


class TeamView(ConsoleMixin, TemplateView):
    required_capability = "staff"
    template_name = "console/team.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["people"] = PlatformStaff.objects.select_related("user", "added_by")
        ctx["labels"] = access.CAPABILITIES
        ctx["history"] = AuditEvent.objects.filter(action__startswith="console.").select_related("actor")[:30]
        return ctx


class TeamAddView(ConsoleMixin, TemplateView):
    required_capability = "staff"
    template_name = "console/team_form.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["form"] = kwargs.get("form") or forms.AddSeatForm()
        ctx["presets"] = access.ROLE_PRESETS
        return ctx

    def post(self, request, *args, **kwargs):
        form = forms.AddSeatForm(request.POST)
        if form.is_valid():
            try:
                seat = services.add(request.user, request=request, **form.cleaned_data)
            except ValidationError as exc:
                form.add_error(None, exc)
            else:
                messages.success(request, _("%(name)s can now open the console.") % {"name": seat.user.full_name})
                return redirect("console:team")
        return self.render_to_response(self.get_context_data(form=form))


class TeamEditView(ConsoleMixin, TemplateView):
    required_capability = "staff"
    template_name = "console/team_form.html"

    def seat(self) -> PlatformStaff:
        return get_object_or_404(PlatformStaff.objects.select_related("user"), pk=self.kwargs["pk"])

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        seat = self.seat()
        ctx["form"] = kwargs.get("form") or forms.EditSeatForm(initial={
            "role": seat.role, "capabilities": seat.capabilities, "note": seat.note, "is_active": seat.is_active})
        ctx["person"] = seat
        ctx["presets"] = access.ROLE_PRESETS
        return ctx

    def post(self, request, *args, **kwargs):
        seat = self.seat()
        form = forms.EditSeatForm(request.POST)
        if form.is_valid():
            try:
                services.update(request.user, seat, request=request, **form.cleaned_data)
            except ValidationError as exc:
                form.add_error(None, exc)
            else:
                messages.success(request, _("Saved."))
                return redirect("console:team")
        return self.render_to_response(self.get_context_data(form=form))


class OwnersView(OwnerMixin, TemplateView):
    """Every organization owner with their email, team and units, and the analytics over them (D-179)."""

    template_name = "console/owners.html"
    FILTERS = ("q", "status", "plan", "org_type", "activity", "email", "sort")

    def filtered(self, pks=None):
        form = forms.OwnerFilterForm(self.request.POST if self.request.method == "POST" else self.request.GET)
        data = form.cleaned_data if form.is_valid() else {}
        params = {k: data.get(k, "") for k in self.FILTERS}
        return form, params, owners.rows(**{("query" if k == "q" else k): v for k, v in params.items()}, pks=pks)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        form, _params, rows = self.filtered()
        data = owners.summary(rows)
        page = Paginator(rows, 50).get_page(self.request.GET.get("page"))
        query = self.request.GET.copy()
        query.pop("page", None)
        ctx.update(form=form, page=page, data=data, count=len(rows), query=query.urlencode(),
                   specs={"team": data["team_chart"], "top": data["top_chart"], "plans": data["plan_chart"],
                          "activity": data["activity_chart"]})
        return ctx


class OwnersExportView(OwnersView):
    def get(self, request, *args, **kwargs):
        _form, _params, rows = self.filtered()
        response = exports.csv_response("owners.csv")
        out = exports.writer(response)
        out.writerow(["Owner", "Email", "Phone", "Organization", "Kind", "Status", "Plan", "Subscription", "Units",
                      "Properties", "Active leases", "Tenants", "Team members", "Joined", "Last active"])
        for r in rows:
            out.writerow([r["user"].full_name, r["email"], r["user"].phone, r["org"].name,
                          r["org"].get_org_type_display(), r["org"].get_status_display(), r["plan"], r["sub_status"],
                          r["units"], r["properties"], r["leases"], r["tenants"], r["team"],
                          timezone.localdate(r["joined"]).isoformat(),
                          timezone.localdate(r["last_seen"]).isoformat() if r["last_seen"] else ""])
        audit.record("console.export", actor=request.user, request=request, changes={"what": [None, "owners"]})
        return response


class OwnerEmailView(OwnersView):
    """Write one email to the owners shown (or the ones ticked), see who gets it, send a test, then send."""

    template_name = "console/owner_email.html"

    def picked(self):
        raw = self.request.POST.getlist("m") if self.request.method == "POST" else self.request.GET.getlist("m")
        ids = []
        for value in raw:
            try:
                ids.append(str(uuid.UUID(value)))
            except ValueError:
                continue
        return ids or None

    def targets(self):
        _form, params, rows = self.filtered(pks=self.picked())
        return params, [r for r in rows if r["email"]], len(rows)

    def get_context_data(self, **kwargs):
        ctx = super(OwnersView, self).get_context_data(**kwargs)
        params, targets, shown = self.targets()
        ctx.update(form=kwargs.get("form") or forms.OwnerEmailForm(), targets=targets, shown=shown,
                   no_email=shown - len(targets), picked=self.picked() or [], cap=owners.MAX_RECIPIENTS,
                   params={k: v for k, v in params.items() if v},
                   history=OwnerEmail.objects.select_related("sent_by")[:10])
        return ctx

    def post(self, request, *args, **kwargs):
        form = forms.OwnerEmailForm(request.POST)
        params, targets, _shown = self.targets()
        if not form.is_valid():
            return self.render_to_response(self.get_context_data(form=form))
        d = form.cleaned_data
        if "test" in request.POST:
            if not request.user.email:
                messages.error(request, _("Add an email to your own account first; the test goes there."))
            else:
                try:
                    owners.send_test(request.user, d["subject"], d["body"], targets[0] if targets else None)
                    messages.success(request, _("A test was sent to %(e)s.") % {"e": request.user.email})
                except Exception:  # noqa: BLE001 - say it failed; the cause is in the logs
                    logger.exception("Owner email test failed")
                    messages.error(request, _("The test could not be sent. Check the email settings."))
            return self.render_to_response(self.get_context_data(form=form))
        if not d["checked"]:
            form.add_error("checked", _("Tick this to send."))
            return self.render_to_response(self.get_context_data(form=form))
        if not targets:
            form.add_error(None, _("No one to send to."))
            return self.render_to_response(self.get_context_data(form=form))
        log = owners.send(request.user, d["subject"], d["body"], targets,
                          owners.audience_text(params, len(self.picked() or [])), request=request)
        text = _("Sent to %(n)s owners.") % {"n": log.sent}
        if log.failed:
            text += " " + _("%(f)s could not be sent.") % {"f": log.failed}
        messages.success(request, text)
        return redirect("console:owner_email_detail", pk=log.pk)


class OwnerEmailsView(OwnerMixin, TemplateView):
    template_name = "console/owner_emails.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["items"] = OwnerEmail.objects.select_related("sent_by")[:100]
        return ctx


class OwnerEmailDetailView(OwnerMixin, TemplateView):
    template_name = "console/owner_email_detail.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        item = get_object_or_404(OwnerEmail.objects.select_related("sent_by"), pk=self.kwargs["pk"])
        ctx.update(item=item, deliveries=item.deliveries.select_related("organization", "user"))
        return ctx


class EmailPreviewsView(ConsoleMixin, TemplateView):
    """Every kind of email we send, drawn as the recipient sees it (made-up words)."""

    template_name = "console/email_previews.html"

    def get_context_data(self, **kwargs):
        from notifications import email_previews

        ctx = super().get_context_data(**kwargs)
        ctx["samples"] = [{"key": k, "label": label} for k, label, *_rest in email_previews.SAMPLES]
        return ctx


class EmailPreviewView(ConsoleMixin, View):
    def get(self, request, key):
        from django.http import HttpResponse

        from notifications import email_previews

        html = email_previews.render(key)
        if html is None:
            raise Http404
        return HttpResponse(html)
