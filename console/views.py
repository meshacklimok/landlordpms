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
from django.db.models import Count, Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils import timezone
from django.utils.http import urlencode
from django.utils.translation import gettext as _
from django.views.generic import TemplateView, View

from accounts import data_export, mfa
from accounts.mixins import VerifiedUserRequiredMixin
from accounts.models import DataExport, Membership, Organization, User
from audit import services as audit
from audit.models import AuditEvent
from core import exports, system_messages
from core.models import EmailRouting, SystemMessageSetting
from growth.models import Article, Referral, ReferralPolicy
from notifications import catalog
from notifications.models import NotificationTypeSetting
from notifications.providers.sms import send_sms
from portal.models import TenantAccount
from subscriptions import services as billing
from subscriptions.models import Subscription
from support import services as support_services
from support.models import SupportRequest

from . import access, chase, forms, insights, metrics, owners, problems, services
from . import today as today_page
from .models import (
    CannedReply,
    OwnerEmail,
    OwnerEmailRecipient,
    OwnerNote,
    PlatformAnnouncement,
    PlatformExpense,
    PlatformStaff,
)

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


class TodayView(ConsoleMixin, TemplateView):
    """What needs attention now. Open to every seat; each part appears only for the pages that seat may open."""

    template_name = "console/today.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["data"] = today_page.build(ctx["allowed"], superuser=self.request.user.is_superuser)
        return ctx


class OverviewView(ConsoleMixin, TemplateView):
    required_capability = "overview"
    template_name = "console/overview.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        signups = metrics.signups()
        funnel = metrics.funnel()
        if self.request.user.is_superuser:
            ctx["follow_ups_due"] = owners.due_count()
        ctx["problems_total"] = problems.total(problems.queue()) if "health" in ctx.get("allowed", ()) else None
        ctx.update(figures=metrics.overview(), signups=signups, funnel=funnel, cohorts=metrics.cohorts(),
                   specs={"signups": signups["chart"], "funnel": metrics.funnel_chart(funnel)})
        return ctx


class RevenueView(ConsoleMixin, TemplateView):
    required_capability = "revenue"
    template_name = "console/revenue.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        data, rent = metrics.revenue(), insights.rent_collected()
        ctx.update(data=data, rent=rent,
                   specs={"collected": data["chart"], "plans": data["plan_chart"], "rent": rent["chart"]})
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
        rows = metrics.organizations(query=data.get("q", ""), status=data.get("status", ""))
        today = timezone.localdate()
        for r in rows:
            why = metrics.risk_reasons(r, today)
            r["risk"] = sum(w for w, _text in why)
            r["health"] = metrics.health_label(r["risk"])
            r["reasons"] = [text for _w, text in why]
        key = self.request.GET.get("sort", "")
        keys = {"risk": lambda r: r["risk"], "month": lambda r: r["collected"], "ever": lambda r: r["ever"], "units": lambda r: r["units"],
                "leases": lambda r: r["leases"], "active": lambda r: r["last_seen"].timestamp() if r["last_seen"] else 0}
        if key in keys:
            rows.sort(key=keys[key], reverse=True)
        return form, rows

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        form, rows = self.rows()
        ctx.update(form=form, rows=rows, query=self.request.GET.urlencode(), sort=self.request.GET.get("sort", ""))
        return ctx


class WatchlistView(ConsoleMixin, TemplateView):
    required_capability = "organizations"
    template_name = "console/watchlist.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["rows"] = metrics.watchlist()
        return ctx


class ChaseView(ConsoleMixin, TemplateView):
    """Landlords with an unpaid subscription, to call or message before the account goes read-only."""

    required_capability = "organizations"
    template_name = "console/chase.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["rows"] = chase.rows()
        return ctx


class OrganizationsExportView(OrganizationsView):
    def get(self, request, *args, **kwargs):
        _form, rows = self.rows()
        response = exports.csv_response("organizations.csv")
        out = exports.writer(response)
        out.writerow(["Organization", "Type", "Status", "Plan", "Subscription", "Signed up", "Units", "Active leases",
                      "Staff", "Collected this month", "Collected ever", "On time %", "Matched on its own %", "Last active"])
        for r in rows:
            org = r["org"]
            out.writerow([org.name, org.get_org_type_display(), org.get_status_display(), r["plan"], r["sub_status"],
                          org.created_at.date().isoformat(), r["units"], r["leases"], r["staff"], r["collected"], r["ever"],
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
            ctx["access"] = forms.OrganizationAccessForm()
            ctx["sub"] = Subscription.objects.filter(organization=org).first()
            ctx["seats"] = (Membership.objects.filter(organization=org, is_active=True)
                            .select_related("user", "role").order_by("role__name", "user__full_name"))
            ctx["tenant_logins"] = (TenantAccount.objects.filter(organization=org, revoked_at__isnull=True,
                                                                 tenant__archived_at__isnull=True)
                                    .select_related("tenant", "user").order_by("tenant__id")[:200])
            ctx["login_people"] = (User.objects.filter(memberships__organization=org, is_active=True)
                                   .order_by("full_name").distinct())
            suspended = set(Membership.objects.filter(organization=org, is_active=False).values_list("user_id", flat=True))
            for p in ctx["login_people"]:
                p.suspended = p.pk in suspended
            sub = Subscription.objects.filter(organization=org).first()
            if sub is not None:
                own = sub.late_overrides or {}
                ctx["late_rows"] = [
                    {"key": key, "label": label, "own": own.get(key, "") if key in own else "",
                     "own_text": "never" if key in own and own[key] is None else (own[key] if key in own else ""),
                     "default": billing.policy_stop_days(key), "now": billing.stop_days(sub, key)}
                    for key, label in billing.STOPPABLE.items()]
                ctx["dunning_rows"] = [
                    {"key": key, "label": label, "value": "default" if key not in own else ("on" if own[key] else "off"),
                     "default": getattr(billing.policy(), key)}
                    for key, label in billing.DUNNING.items()]
                ctx["late_overdue"] = billing.days_overdue(sub)
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


class ViewAsStartView(OwnerMixin, View):
    """Opens the landlord's own pages as their owner sees them, read only, until stopped. Audited."""

    def post(self, request, public_id):
        from . import viewas

        org = get_object_or_404(Organization, public_id=public_id)
        if not Membership.objects.filter(organization=org, is_active=True, role__is_owner_role=True).exists():
            messages.error(request, _("This organization has no active owner to view as."))
            return redirect("console:organization", public_id=org.public_id)
        viewas.clear(request.session)
        seat = account = None
        if request.POST.get("membership"):
            seat = (Membership.objects.filter(organization=org, is_active=True, public_id=request.POST["membership"])
                    .select_related("role", "user").first())
        elif request.POST.get("account"):
            account = (TenantAccount.objects.filter(organization=org, revoked_at__isnull=True,
                                                    public_id=request.POST["account"]).select_related("tenant").first())
        if (request.POST.get("membership") and seat is None) or (request.POST.get("account") and account is None):
            messages.error(request, _("That person cannot be viewed."))
            return redirect("console:organization", public_id=org.public_id)
        request.session[viewas.SESSION_KEY] = str(org.public_id)
        if seat:
            request.session[viewas.MEMBER_KEY] = str(seat.public_id)
        if account:
            request.session[viewas.TENANT_KEY] = str(account.public_id)
        who = (f"{seat.user.full_name} ({seat.role.name})" if seat else f"{account.tenant} (tenant)" if account
               else org.name)
        audit.record("console.view_as", actor=request.user, organization=org, request=request,
                     changes={"started": [None, who]})
        return redirect("portal:home" if account else "accounts:home")


class ViewAsStopView(OwnerMixin, View):
    def post(self, request):
        from . import viewas

        public_id = request.session.get(viewas.SESSION_KEY)
        viewas.clear(request.session)
        org = Organization.objects.filter(public_id=public_id).first() if public_id else None
        if org:
            audit.record("console.view_as", actor=request.user, organization=org, request=request,
                         changes={"stopped": [org.name, None]})
        return redirect("console:organization", public_id=org.public_id) if org else redirect("console:overview")


class OrganizationAccessView(OwnerMixin, View):
    """Subscription by hand, grace days, sign everyone out, suspend or restore one seat (D-211)."""

    def post(self, request, public_id):
        org = get_object_or_404(Organization.objects.select_related("subscription"), public_id=public_id)
        form = forms.OrganizationAccessForm(request.POST)
        if form.is_valid():
            d = form.cleaned_data
            try:
                if d["action"] == "subscription":
                    services.set_subscription(request.user, org, plan=d["plan"], status=d["status"],
                                              interval=d["interval"], period_end=d["period_end"],
                                              reason=d["reason"], request=request)
                    messages.success(request, _("Subscription changed."))
                elif d["action"] == "grace":
                    sub = services.extend_grace(request.user, org, d["days"], d["reason"], request=request)
                    messages.success(request, _("No lapse before %(d)s.") % {"d": sub.grace_until})
                elif d["action"] == "signout":
                    n = services.sign_out_organization(request.user, org, d["reason"], request=request)
                    messages.success(request, _("%(n)s sessions ended.") % {"n": n})
                else:
                    person = get_object_or_404(User, pk=d["user"])
                    services.set_member_active(request.user, org, person, d["active"], d["reason"], request=request)
                    messages.success(request, _("Saved."))
            except ValidationError as exc:
                messages.error(request, " ".join(exc.messages))
        else:
            messages.error(request, _("Check the form: ") + " ".join(
                m for errs in form.errors.values() for m in errs))
        return redirect("console:organization", public_id=org.public_id)


class OrganizationLoginContactView(OwnerMixin, View):
    """A landlord who lost their phone number: the Platform Owner sets the new number and/or email (D-206)."""

    def post(self, request, public_id):
        org = get_object_or_404(Organization, public_id=public_id)
        form = forms.LoginContactForm(request.POST)
        if form.is_valid():
            d = form.cleaned_data
            user = get_object_or_404(User, pk=d["user"])
            try:
                services.change_login_contact(request.user, org, user, phone=d["phone"], email=d["email"],
                                              reset_mfa=d["reset_mfa"], reason=d["reason"], request=request)
                messages.success(request, _("Changed. They are signed out everywhere and verify the new number with "
                                            "a code when they sign in."))
            except ValidationError as exc:
                messages.error(request, " ".join(exc.messages))
        else:
            messages.error(request, _("Check the form: ") + " ".join(
                m for errs in form.errors.values() for m in errs))
        return redirect("console:organization", public_id=org.public_id)


class OrganizationLateRulesView(OwnerMixin, View):
    """This organization's own late-payment rules, over the platform's defaults (D-198)."""

    def post(self, request, public_id):
        org = get_object_or_404(Organization.objects.select_related("subscription"), public_id=public_id)
        form = forms.LateRulesForm(request.POST)
        if form.is_valid():
            try:
                billing.set_late_overrides(request.user, org, form.cleaned_data["values"],
                                           form.cleaned_data["reason"], request=request)
                messages.success(request, _("Saved. These rules apply to this organization from now on."))
            except ValidationError as exc:
                messages.error(request, " ".join(exc.messages))
        else:
            messages.error(request, _("Check the form: ") + " ".join(
                m for errs in form.errors.values() for m in errs))
        return redirect("console:organization", public_id=org.public_id)


class LatePolicyView(OwnerMixin, TemplateView):
    """What stops, and after how many days, while a landlord has not paid; and how they are reminded (D-198).
    These are the defaults for everyone; an organization's own page can override each one."""

    template_name = "console/late_policy.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["form"] = kwargs.get("form") or forms.LatePolicyForm(instance=billing.policy())
        ctx["overridden"] = Subscription.objects.exclude(late_overrides={}).count()
        return ctx

    def post(self, request, *args, **kwargs):
        form = forms.LatePolicyForm(request.POST, instance=billing.policy())
        if form.is_valid():
            obj = form.save()
            audit.record("platform.late_policy", actor=request.user, obj=obj, request=request,
                         changes={f: str(form.cleaned_data[f]) for f in form.changed_data})
            messages.success(request, _("Saved. It applies to every landlord without their own rules, from now on."))
            return redirect("console:late_policy")
        return self.render_to_response(self.get_context_data(form=form))


class AnnouncementsView(ConsoleMixin, TemplateView):
    """Notices to owners: write, edit, switch off, and choose who gets each one (superusers and staff with the
    notices page). A notice stays a month unless an end is chosen."""

    required_capability = "announce"
    template_name = "console/announcements.html"

    def _editing(self):
        pk = self.request.GET.get("edit")
        return get_object_or_404(PlatformAnnouncement, pk=pk) if pk and pk.isdigit() else None

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        editing = kwargs.get("editing") or self._editing()
        ctx["editing"] = editing
        ctx["form"] = kwargs.get("form") or (forms.AnnouncementForm.for_item(editing) if editing
                                             else forms.AnnouncementForm(initial={"level": "info"}))
        ctx["items"] = PlatformAnnouncement.objects.prefetch_related("organizations")[:30]
        ctx["now"] = timezone.now()
        return ctx

    def post(self, request, *args, **kwargs):
        if "toggle" in request.POST:
            item = get_object_or_404(PlatformAnnouncement, pk=request.POST.get("toggle"))
            services.switch_announcement(request.user, item, not item.is_active, request=request)
            return redirect("console:announcements")
        editing = None
        if request.POST.get("edit"):
            editing = get_object_or_404(PlatformAnnouncement, pk=request.POST["edit"])
        form = forms.AnnouncementForm(request.POST)
        if form.is_valid():
            d = form.cleaned_data
            ends = d["ends_at"]
            if ends and timezone.is_naive(ends):
                ends = timezone.make_aware(ends)
            starts = d["starts_at"]
            if starts and timezone.is_naive(starts):
                starts = timezone.make_aware(starts)
            args = {"message": d["message"], "level": d["level"], "ends_at": ends, "starts_at": starts,
                    "organizations": d["organizations"], "request": request}
            if editing:
                services.edit_announcement(request.user, editing, **args)
                messages.success(request, _("Saved."))
            else:
                services.announce(request.user, **args)
                messages.success(request, _("Sent to the chosen organizations.") if d["organizations"]
                                 else _("Shown to everyone signed in."))
            return redirect("console:announcements")
        return self.render_to_response(self.get_context_data(form=form, editing=editing))


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

    csv_limit = 50000

    def get(self, request, *args, **kwargs):
        if request.GET.get("format") != "csv":
            return super().get(request, *args, **kwargs)
        qs = self.events(forms.AuditFilterForm(request.GET))[: self.csv_limit]
        response = exports.csv_response("audit-log.csv")
        out = exports.writer(response)
        out.writerow(["When", "Who", "Organization", "Action", "On", "IP"])
        for e in qs:
            out.writerow([timezone.localtime(e.created_at).strftime("%Y-%m-%d %H:%M:%S"),
                          e.actor.full_name if e.actor else "", e.organization.name if e.organization else "",
                          e.action, e.object_repr, e.ip or ""])
        audit.record("console.export", actor=request.user, request=request,
                     changes={"what": [None, "audit log"]})
        return response

    def events(self, form):
        data = form.cleaned_data if form.is_valid() else {}
        qs = AuditEvent.objects.select_related("actor", "organization")
        if data.get("action"):
            qs = qs.filter(action__icontains=data["action"])
        if data.get("organization"):
            qs = qs.filter(organization__name__icontains=data["organization"])
        if data.get("actor"):
            qs = qs.filter(Q(actor__full_name__icontains=data["actor"]) | Q(actor__phone__icontains=data["actor"]))
        if data.get("since"):
            qs = qs.filter(created_at__date__gte=data["since"])
        if data.get("until"):
            qs = qs.filter(created_at__date__lte=data["until"])
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        form = forms.AuditFilterForm(self.request.GET)
        qs = self.events(form)
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
        ctx.update(data=data, geo=geo, funnel=metrics.funnel(), stuck=metrics.stuck(), specs={
            "growth": data["growth_chart"], "new": data["new_chart"], "roles": data["role_chart"],
            "sizes": data["size_chart"], "counties": geo["chart"]})
        return ctx


class ChannelsView(ConsoleMixin, TemplateView):
    required_capability = "channels"
    template_name = "console/channels.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        msgs, ai = insights.channels(), insights.ai_usage()
        ctx.update(msgs=msgs, ai=ai, categories=insights.message_categories(), specs={"messages": msgs["chart"], "ai": ai["chart"], "tokens": ai["token_chart"]})
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
                      "Assistant questions", "Tokens in", "Tokens out", "Assistant cost KES", "Total cost KES",
                      "Paid us KES", "Left over KES"])
        for r in data["rows"]:
            name = r["org"].name
            out.writerow([name, r["sms_parts"], r["sms_cost"], r["sms_billed"],
                          r["scans"], r["scan_cost"], r["questions"], r["tokens_in"], r["tokens_out"], r["ai_cost"],
                          r["total"], r["income"], r["margin"]])
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


class StatementView(ConsoleMixin, TemplateView):
    """Total income and total expenses (SMS, email, hosting and the rest), month by month. ?format=csv."""

    required_capability = "finance"
    template_name = "console/statement.html"

    def get(self, request, *args, **kwargs):
        if request.GET.get("format") != "csv":
            return super().get(request, *args, **kwargs)
        data = insights.statement()
        response = exports.csv_response("income-and-expenses.csv")
        out = exports.writer(response)
        out.writerow(["Month", "Income KES"] + [r["name"] for r in data["rows"]] + ["Expenses KES", "Net KES"])
        for i, row in enumerate(data["table"]):
            out.writerow([row["month"], row["income"]] + [r["values"][i] for r in data["rows"]]
                         + [row["expenses"], row["net"]])
        audit.record("console.export", actor=request.user, request=request,
                     changes={"what": [None, "income and expenses"]})
        return response

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        data = insights.statement()
        ctx.update(data=data, specs={"statement": data["chart"]})
        return ctx


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


class TeamActivityView(ConsoleMixin, TemplateView):
    """What each console team member changed or downloaded, from the audit log (only changes and exports are logged)."""

    required_capability = "staff"
    template_name = "console/team_activity.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        now = timezone.now()
        week, month = now - datetime.timedelta(days=7), now - datetime.timedelta(days=30)
        events = AuditEvent.objects.filter(action__startswith="console.", created_at__gte=month)
        people = []
        for seat in PlatformStaff.objects.select_related("user"):
            mine = events.filter(actor=seat.user)
            top = mine.filter(created_at__gte=week).values_list("action").annotate(n=Count("pk")).order_by("-n")[:4]
            people.append({"seat": seat, "week": mine.filter(created_at__gte=week).count(), "month": mine.count(),
                           "top": [(a.removeprefix("console."), n) for a, n in top]})
        who = self.request.GET.get("person", "")
        recent = events.select_related("actor", "organization")
        if who.isdigit():
            recent = recent.filter(actor_id=int(who))
        ctx.update(people=people, recent=recent[:100], person=who)
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
    FILTERS = ("q", "status", "plan", "org_type", "activity", "email", "segment", "step", "county", "sort")

    def filtered(self, pks=None):
        form = forms.OwnerFilterForm(self.request.POST if self.request.method == "POST" else self.request.GET)
        data = form.cleaned_data if form.is_valid() else {}
        params = {k: data.get(k, "") for k in self.FILTERS}
        return form, params, owners.rows(**{("query" if k == "q" else k): v for k, v in params.items()}, pks=pks)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        form, params, rows = self.filtered()
        data = owners.summary(rows)
        everyone = owners.rows(**{("query" if k == "q" else k): v for k, v in params.items() if k != "segment"})
        trend = owners.joined_chart(everyone)
        places = owners.locations(everyone)
        page = Paginator(rows, 50).get_page(self.request.GET.get("page"))
        query = self.request.GET.copy()
        query.pop("page", None)
        seg_query = query.copy()
        seg_query.pop("segment", None)
        ctx.update(form=form, page=page, data=data, count=len(rows), query=query.urlencode(), trend=trend,
                   segments=owners.segment_counts(everyone), segment=params["segment"],
                   segment_query=seg_query.urlencode(),
                   places=places,
                   specs={"joined": trend["chart"], "counties": places["chart"], "team": data["team_chart"], "top": data["top_chart"],
                          "plans": data["plan_chart"], "activity": data["activity_chart"]})
        return ctx


class OwnersExportView(OwnersView):
    def get(self, request, *args, **kwargs):
        _form, _params, rows = self.filtered()
        response = exports.csv_response("owners.csv")
        out = exports.writer(response)
        out.writerow(["Owner", "Email", "Phone", "Organization", "Kind", "Status", "Plan", "Subscription", "Units",
                      "Properties", "Active leases", "Tenants", "Team members", "Monthly value KES", "Health score", "Setup stuck at", "Needs attention",
                      "Joined", "Last active"])
        for r in rows:
            out.writerow([r["user"].full_name, r["email"], r["user"].phone, r["org"].name,
                          r["org"].get_org_type_display(), r["org"].get_status_display(), r["plan"], r["sub_status"],
                          r["units"], r["properties"], r["leases"], r["tenants"], r["team"], r["value"], r["score"], r["stuck_label"],
                          "; ".join(str(owners.SEGMENTS[f]) for f in r["flags"] if f not in ("no_mfa", "no_email")),
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
        if "subject" not in request.POST:  # arriving from the ticked list, not yet written
            if not self.picked():
                messages.error(request, _("Tick at least one owner first."))
                return redirect("console:owners")
            return self.render_to_response(self.get_context_data())
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
        if log.is_done:
            text = _("Sent to %(n)s owners.") % {"n": log.sent}
            if log.failed:
                text += " " + _("%(f)s could not be sent.") % {"f": log.failed}
        else:
            text = _("%(n)s emails are queued and go out in the next few minutes. "
                     "The page for this email shows how far it is.") % {"n": log.recipients}
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
        ctx.update(item=item, deliveries=item.deliveries.select_related("organization", "user"),
                   waiting=item.recipients - item.sent - item.failed)
        return ctx


class OwnerEmailResendView(OwnerMixin, View):
    """Tries again the addresses of one email that failed."""

    def post(self, request, pk):
        log = get_object_or_404(OwnerEmail, pk=pk)
        n = owners.requeue_failed(log, request.user, request=request)
        if n:
            messages.success(request, _("%(n)s addresses will be tried again in the next few minutes.") % {"n": n})
        else:
            messages.info(request, _("Nothing failed on this email."))
        return redirect("console:owner_email_detail", pk=log.pk)


class OwnerCreateView(OwnerMixin, TemplateView):
    template_name = "console/owner_form.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["form"] = kwargs.get("form") or forms.OwnerCreateForm()
        return ctx

    def post(self, request, *args, **kwargs):
        form = forms.OwnerCreateForm(request.POST)
        if form.is_valid():
            try:
                m = owners.create_owner(request.user, request=request, **form.cleaned_data)
            except ValidationError as exc:
                for field, errs in exc.message_dict.items():
                    form.add_error(field if field in form.fields else None, errs)
            else:
                messages.success(request, _("Owner added. They sign in with Forgot password on their phone number."))
                return redirect("console:owner", pk=m.public_id)
        return self.render_to_response(self.get_context_data(form=form))


class OrganizationBulkView(OwnerMixin, View):
    """One action for every ticked organization: a note, a few days of grace, or a text message to its owner."""

    LIMIT = 100

    def post(self, request):
        ids = []
        for value in request.POST.getlist("o"):
            try:
                ids.append(str(uuid.UUID(value)))
            except ValueError:
                continue
        action = request.POST.get("action", "")
        orgs = list(Organization.objects.filter(public_id__in=ids).select_related("subscription"))
        back = redirect(request.POST.get("next") if (request.POST.get("next") or "").startswith("/console/")
                        else "console:organizations")
        if not orgs:
            messages.error(request, _("Tick at least one organization first."))
            return back
        if len(orgs) > self.LIMIT:
            messages.error(request, _("Tick at most %(n)s organizations at a time.") % {"n": self.LIMIT})
            return back
        text = (request.POST.get("text") or "").strip()
        if action == "note":
            form = forms.OwnerNoteForm({"body": text, "follow_up_on": request.POST.get("follow_up_on") or ""})
            if not form.is_valid():
                messages.error(request, _("A note needs some words."))
                return back
            for org in orgs:
                owners.add_note(request.user, org, request=request, **form.cleaned_data)
            messages.success(request, _("Note added to %(n)s organizations.") % {"n": len(orgs)})
        elif action == "grace":
            done = 0
            for org in orgs:
                try:
                    services.extend_grace(request.user, org, int(request.POST.get("days") or 0),
                                          text or "Bulk extension", request=request)
                    done += 1
                except (ValidationError, ValueError):
                    continue
            messages.success(request, _("Grace given to %(n)s of %(t)s organizations.") % {"n": done, "t": len(orgs)})
        elif action == "sms":
            if not text:
                messages.error(request, _("Write the message first."))
                return back
            sent = 0
            for org in orgs:
                owner = (Membership.objects.filter(organization=org, is_active=True, role__is_owner_role=True)
                         .select_related("user").order_by("created_at").first())
                if owner is None or not owner.user.phone:
                    continue
                try:
                    ok = send_sms(owner.user.phone, text[:300]).ok
                except Exception:  # noqa: BLE001 - one bad number must not stop the rest
                    ok = False
                sent += ok
                audit.record("console.bulk_sms", actor=request.user, organization=org, request=request,
                             changes={"text": [None, text[:120]], "sent": [None, ok]})
            messages.success(request, _("Text sent to %(n)s of %(t)s owners.") % {"n": sent, "t": len(orgs)})
        else:
            messages.error(request, _("Choose what to do."))
        return back


class OwnerBulkView(OwnerMixin, View):
    """One note, and an optional follow-up date, added to every ticked owner."""

    LIMIT = 200

    def post(self, request):
        ids = []
        for value in request.POST.getlist("m"):
            try:
                ids.append(str(uuid.UUID(value)))
            except ValueError:
                continue
        form = forms.OwnerNoteForm(request.POST)
        if not ids:
            messages.error(request, _("Tick at least one owner first."))
        elif len(ids) > self.LIMIT:
            messages.error(request, _("Tick at most %(n)s owners at a time.") % {"n": self.LIMIT})
        elif not form.is_valid():
            messages.error(request, _("A note needs some words."))
        else:
            members = list(Membership.objects.filter(public_id__in=ids, is_active=True, role__is_owner_role=True)
                           .select_related("organization"))
            n = owners.add_notes(request.user, members, request=request, **form.cleaned_data)
            messages.success(request, _("Note added to %(n)s owners.") % {"n": n})
        return redirect(request.POST.get("next") if (request.POST.get("next") or "").startswith("/console/")
                        else "console:owners")


class OwnerFollowUpsView(OwnerMixin, TemplateView):
    """Every open note with a date, across all owners: what to do today, and what is overdue."""

    template_name = "console/owner_follow_ups.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx.update(data=owners.follow_ups(), today=timezone.localdate())
        return ctx

    def post(self, request, *args, **kwargs):
        note = get_object_or_404(OwnerNote, pk=request.POST.get("note") or 0)
        owners.mark_note(request.user, note, done=request.POST.get("action") != "reopen", request=request)
        return redirect("console:owner_follow_ups")


class ProblemsView(ConsoleMixin, TemplateView):
    """Failed messages, failed webhook deliveries and money left unmatched, each with a retry."""

    required_capability = "health"
    template_name = "console/problems.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        data = problems.queue()
        ctx.update(data=data, total=problems.total(data))
        return ctx

    def post(self, request, *args, **kwargs):
        if "fix" not in access.allowed(request.user):
            raise PermissionDenied("fix")
        what = request.POST.get("what", "")
        if what == "message":
            status = problems.retry_message(request.user, request.POST.get("id") or 0, request=request)
            messages.success(request, _("Sent again.") if status in ("SENT", "DELIVERED") else
                             _("Tried again; it is %(s)s.") % {"s": (status or "no longer failed").lower()})
        elif what == "webhook":
            status = problems.retry_webhook(request.user, request.POST.get("id") or uuid.UUID(int=0),
                                            request=request)
            messages.success(request, _("Delivered.") if status == "SENT" else
                             _("Tried again; it is %(s)s.") % {"s": (status or "no longer failed").lower()})
        elif what in ("messages", "webhooks"):
            r = problems.retry_all(request.user, what, request=request)
            messages.success(request, _("%(ok)s of %(n)s went through.") % {"ok": r["ok"], "n": r["tried"]})
        return redirect("console:problems")


class SupportView(ConsoleMixin, TemplateView):
    """What landlords send through the support form: questions, problems, import help. ?status= and ?kind= filter."""

    required_capability = "support"
    template_name = "console/support.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        status = self.request.GET.get("status", "open")
        kind = self.request.GET.get("kind", "")
        qs = SupportRequest.objects.select_related("organization", "created_by")
        if status == "open":
            qs = qs.exclude(status=SupportRequest.Status.CLOSED)
        elif status in SupportRequest.Status.values:
            qs = qs.filter(status=status)
        if kind in SupportRequest.Kind.values:
            qs = qs.filter(kind=kind)
        ctx.update(speed=insights.support_speed(), items=qs[:200], status=status, kind=kind, kinds=SupportRequest.Kind.choices,
                   statuses=SupportRequest.Status.choices,
                   open_count=SupportRequest.objects.exclude(status=SupportRequest.Status.CLOSED).count())
        return ctx


class CannedRepliesView(ConsoleMixin, TemplateView):
    """Saved answers to the questions we get most, for the support reply box."""

    required_capability = "support_solve"
    template_name = "console/canned_replies.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["items"] = CannedReply.objects.all()
        return ctx

    def post(self, request, *args, **kwargs):
        if request.POST.get("delete"):
            item = get_object_or_404(CannedReply, pk=request.POST["delete"])
            audit.record("console.canned_reply", actor=request.user, obj=item, request=request,
                         changes={"deleted": [item.title, None]})
            item.delete()
            messages.success(request, _("Deleted."))
        else:
            title, body = request.POST.get("title", "").strip()[:80], request.POST.get("body", "").strip()[:2000]
            if not title or not body:
                messages.error(request, _("A saved reply needs a title and some words."))
            else:
                item = CannedReply.objects.create(title=title, body=body, created_by=request.user)
                audit.record("console.canned_reply", actor=request.user, obj=item, request=request,
                             changes={"added": [None, title]})
                messages.success(request, _("Saved."))
        return redirect("console:canned_replies")


class SupportItemView(ConsoleMixin, TemplateView):
    required_capability = "support"
    template_name = "console/support_item.html"

    def item(self) -> SupportRequest:
        return get_object_or_404(SupportRequest.objects.select_related("organization", "created_by"),
                                 public_id=self.kwargs["public_id"])

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx.update(item=self.item(), replies=self.item().replies.select_related("sent_by"), canned=CannedReply.objects.all(),
                   statuses=SupportRequest.Status.choices, can_solve="support_solve" in access.allowed(self.request.user))
        return ctx

    def post(self, request, *args, **kwargs):
        if "support_solve" not in access.allowed(request.user):
            raise PermissionDenied("support_solve")
        item = self.item()
        if request.POST.get("action") == "reply":
            try:
                row = support_services.reply(request.user, item, request.POST.get("body", ""), request=request)
            except ValidationError as exc:
                messages.error(request, exc.messages[0])
            else:
                messages.success(request, _("Sent by %(c)s.") % {"c": row.get_channel_display().lower()} if row.sent
                                 else _("It could not be sent. It is saved here; try again."))
            return redirect("console:support_item", public_id=item.public_id)
        status = request.POST.get("status", item.status)
        if status not in SupportRequest.Status.values:
            status = item.status
        before = {"status": item.status, "internal_note": item.internal_note}
        item.status = status
        item.internal_note = request.POST.get("internal_note", "")[:5000]
        if status == SupportRequest.Status.CLOSED and item.closed_at is None:
            item.closed_at = timezone.now()
        elif status != SupportRequest.Status.CLOSED:
            item.closed_at = None
        item.save(update_fields=["status", "internal_note", "closed_at", "updated_at"])
        audit.record("console.support", actor=request.user, organization=item.organization, obj=item, request=request,
                     changes={k: [v, getattr(item, k)] for k, v in before.items() if v != getattr(item, k)})
        messages.success(request, _("Saved."))
        return redirect("console:support_item", public_id=item.public_id)


class ToolsView(ConsoleMixin, TemplateView):
    """Where errors, product use and website visits are watched: links out, and whether each is switched on."""

    required_capability = "tools"
    template_name = "console/tools.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["tools"] = [
            {"name": "Sentry", "what": _("Errors and slow pages: every crash with the page and the line that failed."),
             "on": bool(settings.SENTRY_DSN), "url": settings.SENTRY_DASHBOARD_URL, "setting": "SENTRY_DSN"},
            {"name": "PostHog", "what": _("Which features landlords use and where they stop."),
             "on": bool(settings.POSTHOG_API_KEY), "url": settings.POSTHOG_DASHBOARD_URL,
             "setting": "POSTHOG_API_KEY"},
            {"name": "Google Analytics", "what": _("Visits to the public website: where they come from, which pages."),
             "on": bool(settings.GOOGLE_ANALYTICS_URL), "url": settings.GOOGLE_ANALYTICS_URL,
             "setting": "GOOGLE_ANALYTICS_URL"},
        ]
        return ctx


class OwnerView(OwnerMixin, TemplateView):
    """One owner: their figures, their details (change them), notes with follow-ups, and what we emailed them."""

    template_name = "console/owner.html"

    def membership(self):
        return get_object_or_404(
            Membership.objects.select_related("user", "organization"), public_id=self.kwargs["pk"],
            is_active=True, role__is_owner_role=True)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        m = self.membership()
        row = owners.rows(pks=[m.public_id])[0]
        initial = {"full_name": m.user.full_name, "phone": m.user.phone, "email": m.user.email or "",
                   "organization": m.organization.name}
        ctx.update(
            m=m, row=row, org=m.organization, today=timezone.localdate(),
            form=kwargs.get("form") or forms.OwnerEditForm(initial=initial),
            note_form=kwargs.get("note_form") or forms.OwnerNoteForm(),
            notes=OwnerNote.objects.filter(organization=m.organization).select_related("author"),
            editing=kwargs.get("editing"),
            emails=OwnerEmailRecipient.objects.filter(organization=m.organization).select_related("email_log")[:20],
            status_choices=Organization.Status.choices, timeline=owners.timeline(m.organization))
        return ctx

    def post(self, request, *args, **kwargs):
        m = self.membership()
        action = request.POST.get("action", "")
        me = request.user
        if action == "save":
            form = forms.OwnerEditForm(request.POST)
            if form.is_valid():
                try:
                    changed = owners.update_owner(me, m, request=request, **form.cleaned_data)
                except ValidationError as exc:
                    for field, errs in exc.message_dict.items():
                        form.add_error(field if field in form.fields else None, errs)
                else:
                    messages.success(request, _("Saved.") if changed else _("Nothing to change."))
                    return redirect("console:owner", pk=m.public_id)
            return self.render_to_response(self.get_context_data(form=form))
        if action == "note_add":
            form = forms.OwnerNoteForm(request.POST)
            if form.is_valid():
                owners.add_note(me, m.organization, request=request, **form.cleaned_data)
                messages.success(request, _("Note added."))
                return redirect("console:owner", pk=m.public_id)
            return self.render_to_response(self.get_context_data(note_form=form))
        note = get_object_or_404(OwnerNote, pk=request.POST.get("note") or 0, organization=m.organization)
        if action == "note_save":
            form = forms.OwnerNoteForm(request.POST)
            if form.is_valid():
                owners.edit_note(me, note, request=request, **form.cleaned_data)
                messages.success(request, _("Note saved."))
            else:
                messages.error(request, _("A note needs some words."))
        elif action == "note_done":
            owners.mark_note(me, note, done=True, request=request)
        elif action == "note_reopen":
            owners.mark_note(me, note, done=False, request=request)
        elif action == "note_delete":
            owners.delete_note(me, note, request=request)
            messages.success(request, _("Note deleted."))
        return redirect("console:owner", pk=m.public_id)


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


class EmailPreviewSendView(ConsoleMixin, View):
    """Sends a sample to the signed-in person, to see it in a real inbox on a phone."""

    def post(self, request, key):
        from core import ratelimit
        from notifications import email_previews

        if not request.user.email:
            messages.error(request, _("Add an email address to your account first."))
        elif not ratelimit.hit(f"email_preview:{request.user.pk}", 20, 3600):
            messages.error(request, _("Too many test emails. Try again in an hour."))
        else:
            try:
                sent = email_previews.send_test(key, request.user.email)
            except Exception:  # noqa: BLE001 - the provider's error is not for this page
                logger.exception("Test email %s could not be sent", key)
                messages.error(request, _("The test email could not be sent. Check the email account on this page."))
            else:
                if not sent:
                    raise Http404
                messages.success(request, _("Sent to %(to)s.") % {"to": request.user.email})
        return redirect("console:email_previews")


class ArticlesView(ConsoleMixin, TemplateView):
    required_capability = "articles"
    template_name = "console/articles.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["items"] = Article.objects.order_by("-created_at")
        return ctx


class ArticleEditView(ConsoleMixin, TemplateView):
    """Write a new article (no pk) or edit one. Save keeps a draft a draft; Publish puts it on the public site."""

    required_capability = "articles"
    template_name = "console/article_form.html"

    def article(self):
        pk = self.kwargs.get("pk")
        return get_object_or_404(Article, pk=pk) if pk else None

    def get_context_data(self, **kwargs):
        article = self.article()
        kwargs.setdefault("form", forms.ArticleForm(instance=article))
        return super().get_context_data(article=article, **kwargs)

    def post(self, request, *args, **kwargs):
        article = self.article()
        if "delete" in request.POST and article:
            try:
                services.delete_article(request.user, article, request=request)
            except ValidationError as e:
                messages.error(request, e.messages[0])
                return redirect("console:article", article.pk)
            messages.success(request, _("Deleted."))
            return redirect("console:articles")
        form = forms.ArticleForm(request.POST, instance=article)
        if not form.is_valid():
            return self.render_to_response(self.get_context_data(form=form))
        action = "publish" if "publish" in request.POST else "unpublish" if "unpublish" in request.POST else "save"
        saved = services.save_article(request.user, form, action, request=request)
        messages.success(request, {"publish": _("Published. It is on the public site now."),
                                   "unpublish": _("Taken off the public site. It is a draft again.")}.get(
                                       action, _("Saved.")))
        if "preview" in request.POST:
            return redirect("console:article_preview", saved.pk)
        return redirect("console:article", saved.pk)


class ArticlePreviewView(ConsoleMixin, View):
    """The saved article as visitors see it, drafts included. Only for people with the articles page."""

    required_capability = "articles"

    def get(self, request, pk):
        from django.shortcuts import render

        from growth.views import article_context
        item = get_object_or_404(Article, pk=pk)
        if item.published_at is None:
            item.published_at = timezone.now()
        return render(request, "growth/article.html", article_context(item, preview=True))
