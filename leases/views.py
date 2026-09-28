"""Thin views: forms in, services do the work (D-009).

A lease is visible when its unit's property is. Anything else is a 404, never a 403.
"""

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db.models import Prefetch, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views import View

from accounts.mixins import CapabilityRequiredMixin
from accounts.permissions import can, visible_properties
from billing import deposits, invoicing
from billing.services import recurring_charge_types
from properties.models import Property, Unit
from properties.views import _apply_errors, _get_unit
from tenants.models import Tenant
from tenants.services import visible_tenants

from . import forms, services
from .models import Lease, LeaseCharge, LeasePayer, LeaseTenant

PAGE_SIZE = 25


def _with_tenants(qs):
    return qs.select_related("unit__property").prefetch_related(
        Prefetch("lease_tenants", queryset=LeaseTenant.objects.select_related("tenant"))
    )


def _get_lease(request, public_id) -> Lease:
    qs = services.visible_leases(request.membership, Lease.all_objects.all())
    return get_object_or_404(_with_tenants(qs), public_id=public_id)


def _tenant_choices(request):
    return visible_tenants(request.membership).order_by("name")


def _transfer_units(membership, lease):
    props = visible_properties(membership, Property.objects.all())
    return (Unit.objects.for_org(membership.organization).filter(property__in=props)
            .exclude(pk=lease.unit_id).exclude(manual_status=Unit.ManualStatus.INACTIVE)
            .select_related("property").order_by("property__name", "code"))


class LeaseListView(CapabilityRequiredMixin, View):
    template_name = "leases/lease_list.html"
    required_capability = "leases.view"

    def get(self, request):
        search = forms.LeaseSearchForm(request.GET)
        search.is_valid()
        q = search.cleaned_data.get("q", "").strip()
        status = search.cleaned_data.get("status", "")
        qs = services.visible_leases(request.membership)
        if status:
            qs = qs.filter(status=status)
        if q:
            qs = qs.filter(
                Q(number__icontains=q) | Q(unit__code__icontains=q) | Q(unit__payment_reference__icontains=q)
                | Q(unit__property__name__icontains=q) | Q(lease_tenants__tenant__name__icontains=q)
            ).distinct()
        page = Paginator(_with_tenants(qs).order_by("-start_date", "-pk"), PAGE_SIZE).get_page(request.GET.get("page"))
        return render(request, self.template_name, {
            "page": page, "q": q, "status": status, "status_choices": Lease.Status.choices,
            "today": timezone.localdate(),
        })


class LeaseCreateView(CapabilityRequiredMixin, View):
    """A new draft lease for a unit, started from the unit page."""

    template_name = "leases/lease_form.html"
    required_capability = "leases.draft"

    def _unit(self, request, public_id):
        unit = _get_unit(request, public_id)
        if not can(request.membership, "leases.draft", unit.property):
            raise PermissionDenied("leases.draft")
        return unit

    def get(self, request, public_id):
        unit = self._unit(request, public_id)
        initial = {"start_date": timezone.localdate()}
        if unit.list_rent:
            initial["rent"] = unit.list_rent
        tenant = request.GET.get("tenant")
        if tenant:
            initial["tenant"] = _tenant_choices(request).filter(public_id=tenant).first()
        form = forms.LeaseForm(initial=initial, tenants=_tenant_choices(request))
        return render(request, self.template_name, {"form": form, "unit": unit})

    def post(self, request, public_id):
        unit = self._unit(request, public_id)
        form = forms.LeaseForm(request.POST, tenants=_tenant_choices(request))
        if form.is_valid():
            data = dict(form.cleaned_data)
            tenants = [data.pop("tenant"), *data.pop("co_tenants")]
            try:
                lease = services.create_lease(request.membership, unit=unit, tenants=tenants, request=request, **data)
            except ValidationError as exc:
                _apply_errors(form, exc)
            else:
                messages.success(request, _("Draft lease saved. Add charges and payers, then activate it."))
                return redirect("leases:detail", public_id=lease.public_id)
        return render(request, self.template_name, {"form": form, "unit": unit})


class LeaseEditView(CapabilityRequiredMixin, View):
    template_name = "leases/lease_form.html"
    required_capability = "leases.draft"

    def _lease(self, request, public_id):
        lease = _get_lease(request, public_id)
        if not can(request.membership, "leases.draft", lease.unit.property):
            raise PermissionDenied("leases.draft")
        return lease

    def get(self, request, public_id):
        lease = self._lease(request, public_id)
        if not lease.is_draft:
            return redirect("leases:detail", public_id=lease.public_id)
        form = forms.LeaseForm(initial=forms.LeaseForm.initial_for(lease), tenants=None, with_tenants=False)
        return render(request, self.template_name, {"form": form, "unit": lease.unit, "lease": lease})

    def post(self, request, public_id):
        lease = self._lease(request, public_id)
        form = forms.LeaseForm(request.POST, tenants=None, with_tenants=False)
        if form.is_valid():
            try:
                services.update_draft_lease(request.membership, lease, request=request, **form.cleaned_data)
            except ValidationError as exc:
                lease.refresh_from_db()
                _apply_errors(form, exc)
            else:
                messages.success(request, _("Lease saved."))
                return redirect("leases:detail", public_id=lease.public_id)
        return render(request, self.template_name, {"form": form, "unit": lease.unit, "lease": lease})


class LeaseDetailView(CapabilityRequiredMixin, View):
    """The lease with its tenants, rent history, charges and payers. Small forms post back here."""

    template_name = "leases/lease_detail.html"
    required_capability = "leases.view"

    def context(self, request, lease, **forms_in):
        m, prop = request.membership, lease.unit.property
        is_open = lease.status in services.OPEN_STATUSES
        is_active = lease.status == Lease.Status.ACTIVE
        can_draft = can(m, "leases.draft", prop)
        can_charges = is_open and (can(m, "charges.manage", prop) or (lease.is_draft and can_draft))
        can_terminate = is_active and can(m, "leases.terminate", prop)
        on_lease = [lt.tenant_id for lt in lease.lease_tenants.all()]
        predecessor = services.closing_predecessor(lease)
        successor = services.open_successor(lease) if is_active else None
        ctx = {
            "lease": lease,
            "unit": lease.unit,
            "property": prop,
            "today": timezone.localdate(),
            "rent_changes": lease.rent_changes.all(),
            "charges": lease.charges.select_related("charge_type"),
            "payers": lease.payers.all(),
            "can_edit_draft": lease.is_draft and can_draft,
            "can_payers": services.can_manage_parties(m, lease),
            "can_charges": can_charges,
            "can_change_rent": lease.status == Lease.Status.ACTIVE and can(m, "leases.change_rent", prop),
            "can_see_tenants": can(m, "tenants.view"),
            "overlap": services.overlapping_lease(lease, ignore=predecessor) if lease.is_draft else None,
            "predecessor": predecessor,
            "predecessor_ends": lease.start_date - services.DAY if predecessor else None,
            "successor": successor,
            "next_leases": Lease.all_objects.filter(previous_lease=lease).exclude(status=Lease.Status.DRAFT),
            "can_activate": lease.is_draft and can(m, "leases.activate", prop),
            "can_terminate": can_terminate,
            "can_renew": is_active and can_draft and successor is None,
            "can_transfer": can_terminate and successor is None,
        }
        if not lease.is_draft and can(m, "invoices.view", prop):
            ctx["account"] = {"balance": invoicing.lease_balance(lease), "deposit_held": deposits.held(lease)}
        if not lease.is_draft and lease.archived_at is None and can(m, "payments.record", prop):
            from mpesa.stk import stk_accounts

            ctx["can_request_mpesa"] = bool(stk_accounts(lease))
        if ctx["can_terminate"]:
            ctx["notice_form"] = forms_in.get("notice_form") or forms.NoticeForm(
                initial={"given_on": ctx["today"].isoformat()})
            ctx["end_form"] = forms_in.get("end_form") or forms.EndLeaseForm(
                initial={"ended_on": min(ctx["today"], lease.move_out_by or ctx["today"]).isoformat()})
        if ctx["can_renew"]:
            ctx["renew_form"] = forms_in.get("renew_form") or forms.RenewForm()
        if ctx["can_transfer"]:
            ctx["transfer_form"] = forms_in.get("transfer_form") or forms.TransferForm(units=_transfer_units(m, lease))
        if ctx["can_edit_draft"]:
            ctx["tenant_form"] = forms_in.get("tenant_form") or forms.AddTenantForm(
                tenants=_tenant_choices(request).exclude(pk__in=on_lease))
        if can_charges:
            ctx["charge_form"] = forms_in.get("charge_form") or forms.ChargeForm(
                charge_types=recurring_charge_types(lease.organization))
        if ctx["can_payers"]:
            ctx["payer_form"] = forms_in.get("payer_form") or forms.PayerForm()
        if ctx["can_change_rent"]:
            ctx["rent_form"] = forms_in.get("rent_form") or forms.RentChangeForm()
        return ctx

    def get(self, request, public_id):
        lease = _get_lease(request, public_id)
        return render(request, self.template_name, self.context(request, lease))

    def post(self, request, public_id):
        lease = _get_lease(request, public_id)
        m = request.membership
        action = request.POST.get("action", "")
        handler = getattr(self, f"do_{action}", None)
        if handler is None:
            return redirect("leases:detail", public_id=lease.public_id)
        try:
            response = handler(request, m, lease)
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
            response = None
        if response is not None:
            return response
        return redirect("leases:detail", public_id=lease.public_id)

    def _bad_form(self, request, lease, name, form, exc=None):
        if exc is not None:
            _apply_errors(form, exc)
        return render(request, self.template_name, self.context(request, lease, **{name: form}))

    # Tenants -----------------------------------------------------------------

    def do_add_tenant(self, request, m, lease):
        form = forms.AddTenantForm(request.POST, tenants=_tenant_choices(request))
        if not form.is_valid():
            return self._bad_form(request, lease, "tenant_form", form)
        try:
            services.add_lease_tenant(m, lease, form.cleaned_data["tenant"], request=request)
        except ValidationError as exc:
            return self._bad_form(request, lease, "tenant_form", form, exc)
        messages.success(request, _("Tenant added."))

    def _lease_tenant(self, request, lease) -> Tenant:
        link = get_object_or_404(lease.lease_tenants.select_related("tenant"),
                                 tenant__public_id=request.POST.get("tenant"))
        return link.tenant

    def do_remove_tenant(self, request, m, lease):
        services.remove_lease_tenant(m, lease, self._lease_tenant(request, lease), request=request)
        messages.success(request, _("Tenant removed."))

    def do_make_primary(self, request, m, lease):
        services.set_primary_tenant(m, lease, self._lease_tenant(request, lease), request=request)
        messages.success(request, _("Primary tenant changed."))

    # Rent --------------------------------------------------------------------

    def do_rent_change(self, request, m, lease):
        form = forms.RentChangeForm(request.POST)
        if not form.is_valid():
            return self._bad_form(request, lease, "rent_form", form)
        try:
            services.add_rent_change(m, lease, request=request, **form.cleaned_data)
        except ValidationError as exc:
            return self._bad_form(request, lease, "rent_form", form, exc)
        messages.success(request, _("Rent change recorded."))

    # Charges -----------------------------------------------------------------

    def do_add_charge(self, request, m, lease):
        form = forms.ChargeForm(request.POST, charge_types=recurring_charge_types(lease.organization))
        if not form.is_valid():
            return self._bad_form(request, lease, "charge_form", form)
        try:
            services.add_charge(m, lease, request=request, **form.cleaned_data)
        except ValidationError as exc:
            return self._bad_form(request, lease, "charge_form", form, exc)
        messages.success(request, _("Charge added."))

    def do_end_charge(self, request, m, lease):
        charge = get_object_or_404(LeaseCharge, lease=lease, pk=request.POST.get("charge"))
        active_to = timezone.localdate()
        if not lease.is_draft:
            form = forms.EndChargeForm(request.POST)
            if not form.is_valid():
                raise ValidationError(_("Enter the last day of the charge."))
            active_to = form.cleaned_data["active_to"]
        services.end_charge(m, charge, active_to=active_to, request=request)
        messages.success(request, _("Charge removed.") if lease.is_draft else _("Charge ended."))

    # Payers ------------------------------------------------------------------

    def do_add_payer(self, request, m, lease):
        form = forms.PayerForm(request.POST)
        if not form.is_valid():
            return self._bad_form(request, lease, "payer_form", form)
        try:
            services.add_payer(m, lease, request=request, **form.cleaned_data)
        except ValidationError as exc:
            return self._bad_form(request, lease, "payer_form", form, exc)
        messages.success(request, _("Payer added."))

    def do_remove_payer(self, request, m, lease):
        payer = get_object_or_404(LeasePayer, lease=lease, pk=request.POST.get("payer"))
        services.remove_payer(m, payer, request=request)
        messages.success(request, _("Payer removed."))

    # Lease actions -----------------------------------------------------------

    def do_activate(self, request, m, lease):
        services.activate_lease(m, lease, request=request)
        messages.success(request, _("Lease %(number)s is active.") % {"number": lease.number})

    def do_notice(self, request, m, lease):
        form = forms.NoticeForm(request.POST)
        if not form.is_valid():
            return self._bad_form(request, lease, "notice_form", form)
        try:
            services.give_notice(m, lease, request=request, **form.cleaned_data)
        except ValidationError as exc:
            return self._bad_form(request, lease, "notice_form", form, exc)
        messages.success(request, _("Notice recorded. Move out by %(date)s.")
                         % {"date": lease.move_out_by.strftime("%d %b %Y")})

    def do_withdraw_notice(self, request, m, lease):
        services.withdraw_notice(m, lease, request=request)
        messages.success(request, _("Notice withdrawn."))

    def do_end(self, request, m, lease):
        form = forms.EndLeaseForm(request.POST)
        if not form.is_valid():
            return self._bad_form(request, lease, "end_form", form)
        try:
            services.end_lease(m, lease, request=request, **form.cleaned_data)
        except ValidationError as exc:
            lease.refresh_from_db()
            return self._bad_form(request, lease, "end_form", form, exc)
        messages.success(request, _("Lease terminated.") if lease.status == Lease.Status.TERMINATED
                         else _("Lease ended."))

    def do_renew(self, request, m, lease):
        form = forms.RenewForm(request.POST)
        if not form.is_valid():
            return self._bad_form(request, lease, "renew_form", form)
        try:
            new = services.renew_lease(m, lease, request=request, **form.cleaned_data)
        except ValidationError as exc:
            return self._bad_form(request, lease, "renew_form", form, exc)
        messages.success(request, _("Renewal drafted. Check it, then activate it."))
        return redirect("leases:detail", public_id=new.public_id)

    def do_transfer(self, request, m, lease):
        form = forms.TransferForm(request.POST, units=_transfer_units(m, lease))
        if not form.is_valid():
            return self._bad_form(request, lease, "transfer_form", form)
        try:
            new = services.transfer_lease(m, lease, request=request, **form.cleaned_data)
        except ValidationError as exc:
            return self._bad_form(request, lease, "transfer_form", form, exc)
        messages.success(request, _("Transfer drafted. Activating it ends this lease the day before the move."))
        return redirect("leases:detail", public_id=new.public_id)

    # Draft -------------------------------------------------------------------

    def do_delete(self, request, m, lease):
        unit = lease.unit
        services.delete_draft_lease(m, lease, request=request)
        messages.success(request, _("Draft lease deleted."))
        return redirect("properties:unit", public_id=unit.public_id)
