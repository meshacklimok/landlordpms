"""The developer console (D-135): the platform's own analytics and the team that may see them.

Not for landlords. Anyone without a seat gets a plain 404, so the pages are not even confirmed to exist.
"""

import csv
import datetime

from django.conf import settings
from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Q
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils import timezone
from django.utils.http import urlencode
from django.utils.translation import gettext as _
from django.views.generic import TemplateView, View

from accounts import mfa
from accounts.mixins import VerifiedUserRequiredMixin
from accounts.models import Organization
from audit import services as audit
from audit.models import AuditEvent
from core.models import EmailRouting

from . import access, forms, insights, metrics, services
from .models import Announcement, PlatformExpense, PlatformStaff


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
    template_name = "analytics/overview.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        signups = metrics.signups()
        funnel = metrics.funnel()
        ctx.update(figures=metrics.overview(), signups=signups, funnel=funnel, cohorts=metrics.cohorts(),
                   specs={"signups": signups["chart"], "funnel": metrics.funnel_chart(funnel)})
        return ctx


class RevenueView(ConsoleMixin, TemplateView):
    required_capability = "revenue"
    template_name = "analytics/revenue.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        data = metrics.revenue()
        ctx.update(data=data, specs={"collected": data["chart"], "plans": data["plan_chart"]})
        return ctx


class BehaviourView(ConsoleMixin, TemplateView):
    required_capability = "behaviour"
    template_name = "analytics/behaviour.html"

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
    template_name = "analytics/usage.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        data = metrics.engagement()
        ctx.update(data=data, specs={"active": data["active_chart"], "logins": data["login_chart"],
                                     "opens": data["opens_chart"], "features": data["feature_chart"]})
        return ctx


class OrganizationsView(ConsoleMixin, TemplateView):
    required_capability = "organizations"
    template_name = "analytics/organizations.html"

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
    template_name = "analytics/watchlist.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["rows"] = metrics.watchlist()
        return ctx


class OrganizationsExportView(OrganizationsView):
    def get(self, request, *args, **kwargs):
        _form, rows = self.rows()
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = 'attachment; filename="organizations.csv"'
        out = csv.writer(response)
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
    template_name = "analytics/organization.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        org = get_object_or_404(Organization, public_id=self.kwargs["public_id"])
        data = metrics.organization_detail(org)
        ctx.update(org=org, data=data, specs={"money": data["chart"]})
        if self.request.user.is_superuser:
            ctx["control"] = forms.OrganizationControlForm()
        return ctx


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
    template_name = "analytics/announcements.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["form"] = kwargs.get("form") or forms.AnnouncementForm()
        ctx["items"] = Announcement.objects.all()[:30]
        return ctx

    def post(self, request, *args, **kwargs):
        if "toggle" in request.POST:
            item = get_object_or_404(Announcement, pk=request.POST.get("toggle"))
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

    template_name = "analytics/email.html"

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


class AuditLogView(OwnerMixin, TemplateView):
    template_name = "analytics/audit.html"
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
    template_name = "analytics/growth.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        data, geo = insights.growth(), insights.geography()
        ctx.update(data=data, geo=geo, funnel=metrics.funnel(), specs={
            "growth": data["growth_chart"], "new": data["new_chart"], "roles": data["role_chart"],
            "sizes": data["size_chart"], "counties": geo["chart"]})
        return ctx


class ChannelsView(ConsoleMixin, TemplateView):
    required_capability = "channels"
    template_name = "analytics/channels.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        msgs, ai = insights.channels(), insights.ai_usage()
        ctx.update(msgs=msgs, ai=ai, specs={"messages": msgs["chart"], "ai": ai["chart"], "tokens": ai["token_chart"]})
        return ctx


class CostsView(ConsoleMixin, TemplateView):
    """What each landlord cost us in a month: SMS, photo scans and assistant questions (D-145). ?format=csv."""

    required_capability = "finance"
    template_name = "analytics/costs.html"

    def month(self) -> datetime.date:
        try:
            return datetime.datetime.strptime(self.request.GET.get("month", ""), "%Y-%m").date()
        except ValueError:
            return timezone.localdate().replace(day=1)

    def get(self, request, *args, **kwargs):
        if request.GET.get("format") != "csv":
            return super().get(request, *args, **kwargs)
        data = insights.costs_by_organization(self.month())
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = f'attachment; filename="costs-{data["month"]:%Y-%m}.csv"'
        out = csv.writer(response)
        out.writerow(["Organization", "SMS parts", "SMS cost KES", "SMS billed KES", "Photo scans", "Scan cost KES",
                      "Assistant questions", "Tokens in", "Tokens out", "Assistant cost KES", "Total cost KES"])
        for r in data["rows"]:
            name = r["org"].name
            out.writerow(["'" + name if name[:1] in "=+-@" else name, r["sms_parts"], r["sms_cost"], r["sms_billed"],
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
    template_name = "analytics/money.html"

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
    template_name = "analytics/health.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["data"] = metrics.health()
        return ctx


class TeamView(ConsoleMixin, TemplateView):
    required_capability = "staff"
    template_name = "analytics/team.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["people"] = PlatformStaff.objects.select_related("user", "added_by")
        ctx["labels"] = access.CAPABILITIES
        ctx["history"] = AuditEvent.objects.filter(action__startswith="console.").select_related("actor")[:30]
        return ctx


class TeamAddView(ConsoleMixin, TemplateView):
    required_capability = "staff"
    template_name = "analytics/team_form.html"

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
    template_name = "analytics/team_form.html"

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
