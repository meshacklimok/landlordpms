"""Thin views: forms in, services do the work (D-009).

A tenant outside the member's organization or visibility is a 404, never a 403.
"""

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views import View

from accounts.mixins import CapabilityRequiredMixin
from accounts.permissions import can
from core.phone import InvalidPhoneNumber, normalize_phone
from leases.models import Lease
from leases.services import visible_leases
from notifications import services as notifications
from notifications.catalog import SMS
from notifications.selectors import recent_for_tenant
from properties.views import _apply_errors

from . import forms, services
from .models import Tenant

PAGE_SIZE = 25


def _get_tenant(request, public_id) -> Tenant:
    qs = services.visible_tenants(request.membership, Tenant.all_objects.all())
    return get_object_or_404(qs, public_id=public_id)


def _duplicates(request, form, exclude_pk=None):
    """Other tenants with this phone, unless the user already confirmed."""
    if form.cleaned_data.get("confirm_duplicate"):
        return []
    phone = form.cleaned_data.get("phone")
    if not phone:
        return []
    return list(services.visible_tenants(request.membership, services.tenants_with_phone(
        request.organization, phone, exclude_pk=exclude_pk)).order_by("name")[:5])


def _form_context(request, form, tenant=None, duplicates=()):
    if duplicates:
        form.data = form.data.copy()
        form.data["confirm_duplicate"] = "on"
    return {"form": form, "tenant": tenant, "duplicates": duplicates}


class TenantListView(CapabilityRequiredMixin, View):
    template_name = "tenants/tenant_list.html"
    required_capability = "tenants.view"

    def get(self, request):
        search = forms.TenantSearchForm(request.GET)
        search.is_valid()
        q = search.cleaned_data.get("q", "").strip()
        status = search.cleaned_data.get("status", "")
        can_manage = can(request.membership, "tenants.manage")
        show_archived = search.cleaned_data.get("archived") and can_manage
        base = Tenant.all_objects.archived() if show_archived else Tenant.objects.all()
        qs = services.visible_tenants(request.membership, base)
        if status:
            qs = qs.filter(status=status)
        if q:
            match = Q(name__icontains=q) | Q(contact_person__icontains=q)
            try:
                phone = normalize_phone(q)
                match |= Q(phone=phone) | Q(alt_phone=phone)
            except InvalidPhoneNumber:
                pass
            if can(request.membership, "tenants.view_sensitive"):
                match |= Q(id_number__iexact=q.replace(" ", ""))
            qs = qs.filter(match)
        page = Paginator(qs.order_by("name", "pk"), PAGE_SIZE).get_page(request.GET.get("page"))
        return render(request, self.template_name, {
            "page": page, "q": q, "status": status, "show_archived": show_archived, "can_manage": can_manage,
            "status_choices": Tenant.Status.choices,
        })


class TenantCreateView(CapabilityRequiredMixin, View):
    template_name = "tenants/tenant_form.html"
    required_capability = "tenants.manage"

    def _form(self, request, data=None):
        return forms.TenantForm(data, show_sensitive=can(request.membership, "tenants.view_sensitive"))

    def get(self, request):
        return render(request, self.template_name, {"form": self._form(request)})

    def post(self, request):
        form = self._form(request, request.POST)
        if form.is_valid():
            duplicates = _duplicates(request, form)
            if duplicates:
                return render(request, self.template_name, _form_context(request, form, duplicates=duplicates))
            try:
                tenant = services.create_tenant(request.membership, request=request, **form.service_data())
            except ValidationError as exc:
                _apply_errors(form, exc)
            else:
                messages.success(request, _("Tenant added."))
                return redirect("tenants:detail", public_id=tenant.public_id)
        return render(request, self.template_name, _form_context(request, form))


class TenantDetailView(CapabilityRequiredMixin, View):
    template_name = "tenants/tenant_detail.html"
    required_capability = "tenants.view"

    def get(self, request, public_id):
        tenant = _get_tenant(request, public_id)
        m = request.membership
        leases = None
        if can(m, "leases.view"):
            leases = visible_leases(m, Lease.all_objects.filter(lease_tenants__tenant=tenant)).select_related(
                "unit__property")
        return render(request, self.template_name, {
            "tenant": tenant,
            "leases": leases,
            "today": timezone.localdate(),
            "can_manage": can(request.membership, "tenants.manage"),
            "show_sensitive": can(request.membership, "tenants.view_sensitive"),
            "sms_allowed": notifications.channel_allowed(tenant, SMS),
            "recent_messages": recent_for_tenant(tenant) if can(m, "messages.view") else None,
        })


class TenantEditView(CapabilityRequiredMixin, View):
    template_name = "tenants/tenant_form.html"
    required_capability = "tenants.manage"

    def _form(self, request, tenant, data=None):
        return forms.TenantForm(data, instance=tenant,
                                show_sensitive=can(request.membership, "tenants.view_sensitive"))

    def get(self, request, public_id):
        tenant = _get_tenant(request, public_id)
        if tenant.is_archived:
            return redirect("tenants:detail", public_id=tenant.public_id)
        return render(request, self.template_name, {"form": self._form(request, tenant), "tenant": tenant})

    def post(self, request, public_id):
        tenant = _get_tenant(request, public_id)
        form = self._form(request, tenant, request.POST)
        if form.is_valid():
            # The form never writes to the instance (_post_clean is off), so tenant.phone is the saved value.
            changed = form.cleaned_data["phone"] != tenant.phone
            duplicates = _duplicates(request, form, exclude_pk=tenant.pk) if changed else []
            if duplicates:
                return render(request, self.template_name, _form_context(request, form, tenant, duplicates))
            try:
                services.update_tenant(request.membership, tenant, request=request, **form.service_data())
            except ValidationError as exc:
                _apply_errors(form, exc)
            else:
                messages.success(request, _("Tenant updated."))
                return redirect("tenants:detail", public_id=tenant.public_id)
        tenant.refresh_from_db()
        return render(request, self.template_name, _form_context(request, form, tenant))


class TenantArchiveView(CapabilityRequiredMixin, View):
    required_capability = "tenants.manage"

    def post(self, request, public_id):
        tenant = _get_tenant(request, public_id)
        restore = request.POST.get("action") == "restore"
        try:
            if restore:
                services.restore_tenant(request.membership, tenant, request=request)
            else:
                services.archive_tenant(request.membership, tenant, request=request)
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
        else:
            messages.success(request, _("Tenant restored.") if restore else _("Tenant archived."))
        return redirect("tenants:detail", public_id=tenant.public_id)
