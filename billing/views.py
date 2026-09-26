"""Charge types page. Thin views; billing.services does the work."""

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _
from django.views import View

from accounts.mixins import CapabilityRequiredMixin
from properties.views import _apply_errors

from . import forms, services
from .models import ChargeType


class ChargeTypeListView(CapabilityRequiredMixin, View):
    template_name = "billing/charge_types.html"
    required_capability = "charges.manage"

    def context(self, request, form=None):
        org = request.organization
        services.ensure_default_charge_types(org)
        return {
            "charge_types": ChargeType.objects.for_org(org),
            "archived": ChargeType.all_objects.for_org(org).archived(),
            "form": form or forms.ChargeTypeForm(),
        }

    def get(self, request):
        return render(request, self.template_name, self.context(request))

    def post(self, request):
        m = request.membership
        action = request.POST.get("action", "create")
        if action == "create":
            form = forms.ChargeTypeForm(request.POST)
            if form.is_valid():
                try:
                    services.create_charge_type(m, request=request, **form.cleaned_data)
                except ValidationError as exc:
                    _apply_errors(form, exc)
                else:
                    messages.success(request, _("Charge type added."))
                    return redirect("billing:charge_types")
            return render(request, self.template_name, self.context(request, form=form))
        ct = get_object_or_404(ChargeType.all_objects.for_org(request.organization),
                               public_id=request.POST.get("charge_type"))
        try:
            if action == "rename":
                services.rename_charge_type(m, ct, name=request.POST.get("name", ""), request=request)
                messages.success(request, _("Charge type renamed."))
            elif action == "archive":
                services.archive_charge_type(m, ct, request=request)
                messages.success(request, _("Charge type archived."))
            elif action == "restore":
                services.restore_charge_type(m, ct, request=request)
                messages.success(request, _("Charge type restored."))
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
        return redirect("billing:charge_types")
