"""Tenancy letter pages (D-048). Thin views; services do the work.

Anything outside the member's properties is a 404, never a 403.
"""

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _
from django.views import View

from accounts.mixins import CapabilityRequiredMixin
from accounts.permissions import can
from core import ratelimit
from core.net import client_ip
from leases.models import Lease
from leases.services import visible_leases

from . import forms, services
from .models import TenancyLetter


class LeaseLettersView(CapabilityRequiredMixin, View):
    """What a letter would say today, the letters already issued for the tenancy, and the issue button."""

    template_name = "letters/lease.html"
    required_capability = services.CAPABILITY

    def get_lease(self, request, public_id) -> Lease:
        qs = visible_leases(request.membership, Lease.all_objects.all()).select_related("unit__property")
        return services.latest_lease(get_object_or_404(qs, public_id=public_id))

    def context(self, request, lease, withdraw_form=None, withdraw_id=None):
        problem = services.issue_problem(lease)
        preview = None if problem else services.facts(lease, org=request.organization)
        return {
            "lease": lease, "unit": lease.unit, "property": lease.unit.property, "problem": problem,
            "rows": services.statements(preview) if preview else [],
            "concerns": services.concerns(preview) if preview else [],
            "letters": (services.visible_letters(request.membership)
                        .filter(lease__in=services.tenancy(lease)).select_related("issued_by", "withdrawn_by")),
            "withdraw_form": withdraw_form or forms.WithdrawForm(), "withdraw_id": withdraw_id,
            "can_settings": can(request.membership, "organization.manage"),
        }

    def get(self, request, public_id):
        lease = self.get_lease(request, public_id)
        return render(request, self.template_name, self.context(request, lease))

    def post(self, request, public_id):
        lease = self.get_lease(request, public_id)
        m = request.membership
        if request.POST.get("action") == "withdraw":
            letter = get_object_or_404(services.visible_letters(m).filter(lease__in=services.tenancy(lease)),
                                       public_id=request.POST.get("letter") or None)
            form = forms.WithdrawForm(request.POST)
            if form.is_valid():
                try:
                    services.withdraw(m, letter, reason=form.cleaned_data["reason"], request=request)
                except ValidationError as exc:
                    form.add_error(None, exc)
                else:
                    messages.success(request, _("Letter %(number)s withdrawn.") % {"number": letter.number})
                    return redirect("letters:lease", public_id=lease.public_id)
            return render(request, self.template_name,
                          self.context(request, lease, withdraw_form=form, withdraw_id=str(letter.public_id)),
                          status=400)
        try:
            letter = services.issue(m, lease, request=request)
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
            return redirect("letters:lease", public_id=lease.public_id)
        messages.success(request, _("Letter %(number)s issued.") % {"number": letter.number})
        return redirect("letters:lease", public_id=lease.public_id)


class LetterPdfView(CapabilityRequiredMixin, View):
    required_capability = services.CAPABILITY

    def get(self, request, public_id):
        letter = get_object_or_404(services.visible_letters(request.membership), public_id=public_id)
        if not letter.pdf:
            raise Http404
        return FileResponse(letter.pdf.open("rb"), content_type="application/pdf",
                            as_attachment=request.GET.get("download") == "1", filename=f"{letter.number}.pdf")


class SettingsView(CapabilityRequiredMixin, View):
    template_name = "letters/settings.html"
    required_capability = "organization.manage"

    def get(self, request):
        org = request.organization
        form = forms.SettingsForm(initial={f: getattr(org, f) for f in services.SETTINGS})
        return render(request, self.template_name, {"form": form, "next": _safe_next(request)})

    def post(self, request):
        form = forms.SettingsForm(request.POST)
        form.is_valid()  # every field is an optional checkbox
        services.save_settings(request.membership, request=request, **form.cleaned_data)
        messages.success(request, _("Letter settings saved."))
        return redirect(_safe_next(request) or "letters:settings")


def _safe_next(request) -> str:
    """Only a path on this site, so the settings page can send the owner back to the lease."""
    nxt = request.POST.get("next") or request.GET.get("next") or ""
    return nxt if nxt.startswith("/") and not nxt.startswith("//") and "\\" not in nxt else ""


CHECK_RATE_LIMIT = 30  # views per IP per minute


class CheckView(View):
    """The public check page behind the code printed on a letter. No login: the code is the key."""

    template_name = "letters/check.html"

    def get(self, request, code):
        if not ratelimit.hit(f"letter_check:ip:{client_ip(request)}", CHECK_RATE_LIMIT, 60):
            return HttpResponse(_("Too many requests. Try again in a minute."), status=429)
        letter = TenancyLetter.objects.filter(verify_code=code).first()
        if letter is None:
            response = render(request, "letters/check_unknown.html", status=404)
        else:
            response = render(request, self.template_name, {
                "letter": letter, "rows": services.statements(letter.facts),
                "organization": letter.facts["organization"]})
        response["X-Robots-Tag"] = "noindex, nofollow"
        response["Referrer-Policy"] = "no-referrer"
        return response
