"""Tenancy letter pages (D-048), notice to vacate and demand letters (D-109). Thin views; services do the work.

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
from core.ids import uuid_or_none
from core.net import client_ip
from leases.models import Lease
from leases.services import visible_leases

from . import forms, handover, notices, services
from .models import NoticeLetter, TenancyLetter


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
                                       public_id=uuid_or_none(request.POST.get("letter")))
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


class NoticesView(CapabilityRequiredMixin, View):
    """Notice to vacate and demand letters for a tenancy: what each would say today, and those issued (D-109)."""

    template_name = "letters/notices.html"
    required_capability = notices.CAPABILITY

    def get_lease(self, request, public_id) -> Lease:
        qs = visible_leases(request.membership, Lease.all_objects.all()).select_related("unit__property")
        lease = services.latest_lease(get_object_or_404(qs, public_id=public_id))
        if not lease.organization.bills_rent:  # no tenancy law for estates, water vendors or garbage (D-130)
            raise Http404
        return lease

    def context(self, request, lease, **forms_in):
        org = request.organization
        vacate_problem = notices.vacate_problem(lease)
        vacate = demand = None
        if not vacate_problem:
            notice = notices.landlord_notice(lease)
            data = notices.vacate_facts(lease, notice, org=org)
            vacate = {"data": data, "paragraphs": notices.paragraphs(NoticeLetter.Kind.VACATE, data),
                      "concerns": notices.vacate_concerns(lease, notice), "notice": notice}
        demand_problem = notices.demand_problem(lease)
        if not demand_problem:
            data = notices.demand_facts(lease, org=org)
            demand = {"data": data, "paragraphs": notices.paragraphs(NoticeLetter.Kind.DEMAND, data),
                      "rows": notices.arrears_rows(data), "concerns": notices.demand_concerns(lease)}
        letters = list(notices.visible_notice_letters(request.membership)
                       .filter(lease__in=services.tenancy(lease))
                       .select_related("issued_by", "withdrawn_by", "proof"))
        for letter in letters:
            letter.warnings = notices.service_concerns(letter)
        return {
            "lease": lease, "unit": lease.unit, "property": lease.unit.property,
            "vacate_problem": vacate_problem, "vacate": vacate,
            "vacate_form": forms_in.get("vacate_form") or forms.VacateLetterForm(),
            "demand_problem": demand_problem, "demand": demand,
            "demand_form": forms_in.get("demand_form") or forms.DemandLetterForm(),
            "letters": letters, "withdraw_form": forms_in.get("withdraw_form") or forms.WithdrawForm(),
            "withdraw_id": forms_in.get("withdraw_id"),
        }

    def get(self, request, public_id):
        lease = self.get_lease(request, public_id)
        return render(request, self.template_name, self.context(request, lease))

    def post(self, request, public_id):
        lease = self.get_lease(request, public_id)
        m = request.membership
        action = request.POST.get("action")
        if action == "withdraw":
            letter = get_object_or_404(
                notices.visible_notice_letters(m).filter(lease__in=services.tenancy(lease)),
                public_id=uuid_or_none(request.POST.get("letter")))
            form = forms.WithdrawForm(request.POST)
            if form.is_valid():
                try:
                    notices.withdraw(m, letter, reason=form.cleaned_data["reason"], request=request)
                except ValidationError as exc:
                    form.add_error(None, exc)
                else:
                    messages.success(request, _("Letter %(number)s withdrawn.") % {"number": letter.number})
                    return redirect("letters:notices", public_id=lease.public_id)
            return render(request, self.template_name, self.context(
                request, lease, withdraw_form=form, withdraw_id=str(letter.public_id)), status=400)
        if action == "vacate":
            form, key = forms.VacateLetterForm(request.POST), "vacate_form"
        elif action == "demand":
            form, key = forms.DemandLetterForm(request.POST), "demand_form"
        else:
            return redirect("letters:notices", public_id=lease.public_id)
        if form.is_valid():
            try:
                if action == "vacate":
                    letter = notices.issue_vacate(m, lease, note=form.cleaned_data["note"],
                                                  print_reason=form.cleaned_data["print_reason"], request=request)
                else:
                    letter = notices.issue_demand(m, lease, pay_within_days=form.cleaned_data["pay_within_days"],
                                                  note=form.cleaned_data["note"], request=request)
            except ValidationError as exc:
                messages.error(request, " ".join(exc.messages))
                return redirect("letters:notices", public_id=lease.public_id)
            messages.success(request, _("Letter %(number)s issued. Print it, serve it, then record how it was "
                                        "served.") % {"number": letter.number})
            return redirect("letters:notices", public_id=lease.public_id)
        return render(request, self.template_name, self.context(request, lease, **{key: form}), status=400)


class HandoverView(CapabilityRequiredMixin, View):
    """The advocate handover pack (D-122): what it will hold and what to check, then the ZIP by POST."""

    template_name = "letters/handover.html"
    required_capability = handover.CAPABILITY

    def get_lease(self, request, public_id) -> Lease:
        qs = visible_leases(request.membership, Lease.all_objects.all()).select_related("unit__property")
        lease = services.latest_lease(get_object_or_404(qs, public_id=public_id))
        if not lease.organization.bills_rent or not can(request.membership, handover.CAPABILITY, lease.unit.property):
            raise Http404
        return lease

    def show(self, request, lease, form, status=200):
        purpose = form.data.get("purpose") if form.is_bound else request.GET.get("purpose")
        if purpose not in handover.Purpose.values:
            purpose = handover.Purpose.ARREARS
        problem = handover.problem(lease)
        data = None if problem else handover.summary(request.membership, lease, purpose=purpose)
        return render(request, self.template_name, {
            "lease": lease, "unit": lease.unit, "property": lease.unit.property, "problem": problem,
            "data": data, "form": form, "purpose": purpose}, status=status)

    def get(self, request, public_id):
        lease = self.get_lease(request, public_id)
        purpose = request.GET.get("purpose")
        initial = {"purpose": purpose} if purpose in handover.Purpose.values else {}
        return self.show(request, lease, forms.HandoverForm(initial=initial))

    def post(self, request, public_id):
        lease = self.get_lease(request, public_id)
        form = forms.HandoverForm(request.POST)
        if form.is_valid():
            try:
                filename, content = handover.build(request.membership, lease, request=request, **form.cleaned_data)
            except ValidationError as exc:
                form.add_error(None, exc)
            else:
                response = HttpResponse(content, content_type="application/zip")
                response["Content-Disposition"] = f'attachment; filename="{filename}"'
                response["Cache-Control"] = "private, no-store"
                return response
        return self.show(request, lease, form, status=400)


class NoticePdfView(CapabilityRequiredMixin, View):
    required_capability = notices.CAPABILITY

    def get(self, request, public_id):
        letter = get_object_or_404(notices.visible_notice_letters(request.membership), public_id=public_id)
        if not letter.pdf:
            raise Http404
        return FileResponse(letter.pdf.open("rb"), content_type="application/pdf",
                            as_attachment=request.GET.get("download") == "1", filename=f"{letter.number}.pdf")


class ServiceView(CapabilityRequiredMixin, View):
    """Records, or corrects, when and how a notice or demand was served (D-109 item 6)."""

    template_name = "letters/service.html"
    required_capability = notices.CAPABILITY

    def get_letter(self, request, public_id) -> NoticeLetter:
        letter = get_object_or_404(notices.visible_notice_letters(request.membership)
                                   .select_related("lease__unit__property", "proof"), public_id=public_id)
        if letter.is_withdrawn:
            raise Http404
        return letter

    def form(self, request, letter, data=None, files=None):
        from documents import services as documents

        initial = ({f: getattr(letter, f) for f in notices.SERVICE_FIELDS} if letter.is_served
                   else {"served_by_name": request.user.get_full_name()})
        return forms.ServiceForm(data, files, initial=initial,
                                 can_upload=documents.can_upload(request.membership, letter.lease))

    def show(self, request, letter, form, status=200):
        return render(request, self.template_name, {"letter": letter, "lease": letter.lease, "form": form},
                      status=status)

    def get(self, request, public_id):
        letter = self.get_letter(request, public_id)
        return self.show(request, letter, self.form(request, letter))

    def post(self, request, public_id):
        letter = self.get_letter(request, public_id)
        form = self.form(request, letter, request.POST, request.FILES)
        if form.is_valid():
            values = {f: form.cleaned_data[f] for f in notices.SERVICE_FIELDS}
            try:
                notices.record_service(request.membership, letter, **values,
                                       proof_file=form.cleaned_data.get("proof"), request=request)
            except ValidationError as exc:
                if hasattr(exc, "error_dict"):
                    for field, errs in exc.message_dict.items():
                        form.add_error(field if field in form.fields else None, errs)
                else:
                    form.add_error(None, exc)
            else:
                messages.success(request, _("Service of %(number)s recorded.") % {"number": letter.number})
                return redirect("letters:notices", public_id=letter.lease.public_id)
        return self.show(request, letter, form, status=400)


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
        notice = None if letter else NoticeLetter.objects.filter(verify_code=code).first()
        if letter is not None:
            response = render(request, self.template_name, {
                "letter": letter, "rows": services.statements(letter.facts),
                "title": _("Tenancy and payment record"), "organization": letter.facts["organization"]})
        elif notice is not None:
            response = render(request, self.template_name, {
                "letter": notice, "rows": notices.check_rows(notice), "title": notice.get_kind_display(),
                "organization": notice.facts["organization"]})
        else:
            response = render(request, "letters/check_unknown.html", status=404)
        response["X-Robots-Tag"] = "noindex, nofollow"
        response["Referrer-Policy"] = "no-referrer"
        return response
