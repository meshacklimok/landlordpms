"""Thin views: forms in, services do the work (D-009).

A lease is visible when its unit's property is. Anything else is a 404, never a 403.
"""

import datetime

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.base import ContentFile
from django.core.paginator import Paginator
from django.db.models import Prefetch, Q
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views import View

from accounts.mixins import CapabilityRequiredMixin
from accounts.permissions import can, visible_properties
from audit import services as audit
from billing import deposits, invoicing
from billing.services import recurring_charge_types
from core.ids import uuid_or_none
from documents import services as documents
from documents.models import Document
from properties.models import Property, Unit
from properties.views import _apply_errors, _get_unit
from tenants.models import Tenant
from tenants.services import visible_tenants

from . import clauses, forms, paperwork, pdf, services
from .models import EXPIRING_WITHIN_DAYS, Lease, LeaseCharge, LeasePayer, LeaseTenant

PAGE_SIZE = 25


def _with_tenants(qs):
    return qs.select_related("unit__property").prefetch_related(
        Prefetch("lease_tenants", queryset=LeaseTenant.objects.select_related("tenant"))
    )


def _renewal_panel(lease, can_draft: bool, successor) -> dict:
    """The renewal offer card on an active lease (D-131): the latest offer and, to those who may, the offer form."""
    from portal import offers as renewal_offers
    from portal.forms import OfferForm

    offer = renewal_offers.latest(lease)
    can_offer = (can_draft and successor is None and services.open_notice(lease) is None
                 and renewal_offers.open_offer(lease) is None)
    return {"offer": offer, "can_manage": can_draft,
            "form": OfferForm(initial_rent=lease.rent_on(timezone.localdate())) if can_offer else None}


def _get_lease(request, public_id) -> Lease:
    qs = services.visible_leases(request.membership, Lease.all_objects.all())
    return get_object_or_404(_with_tenants(qs), public_id=public_id)


def _tenant_choices(request):
    return visible_tenants(request.membership).order_by("name")


def _transfer_units(membership, lease):
    props = visible_properties(membership, Property.objects.all())
    return (Unit.objects.for_org(membership.organization).filter(property__in=props)
            .exclude(pk=lease.unit_id).exclude(manual_status=Unit.ManualStatus.INACTIVE)
            .select_related("property", "building").order_by("property__name", "code"))


class LeaseListView(CapabilityRequiredMixin, View):
    template_name = "leases/lease_list.html"
    required_capability = "leases.view"

    def get(self, request):
        search = forms.LeaseSearchForm(request.GET)
        search.is_valid()
        q = search.cleaned_data.get("q", "").strip()
        status = search.cleaned_data.get("status", "")
        show_archived = request.GET.get("archived") == "on"
        if show_archived:
            qs = services.visible_leases(request.membership, Lease.all_objects.filter(archived_at__isnull=False))
        else:
            qs = services.visible_leases(request.membership)
        if status:
            qs = qs.filter(status=status)
        # Tenants under notice, soonest move-out first (D-106).
        under_notice = request.GET.get("notice") == "1"
        if under_notice:
            qs = qs.filter(status=Lease.Status.ACTIVE, move_out_on__isnull=False)
        if q:
            qs = qs.filter(
                Q(number__icontains=q) | Q(unit__code__icontains=q) | Q(unit__payment_reference__icontains=q)
                | Q(unit__property__name__icontains=q) | Q(lease_tenants__tenant__name__icontains=q)
            ).distinct()
        # Active leases ending within the expiring window, soonest first.
        ending = bool(search.cleaned_data.get("ending"))
        if ending:
            today = timezone.localdate()
            qs = qs.filter(status=Lease.Status.ACTIVE, end_date__gte=today,
                           end_date__lte=today + datetime.timedelta(days=EXPIRING_WITHIN_DAYS))
        properties = visible_properties(request.membership, Property.objects.all()).order_by("name")
        wanted = search.cleaned_data.get("property")
        prop = next((p for p in properties if str(p.public_id) == wanted), None) if wanted else None
        if prop:
            qs = qs.filter(unit__property=prop)
        order = ("move_out_on", "pk") if under_notice else ("end_date", "pk") if ending else ("-start_date", "-pk")
        page = Paginator(_with_tenants(qs).order_by(*order), PAGE_SIZE).get_page(request.GET.get("page"))
        return render(request, self.template_name, {
            "page": page, "q": q, "status": status, "status_choices": Lease.Status.choices,
            "today": timezone.localdate(), "show_archived": show_archived, "under_notice": under_notice,
            "ending": ending, "properties": properties[:200], "prop": prop, "expiring_days": EXPIRING_WITHIN_DAYS,
        })


def _is_hostel(request) -> bool:
    return request.organization.org_type == "HOSTEL"


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
        form = forms.LeaseForm(initial=initial, tenants=_tenant_choices(request), hostel=_is_hostel(request))
        return render(request, self.template_name, {"form": form, "unit": unit})

    def post(self, request, public_id):
        unit = self._unit(request, public_id)
        form = forms.LeaseForm(request.POST, tenants=_tenant_choices(request), hostel=_is_hostel(request))
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
        form = forms.LeaseForm(initial=forms.LeaseForm.initial_for(lease), tenants=None, with_tenants=False,
                               hostel=_is_hostel(request))
        return render(request, self.template_name, {"form": form, "unit": lease.unit, "lease": lease})

    def post(self, request, public_id):
        lease = self._lease(request, public_id)
        form = forms.LeaseForm(request.POST, tenants=None, with_tenants=False, hostel=_is_hostel(request))
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


def _condition_reports(m, lease) -> list[dict]:
    """The move-in and move-out report of this lease, or whether one can be started (D-047)."""
    from inspections import services as inspections
    from inspections.models import ConditionReport

    may_record = can(m, "inspections.record", lease.unit.property)
    return [{"kind": kind, "label": label, "slug": slug, "report": inspections.current(lease, kind),
             "can_start": may_record and not inspections.start_problem(lease, kind)}
            for kind, label, slug in ((ConditionReport.Kind.MOVE_IN, _("Move-in"), "move-in"),
                                      (ConditionReport.Kind.MOVE_OUT, _("Move-out"), "move-out"))]


MOVE_IN_WINDOW_DAYS = 30


def _move_in_steps(m, lease, ctx) -> list[dict]:
    """What is left to do after a new lease is activated: portal invite, deposit, move-in report, agreement.

    Only for a lease activated in the last MOVE_IN_WINDOW_DAYS; empty once everything is done.
    """
    if lease.status != Lease.Status.ACTIVE or lease.archived_at or not lease.activated_at:
        return []
    if (timezone.now() - lease.activated_at).days > MOVE_IN_WINDOW_DAYS:
        return []
    prop = lease.unit.property
    steps = []
    primary = next((lt.tenant for lt in lease.lease_tenants.all() if lt.is_primary), None)
    if primary and can(m, "tenants.invite_portal") and can(m, "tenants.view"):
        from portal import services as portal

        steps.append({"label": _("Invite %(name)s to the portal") % {"name": primary.name},
                      "hint": _("They see their balance, statement and receipts online."),
                      "done": portal.live_account(primary) is not None,
                      "url": reverse("tenants:detail", args=[primary.public_id])})
    if primary and can(m, "messages.send", prop):
        from notifications.models import Message

        steps.append({"label": _("Send %(name)s a welcome message (optional)") % {"name": primary.name},
                      "hint": _("Add the house rules, or anything else they should know on day one."),
                      "done": Message.objects.filter(lease=lease, tenant=primary, type="tenant_message").exists(),
                      "url": reverse("notifications:compose") + f"?tenant={primary.public_id}&welcome=1"})
    account = ctx.get("account")
    if account and lease.organization.takes_deposits and lease.deposit_amount > 0:
        steps.append({"label": _("Record the deposit"),
                      "hint": _("Agreed deposit: %(amount)s.") % {"amount": f"{lease.currency} {lease.deposit_amount:,.2f}"},
                      "done": account["deposit_held"] >= lease.deposit_amount,
                      "url": reverse("billing:lease_account", args=[lease.public_id])})
    reports = ctx.get("condition_reports")
    if reports:
        move_in = reports[0]
        if move_in["report"] or move_in["can_start"]:
            steps.append({"label": _("Do the move-in inspection"),
                          "hint": _("A record of the unit's condition protects both sides at move-out."),
                          "done": bool(move_in["report"]), "url": "#condition-reports"})
    esign = ctx.get("esign")
    if esign:
        from esign.models import SigningRequest

        Kind = SigningRequest.Kind
        sent = any(r.kind == Kind.AGREEMENT for r in esign["requests"])
        if sent or any(s["kind"] == Kind.AGREEMENT.value for s in esign["sendable"]):
            steps.append({"label": _("Get the agreement signed"),
                          "hint": _("Send it for e-signature, or print it, sign it and upload the copy."),
                          "done": sent, "url": "#paperwork"})
    return [] if all(s["done"] for s in steps) else steps


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
            "doc_panel": documents.panel(m, lease),
            "can_restore": lease.is_archived and can(m, "leases.terminate", prop),
        }
        if not lease.is_draft and can(m, "inspections.view", prop):
            ctx["condition_reports"] = _condition_reports(m, lease)
        ctx["can_issue_letter"] = not lease.is_draft and can(m, "tenants.issue_letter", prop)
        # Notices to vacate and demand letters rest on tenancy law, so estates, water vendors and garbage collectors
        # have none (D-130).
        if not lease.is_draft and lease.organization.bills_rent and can(m, "leases.issue_notice", prop):
            from letters.models import NoticeLetter

            ctx["can_issue_notice"] = True
            ctx["notice_letters"] = list(NoticeLetter.objects.filter(lease=lease, withdrawn_at__isnull=True)[:3])
        ctx["can_save_agreement"] = documents.can_upload(m, lease)
        ctx["can_share_agreement"] = ctx["can_save_agreement"] and can(m, "documents.share_with_tenant", prop)
        ctx["can_move_out"] = paperwork.move_out_applies(lease) and can(m, "invoices.view", prop)
        from esign import services as esign

        ctx["esign"] = esign.lease_panel(m, lease)
        if not lease.is_draft and can(m, "invoices.view", prop):
            ctx["account"] = {"balance": invoicing.lease_balance(lease), "deposit_held": deposits.held(lease)}
        if not lease.is_draft and lease.archived_at is None and can(m, "payments.record", prop):
            from mpesa.stk import stk_accounts

            ctx["can_request_mpesa"] = bool(stk_accounts(lease))
        if not lease.is_draft:
            notices = list(lease.notices.all())
            ctx["notices"] = notices
            ctx["open_notice"] = next((n for n in notices if n.is_open), None)
        if ctx["can_terminate"] and lease.organization.bills_rent:
            ctx["notice_form"] = forms_in.get("notice_form") or forms.NoticeForm(
                initial={"given_on": ctx["today"].isoformat()})
            ctx["end_form"] = forms_in.get("end_form") or forms.EndLeaseForm(
                initial={"ended_on": min(ctx["today"], lease.move_out_by or ctx["today"]).isoformat()})
        ctx["move_in_steps"] = _move_in_steps(m, lease, ctx)
        if is_active:
            ctx["renewal"] = _renewal_panel(lease, can_draft, successor)
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
        # Several of these forms share field names (amount, start date, reason); give each its own element ids so
        # every label points at its own field. Only the ids change, not the posted names.
        for key in ("notice_form", "end_form", "renew_form", "transfer_form", "tenant_form", "charge_form", "payer_form",
                    "rent_form"):
            if ctx.get(key) is not None:
                ctx[key].auto_id = f"{key.removesuffix('_form')}_%s"
        renewal = ctx.get("renewal")
        if isinstance(renewal, dict) and renewal.get("form") is not None:
            renewal["form"].auto_id = "offer_%s"
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

    def do_restore(self, request, m, lease):
        services.restore_lease(m, lease, request=request)
        messages.success(request, _("Lease restored. It is back in the lists for at least %(n)s days.")
                         % {"n": services.RESTORE_HOLD_DAYS})

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
                                 tenant__public_id=uuid_or_none(request.POST.get("tenant")))
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
        messages.success(request, _("Lease %(number)s is active. Billing has started.")
                         % {"number": lease.number})

    def do_notice(self, request, m, lease):
        if not lease.organization.bills_rent:
            raise Http404
        form = forms.NoticeForm(request.POST)
        if not form.is_valid():
            return self._bad_form(request, lease, "notice_form", form)
        try:
            notice = services.give_notice(m, lease, request=request, **form.cleaned_data)
        except ValidationError as exc:
            return self._bad_form(request, lease, "notice_form", form, exc)
        messages.success(request, _("Notice recorded. Move out on %(date)s.")
                         % {"date": notice.move_out_on.strftime("%d %b %Y")})

    def do_withdraw_notice(self, request, m, lease):
        form = forms.WithdrawNoticeForm(request.POST)
        form.is_valid()
        services.withdraw_notice(m, lease, request=request, reason=form.cleaned_data.get("reason", ""))
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
        data = dict(form.cleaned_data)
        activate_now = data.pop("activate_now")
        try:
            new = services.transfer_lease(m, lease, request=request, **data)
        except ValidationError as exc:
            return self._bad_form(request, lease, "transfer_form", form, exc)
        if activate_now:
            try:
                services.activate_lease(m, new, request=request)
            except (ValidationError, PermissionDenied) as exc:
                reason = " ".join(exc.messages) if isinstance(exc, ValidationError) else _("you cannot activate leases")
                messages.warning(request, _("Move drafted but not finished: %(why)s. Check it, then activate it.")
                                 % {"why": reason})
            else:
                messages.success(request, _("Moved to %(unit)s. Lease %(number)s is active, the old lease has ended.")
                                 % {"unit": new.unit.code, "number": new.number})
            return redirect("leases:detail", public_id=new.public_id)
        messages.success(request, _("Transfer drafted. Activating it ends this lease the day before the move."))
        return redirect("leases:detail", public_id=new.public_id)

    # Draft -------------------------------------------------------------------

    def do_delete(self, request, m, lease):
        unit = lease.unit
        services.delete_draft_lease(m, lease, request=request)
        messages.success(request, _("Draft lease deleted."))
        return redirect("properties:unit", public_id=unit.public_id)


# ---------------------------------------------------------------------------
# Paperwork: agreement PDF and move-out statement (D-073)
# ---------------------------------------------------------------------------


def _pdf_response(request, content: bytes, filename: str) -> HttpResponse:
    response = HttpResponse(content, content_type="application/pdf")
    disposition = "attachment" if request.GET.get("download") == "1" else "inline"
    response["Content-Disposition"] = f'{disposition}; filename="{filename}"'
    return response


def _save_copy(request, lease, content: bytes, *, filename: str, title: str, category: str):
    """Files the PDF on the lease through the documents app; returns the new document."""
    m = request.membership
    if not documents.can_upload(m, lease):
        raise Http404
    share = request.POST.get("share") == "on"
    try:
        doc = documents.upload(m, lease, file=ContentFile(content, name=filename), title=title, category=category,
                               share=share, request=request)
    except PermissionDenied:
        raise Http404 from None
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
        return None
    messages.success(request, _("A copy was saved to the lease's documents.")
                     + (" " + _("The tenant can see it in the portal.") if doc.shared_with_tenant else ""))
    return doc


def _can_share(m, lease) -> bool:
    return documents.can_upload(m, lease) and can(m, "documents.share_with_tenant", lease.unit.property)


def _filename(lease, kind: str) -> str:
    return f"{kind}-{lease.number or 'draft-' + lease.unit.code}.pdf"


class AgreementView(CapabilityRequiredMixin, View):
    """GET: the agreement PDF. POST: save a copy to the lease's documents."""

    required_capability = "leases.view"

    def get(self, request, public_id):
        lease = _get_lease(request, public_id)
        content = pdf.render_agreement(paperwork.agreement(lease, request.membership))
        return _pdf_response(request, content, _filename(lease, "agreement"))

    def post(self, request, public_id):
        lease = _get_lease(request, public_id)
        content = pdf.render_agreement(paperwork.agreement(lease, request.membership))
        title = _("Tenancy agreement %(lease)s") % {"lease": lease}
        _save_copy(request, lease, content, filename=_filename(lease, "agreement"), title=title[:120],
                   category=Document.Category.LEASE)
        return redirect("leases:detail", public_id=lease.public_id)


class BlankAgreementView(CapabilityRequiredMixin, View):
    """The agreement with blank lines, to print and fill in by hand (D-187). No lease is needed."""

    required_capability = "leases.view"

    def get(self, request):
        org = request.organization
        content = pdf.render_blank_agreement(org, org.brand_name or org.name)
        return _pdf_response(request, content, "tenancy-agreement-blank.pdf")


class AgreementWordingView(CapabilityRequiredMixin, View):
    """Where an owner reads and rewrites the conditions printed on every tenancy agreement (D-187)."""

    template_name = "leases/agreement_wording.html"
    required_capability = "organization.manage"

    def render(self, request, form, status=200):
        return render(request, self.template_name, {
            "form": form, "custom": bool(request.organization.agreement_clauses.strip()),
            "markers": clauses.PLACEHOLDERS}, status=status)

    def get(self, request):
        org = request.organization
        text = org.agreement_clauses.strip() or clauses.default_text()
        return self.render(request, forms.AgreementWordingForm(initial={"clauses": text}))

    def post(self, request):
        org = request.organization
        if "reset" in request.POST:
            text = ""
        else:
            form = forms.AgreementWordingForm(request.POST)
            if not form.is_valid():
                return self.render(request, form, status=400)
            text = form.cleaned_data["clauses"]
            if text == clauses.default_text().strip():  # unchanged standard wording is not stored as the owner's own
                text = ""
        if text != org.agreement_clauses.strip():
            org.agreement_clauses = text
            org.save(update_fields=["agreement_clauses"])
            audit.record("organization.agreement_wording", actor=request.user, organization=org, obj=org,
                         request=request, changes={"agreement_clauses": ["custom" if text else "standard"] * 2})
            messages.success(request, _("Agreement wording saved.") if text else _("Back to the standard wording."))
        else:
            messages.success(request, _("Nothing changed."))
        return redirect("leases:wording")


def _move_out_lease(request, public_id) -> Lease:
    lease = _get_lease(request, public_id)
    if not can(request.membership, "invoices.view", lease.unit.property) or not paperwork.move_out_applies(lease):
        raise Http404
    return lease


class MoveOutView(CapabilityRequiredMixin, View):
    """The move-out statement page. POST saves the PDF to the lease's documents."""

    template_name = "leases/move_out.html"
    required_capability = "invoices.view"

    def get(self, request, public_id):
        lease = _move_out_lease(request, public_id)
        m, prop = request.membership, lease.unit.property
        return render(request, self.template_name, {
            "s": paperwork.move_out(lease), "lease": lease, "settlement": pdf.settlement_text,
            "can_save": documents.can_upload(m, lease), "can_share": _can_share(m, lease),
            "can_deposits": lease.archived_at is None and (can(m, "deposits.deduct", prop)
                                                            or can(m, "deposits.record", prop)),
        })

    def post(self, request, public_id):
        lease = _move_out_lease(request, public_id)
        s = paperwork.move_out(lease)
        title = _("Move-out statement %(lease)s, %(day)s") % {"lease": lease, "day": s["today"].strftime("%d %b %Y")}
        _save_copy(request, lease, pdf.render_move_out(s), filename=_filename(lease, "move-out"), title=title[:120],
                   category=Document.Category.NOTICE)
        return redirect("leases:move_out", public_id=lease.public_id)


class MoveOutPdfView(CapabilityRequiredMixin, View):
    required_capability = "invoices.view"

    def get(self, request, public_id):
        lease = _move_out_lease(request, public_id)
        return _pdf_response(request, pdf.render_move_out(paperwork.move_out(lease)), _filename(lease, "move-out"))
