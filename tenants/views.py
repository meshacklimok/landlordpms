"""Thin views: forms in, services do the work (D-009).

A tenant outside the member's organization or visibility is a 404, never a 403.
"""

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db.models import Exists, OuterRef, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views import View

from accounts.mixins import CapabilityRequiredMixin
from accounts.permissions import can, visible_properties
from core.ids import uuid_or_none
from core.phone import InvalidPhoneNumber, normalize_phone
from documents import services as documents
from leases.forms import TransferForm
from leases.models import Lease, LeaseTenant
from leases.services import open_successor, visible_leases, with_occupancy
from leases.views import _transfer_units
from notifications import services as notifications
from notifications.catalog import EMAIL, SMS, WHATSAPP
from notifications.delivery import whatsapp_available
from notifications.selectors import recent_for_tenant
from portal import views as portal_views
from properties.models import Property, Unit
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


def _assignable_units(membership):
    """Vacant units this member may start a lease on, or None when they may not draft leases."""
    if not can(membership, "leases.draft"):
        return None
    props = visible_properties(membership, Property.objects.all())
    units = (Unit.objects.for_org(membership.organization).filter(property__in=props)
             .exclude(manual_status=Unit.ManualStatus.INACTIVE).select_related("property", "building"))
    return with_occupancy(units).filter(is_occupied=False).order_by("property__name", "code")


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
        see_places = can(request.membership, "leases.view")
        placed = search.cleaned_data.get("placed", "") if see_places else ""
        if placed:
            in_unit = Exists(LeaseTenant.objects.filter(tenant=OuterRef("pk"), lease__status=Lease.Status.ACTIVE,
                                                        lease__start_date__lte=timezone.localdate()))
            qs = qs.filter(in_unit) if placed == "yes" else qs.exclude(in_unit)
        properties = prop = None
        if see_places:
            properties = visible_properties(request.membership, Property.objects.all()).order_by("name")
            wanted = search.cleaned_data.get("property")
            prop = next((p for p in properties if str(p.public_id) == wanted), None)
            if prop:
                qs = qs.filter(lease_links__lease__unit__property=prop).distinct()
        page = Paginator(qs.order_by("name", "pk"), PAGE_SIZE).get_page(request.GET.get("page"))
        if see_places:
            _attach_places(request.membership, page.object_list)
        return render(request, self.template_name, {
            "page": page, "q": q, "status": status, "show_archived": show_archived, "can_manage": can_manage,
            "status_choices": Tenant.Status.choices, "properties": properties, "prop": prop,
            "see_places": see_places, "placed": placed,
        })


def _attach_places(membership, tenants):
    """Set `t.places` on each tenant: where they live now (property · block · unit), else where they last lived.

    A tenant has no property of their own; it comes from their lease's unit (D-016).
    """
    tenants = list(tenants)
    links = LeaseTenant.objects.filter(tenant__in=tenants).exclude(lease__status=Lease.Status.DRAFT)
    leases = visible_leases(membership, Lease.all_objects.filter(lease_tenants__in=links)).select_related(
        "unit__property", "unit__building").prefetch_related("lease_tenants")
    today = timezone.localdate()
    by_tenant = {}
    for lease in leases.order_by("-start_date", "-pk"):
        current = lease.status == Lease.Status.ACTIVE and lease.start_date <= today
        for link in lease.lease_tenants.all():
            by_tenant.setdefault(link.tenant_id, []).append((current, lease))
    for t in tenants:
        found = by_tenant.get(t.pk, [])
        current = [lease for is_current, lease in found if is_current]
        t.places = current or found[:1]
        t.places_are_current = bool(current)


class TenantCreateView(CapabilityRequiredMixin, View):
    template_name = "tenants/tenant_form.html"
    required_capability = "tenants.manage"

    def _for_unit(self, request):
        """The unit whose lease form sent us here to add a tenant, so we can go back to it."""
        raw = uuid_or_none(request.POST.get("for_unit") or request.GET.get("for_unit"))
        m = request.membership
        if not raw or not can(m, "leases.draft"):
            return None
        props = visible_properties(m, Property.objects.all())
        return (Unit.objects.for_org(m.organization).filter(property__in=props, public_id=raw)
                .select_related("property").first())

    def _form(self, request, data=None, for_unit=None):
        return forms.TenantForm(data, show_sensitive=can(request.membership, "tenants.view_sensitive"),
                                units=None if for_unit else _assignable_units(request.membership))

    def _context(self, request, form, for_unit, **extra):
        return {**_form_context(request, form, **extra), "for_unit": for_unit}

    def get(self, request):
        for_unit = self._for_unit(request)
        return render(request, self.template_name, {"form": self._form(request, for_unit=for_unit),
                                                    "for_unit": for_unit})

    def post(self, request):
        for_unit = self._for_unit(request)
        form = self._form(request, request.POST, for_unit)
        if form.is_valid():
            duplicates = _duplicates(request, form)
            if duplicates:
                return render(request, self.template_name,
                              self._context(request, form, for_unit, duplicates=duplicates))
            try:
                tenant = services.create_tenant(request.membership, request=request, **form.service_data())
            except ValidationError as exc:
                _apply_errors(form, exc)
            else:
                unit = for_unit or form.cleaned_data.get("unit")
                if unit:
                    messages.success(request, _("Tenant added. Now set the lease for %(unit)s.") % {"unit": unit.code})
                    return redirect(reverse("leases:create", args=[unit.public_id]) + f"?tenant={tenant.public_id}")
                messages.success(request, _("Tenant added. Next, assign them to a unit to start their lease."))
                return redirect("tenants:detail", public_id=tenant.public_id)
        return render(request, self.template_name, self._context(request, form, for_unit))


class TenantDetailView(CapabilityRequiredMixin, View):
    template_name = "tenants/tenant_detail.html"
    required_capability = "tenants.view"

    def get(self, request, public_id):
        tenant = _get_tenant(request, public_id)
        m = request.membership
        leases = None
        if can(m, "leases.view"):
            leases = visible_leases(m, Lease.all_objects.filter(lease_tenants__tenant=tenant)).select_related(
                "unit__property", "unit__building")
        assign_units = move_lease = move_form = None
        today = timezone.localdate()
        if leases is not None and not tenant.is_archived:
            current = [ls for ls in leases if ls.status == Lease.Status.ACTIVE and ls.start_date <= today]
            if not current:
                assign_units = _assignable_units(m)
            else:
                lease = current[0]
                prop = lease.unit.property
                if (can(m, "leases.terminate", prop) and can(m, "leases.draft", prop)
                        and open_successor(lease) is None):
                    move_lease = lease
                    move_form = TransferForm(units=_transfer_units(m, lease), initial={"start_date": today})
        return render(request, self.template_name, {
            "tenant": tenant,
            "assign_units": assign_units, "move_lease": move_lease, "move_form": move_form,
            "leases": leases,
            "today": timezone.localdate(),
            "can_manage": can(request.membership, "tenants.manage"),
            "can_message": can(m, "messages.send") and not tenant.is_archived,
            "show_sensitive": can(request.membership, "tenants.view_sensitive"),
            "sms_allowed": notifications.channel_allowed(tenant, SMS),
            "whatsapp_offered": whatsapp_available(tenant.organization),
            "whatsapp_allowed": notifications.channel_allowed(tenant, WHATSAPP),
            "email_allowed": notifications.channel_allowed(tenant, EMAIL),
            "recent_messages": recent_for_tenant(tenant) if can(m, "messages.view") else None,
            "portal": portal_views.tenant_card(tenant) if can(m, "tenants.invite_portal") else None,
            "doc_panel": documents.panel(m, tenant),
        })


class TenantAssignView(CapabilityRequiredMixin, View):
    """Pick a vacant unit for a tenant, then go on to the draft lease form."""

    required_capability = "leases.draft"

    def get(self, request, public_id):
        tenant = _get_tenant(request, public_id)
        unit = (_assignable_units(request.membership) or Unit.objects.none()).filter(
            public_id=request.GET.get("unit") or None).first()
        if unit is None:
            messages.error(request, _("Choose a vacant unit."))
            return redirect("tenants:detail", public_id=tenant.public_id)
        return redirect(reverse("leases:create", args=[unit.public_id]) + f"?tenant={tenant.public_id}")


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
