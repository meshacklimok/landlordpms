"""Lease services (doc 11 §7, D-016, D-018, doc 14 A3/A4).

Step 3 covers drafting: create and edit a draft, its tenants, recurring charges and
extra payer phones, and rent changes on an active lease. Activation, renewal,
termination and transfer are lease actions (step 4).

Rules:
- A lease belongs to the unit's property scope: `leases.*` capabilities are checked against it.
- Tenants on a lease must be visible to the actor, in the same organization and not archived.
- A draft can be changed freely and deleted (it was never issued, doc 11 §23).
  Once active, rent changes are added as new rows, never edited.
- Recurring charges cannot be rent, deposit or late fee; they live inside the lease dates.
- Every change is audited.
"""

import datetime
from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Exists, OuterRef, Q
from django.utils import timezone
from django.utils.translation import gettext as _

from accounts.models import Membership
from accounts.permissions import can, require, visible_properties
from audit import services as audit
from billing.models import ChargeType
from core.phone import InvalidPhoneNumber, normalize_phone
from properties.models import Property, Unit
from tenants.models import Tenant
from tenants.services import visible_tenants

from .models import Lease, LeaseCharge, LeasePayer, LeaseRentChange, LeaseTenant

LEASE_FIELDS = ("start_date", "end_date", "due_day", "grace_days", "notice_days", "deposit_amount", "terms")
OPEN_STATUSES = (Lease.Status.DRAFT, Lease.Status.ACTIVE)


# ---------------------------------------------------------------------------
# Selectors
# ---------------------------------------------------------------------------


def visible_leases(membership: Membership, queryset=None):
    qs = (queryset if queryset is not None else Lease.objects.all()).for_org(membership.organization)
    props = visible_properties(membership, Property.all_objects.all())
    return qs.filter(unit__property__in=props)


def occupying_leases(today: datetime.date | None = None):
    """Active leases that have started. An active lease past its end date still occupies (holdover)."""
    today = today or timezone.localdate()
    return Lease.objects.filter(status=Lease.Status.ACTIVE, start_date__lte=today)


def with_occupancy(units, today: datetime.date | None = None):
    return units.annotate(is_occupied=Exists(occupying_leases(today).filter(unit=OuterRef("pk"))))


def sync_tenant_status(tenant: Tenant) -> str:
    """ACTIVE while on an active lease, FORMER once every lease has closed, else PROSPECT (doc 11 §6)."""
    links = LeaseTenant.objects.filter(tenant=tenant)
    if links.filter(lease__status=Lease.Status.ACTIVE).exists():
        status = Tenant.Status.ACTIVE
    elif links.exclude(lease__status=Lease.Status.DRAFT).exists():
        status = Tenant.Status.FORMER
    else:
        status = Tenant.Status.PROSPECT
    if status != tenant.status:
        Tenant.all_objects.filter(pk=tenant.pk).update(status=status, updated_at=timezone.now())
        tenant.status = status
    return status


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _same_org(actor: Membership, *objs) -> None:
    for obj in objs:
        if obj is not None and obj.organization_id != actor.organization_id:
            raise PermissionDenied(_("That record belongs to another organization."))


def _require(actor: Membership, capability: str, lease: Lease) -> None:
    _same_org(actor, lease)
    require(actor, capability, lease.unit.property)


def _require_draft(lease: Lease) -> None:
    if lease.status != Lease.Status.DRAFT:
        raise ValidationError(_("Only a draft lease can be changed this way."))


def _require_charges(actor: Membership, lease: Lease) -> None:
    """Whoever drafts a lease sets its charges; once issued it takes charges.manage."""
    _same_org(actor, lease)
    prop = lease.unit.property
    if lease.status == Lease.Status.DRAFT and can(actor, "leases.draft", prop):
        return
    require(actor, "charges.manage", prop)


def _require_open(lease: Lease) -> None:
    if lease.status not in OPEN_STATUSES:
        raise ValidationError(_("This lease has closed."))


def _money(value, field: str, *, allow_zero: bool) -> Decimal:
    try:
        amount = Decimal(str(value)).quantize(Decimal("0.01"))
    except Exception:
        raise ValidationError({field: _("Enter an amount.")}) from None
    if amount < 0 or (amount == 0 and not allow_zero):
        raise ValidationError({field: _("Enter an amount above zero.") if not allow_zero
                               else _("The amount cannot be negative.")})
    return amount


def _check_unit(actor: Membership, unit: Unit) -> None:
    _same_org(actor, unit)
    if unit.is_archived or unit.property.is_archived:
        raise ValidationError({"unit": _("This unit is archived.")})
    if unit.manual_status == Unit.ManualStatus.INACTIVE:
        raise ValidationError({"unit": _("This unit is inactive. Change its status before letting it.")})


def _check_tenant(actor: Membership, tenant: Tenant) -> None:
    _same_org(actor, tenant)
    if not visible_tenants(actor, Tenant.all_objects.all()).filter(pk=tenant.pk).exists():
        raise PermissionDenied("tenants.view")
    if tenant.is_archived:
        raise ValidationError({"tenants": _("%(name)s is archived.") % {"name": tenant.name}})


def _check_dates(lease: Lease) -> None:
    if lease.end_date and lease.end_date < lease.start_date:
        raise ValidationError({"end_date": _("The end date must be on or after the start date.")})
    lease.full_clean(exclude=["organization", "unit", "created_by", "previous_lease"], validate_unique=False,
                     validate_constraints=False)


def _snapshot(lease: Lease) -> dict:
    out = {}
    for f in LEASE_FIELDS:
        v = getattr(lease, f)
        out[f] = str(v) if isinstance(v, (Decimal, datetime.date)) else v
    return out


def _audit(action, actor, obj, request=None, changes=None):
    audit.record(action, actor=actor.user, organization=actor.organization, obj=obj, request=request,
                 changes=changes or {})


def overlapping_active_lease(lease: Lease):
    """The active lease on the same unit whose dates overlap this one, if any."""
    qs = Lease.objects.filter(unit_id=lease.unit_id, status=Lease.Status.ACTIVE).exclude(pk=lease.pk)
    if lease.end_date:
        qs = qs.filter(start_date__lte=lease.end_date)
    return qs.filter(Q(end_date__isnull=True) | Q(end_date__gte=lease.start_date)).first()


# ---------------------------------------------------------------------------
# Drafts
# ---------------------------------------------------------------------------


@transaction.atomic
def create_lease(actor: Membership, *, unit: Unit, tenants: list[Tenant], start_date: datetime.date, rent,
                 end_date=None, deposit_amount=0, due_day=1, grace_days=3, notice_days=30, terms="",
                 request=None) -> Lease:
    """Creates a DRAFT lease. The first tenant is the primary tenant."""
    _same_org(actor, unit)
    require(actor, "leases.draft", unit.property)
    _check_unit(actor, unit)
    tenants = list(dict.fromkeys(tenants))
    if not tenants:
        raise ValidationError({"tenants": _("Choose at least one tenant.")})
    for t in tenants:
        _check_tenant(actor, t)
    rent = _money(rent, "rent", allow_zero=False)
    lease = Lease(organization=actor.organization, unit=unit, start_date=start_date, end_date=end_date,
                  deposit_amount=_money(deposit_amount or 0, "deposit_amount", allow_zero=True),
                  due_day=due_day, grace_days=grace_days, notice_days=notice_days, terms=(terms or "").strip(),
                  created_by=actor.user)
    _check_dates(lease)
    lease.save()
    for i, t in enumerate(tenants):
        LeaseTenant.objects.create(organization=lease.organization, lease=lease, tenant=t, is_primary=i == 0)
    LeaseRentChange.objects.create(organization=lease.organization, lease=lease, effective_from=start_date,
                                   amount=rent, reason=_("Initial rent"), created_by=actor.user)
    _audit("lease.create", actor, lease, request, {
        **{k: [None, v] for k, v in _snapshot(lease).items() if v not in (None, "")},
        "unit": [None, unit.payment_reference], "rent": [None, str(rent)],
        "tenants": [None, [t.name for t in tenants]],
    })
    return lease


@transaction.atomic
def update_draft_lease(actor: Membership, lease: Lease, *, request=None, rent=None, **fields) -> Lease:
    _require(actor, "leases.draft", lease)
    _require_draft(lease)
    before = _snapshot(lease)
    for k in LEASE_FIELDS:
        if k in fields:
            value = fields[k]
            if k == "deposit_amount":
                value = _money(value or 0, k, allow_zero=True)
            elif k == "terms":
                value = (value or "").strip()
            setattr(lease, k, value)
    _check_dates(lease)
    lease.save()
    changes = audit.diff(before, _snapshot(lease))

    # A draft has exactly one rent row, dated on the start date.
    initial = lease.rent_changes.get()
    new_rent = _money(rent, "rent", allow_zero=False) if rent is not None else initial.amount
    if (initial.amount, initial.effective_from) != (new_rent, lease.start_date):
        if initial.amount != new_rent:
            changes["rent"] = [str(initial.amount), str(new_rent)]
        LeaseRentChange.objects.filter(pk=initial.pk).update(amount=new_rent, effective_from=lease.start_date)
    for charge in lease.charges.filter(active_from__lt=lease.start_date):
        charge.active_from = lease.start_date
        charge.save(update_fields=["active_from", "updated_at"])
    if changes:
        _audit("lease.update", actor, lease, request, changes)
    return lease


@transaction.atomic
def delete_draft_lease(actor: Membership, lease: Lease, request=None) -> None:
    """A draft was never issued, so it may be deleted (doc 11 §23). The audit event stays."""
    _require(actor, "leases.draft", lease)
    _require_draft(lease)
    _audit("lease.delete_draft", actor, lease, request, {"unit": [lease.unit.payment_reference, None]})
    tenants = [lt.tenant for lt in lease.lease_tenants.select_related("tenant")]
    for rel in (lease.lease_tenants, lease.rent_changes, lease.charges, lease.payers):
        rel.all().delete()
    lease.delete()
    for t in tenants:
        sync_tenant_status(t)


@transaction.atomic
def add_lease_tenant(actor: Membership, lease: Lease, tenant: Tenant, request=None) -> LeaseTenant:
    _require(actor, "leases.draft", lease)
    _require_draft(lease)
    _check_tenant(actor, tenant)
    if lease.lease_tenants.filter(tenant=tenant).exists():
        raise ValidationError({"tenant": _("%(name)s is already on this lease.") % {"name": tenant.name}})
    link = LeaseTenant.objects.create(organization=lease.organization, lease=lease, tenant=tenant,
                                      is_primary=not lease.lease_tenants.exists())
    _audit("lease.tenant_add", actor, lease, request, {"tenant": [None, tenant.name]})
    return link


@transaction.atomic
def remove_lease_tenant(actor: Membership, lease: Lease, tenant: Tenant, request=None) -> None:
    _require(actor, "leases.draft", lease)
    _require_draft(lease)
    _same_org(actor, tenant)
    link = lease.lease_tenants.filter(tenant=tenant).first()
    if link is None:
        return
    if link.is_primary:
        raise ValidationError(_("Make another tenant primary before removing the primary tenant."))
    link.delete()
    _audit("lease.tenant_remove", actor, lease, request, {"tenant": [tenant.name, None]})


@transaction.atomic
def set_primary_tenant(actor: Membership, lease: Lease, tenant: Tenant, request=None) -> None:
    _require(actor, "leases.draft", lease)
    _require_open(lease)
    _same_org(actor, tenant)
    link = lease.lease_tenants.filter(tenant=tenant).first()
    if link is None:
        raise ValidationError(_("That tenant is not on this lease."))
    if link.is_primary:
        return
    old = lease.lease_tenants.filter(is_primary=True).select_related("tenant").first()
    lease.lease_tenants.filter(is_primary=True).update(is_primary=False)
    LeaseTenant.objects.filter(pk=link.pk).update(is_primary=True)
    _audit("lease.primary_tenant", actor, lease, request, {"primary": [old.tenant.name if old else None, tenant.name]})


# ---------------------------------------------------------------------------
# Rent changes (active leases)
# ---------------------------------------------------------------------------


@transaction.atomic
def add_rent_change(actor: Membership, lease: Lease, *, effective_from: datetime.date, amount, reason="",
                    request=None) -> LeaseRentChange:
    """A new rent from a date. History is kept: earlier rows are never edited (D-018)."""
    _require(actor, "leases.change_rent", lease)
    if lease.status != Lease.Status.ACTIVE:
        raise ValidationError(_("Rent changes are recorded on an active lease. Edit a draft's rent directly."))
    amount = _money(amount, "amount", allow_zero=False)
    if effective_from <= lease.start_date:
        raise ValidationError({"effective_from": _("The change must start after the lease start date.")})
    if lease.end_date and effective_from > lease.end_date:
        raise ValidationError({"effective_from": _("The change must start before the lease ends.")})
    if lease.rent_changes.filter(effective_from=effective_from).exists():
        raise ValidationError({"effective_from": _("There is already a rent change on that date.")})
    before = lease.rent_on(effective_from)
    change = LeaseRentChange.objects.create(organization=lease.organization, lease=lease,
                                            effective_from=effective_from, amount=amount,
                                            reason=(reason or "").strip()[:200], created_by=actor.user)
    _audit("lease.rent_change", actor, lease, request, {
        "rent": [str(before), str(amount)], "effective_from": [None, str(effective_from)],
    })
    return change


# ---------------------------------------------------------------------------
# Recurring charges
# ---------------------------------------------------------------------------


@transaction.atomic
def add_charge(actor: Membership, lease: Lease, *, charge_type: ChargeType, amount, active_from=None,
               active_to=None, request=None) -> LeaseCharge:
    _require_charges(actor, lease)
    _require_open(lease)
    _same_org(actor, charge_type)
    if charge_type.is_archived:
        raise ValidationError({"charge_type": _("This charge type is archived.")})
    if not charge_type.is_recurring_allowed:
        raise ValidationError({"charge_type": _("Rent, deposit and late fees are not recurring charges.")})
    amount = _money(amount, "amount", allow_zero=False)
    active_from = active_from or lease.start_date
    if active_from < lease.start_date:
        raise ValidationError({"active_from": _("A charge cannot start before the lease.")})
    if lease.end_date and active_from > lease.end_date:
        raise ValidationError({"active_from": _("A charge cannot start after the lease ends.")})
    if active_to and active_to < active_from:
        raise ValidationError({"active_to": _("The end must be on or after the start.")})
    clash = lease.charges.filter(charge_type=charge_type).filter(
        Q(active_to__isnull=True) | Q(active_to__gte=active_from))
    if active_to:
        clash = clash.filter(active_from__lte=active_to)
    if clash.exists():
        raise ValidationError({"charge_type": _("This lease already has %(name)s for those dates.")
                               % {"name": charge_type.name}})
    charge = LeaseCharge.objects.create(organization=lease.organization, lease=lease, charge_type=charge_type,
                                        amount=amount, active_from=active_from, active_to=active_to,
                                        created_by=actor.user)
    _audit("lease.charge_add", actor, lease, request, {
        "charge": [None, f"{charge_type.name} {amount}"], "active_from": [None, str(active_from)],
    })
    return charge


@transaction.atomic
def end_charge(actor: Membership, charge: LeaseCharge, *, active_to: datetime.date, request=None) -> None:
    """Stops a charge from a date. On a draft the charge is simply removed."""
    lease = charge.lease
    _require_charges(actor, lease)
    _require_open(lease)
    if lease.status == Lease.Status.DRAFT:
        label = f"{charge.charge_type.name} {charge.amount}"
        charge.delete()
        _audit("lease.charge_remove", actor, lease, request, {"charge": [label, None]})
        return
    if active_to < charge.active_from:
        raise ValidationError({"active_to": _("The end must be on or after the start.")})
    old = charge.active_to
    charge.active_to = active_to
    charge.save(update_fields=["active_to", "updated_at"])
    _audit("lease.charge_end", actor, lease, request, {
        "charge": [f"{charge.charge_type.name} {charge.amount}"] * 2,
        "active_to": [str(old) if old else None, str(active_to)],
    })


# ---------------------------------------------------------------------------
# Extra payers
# ---------------------------------------------------------------------------


@transaction.atomic
def add_payer(actor: Membership, lease: Lease, *, phone: str, name="", request=None) -> LeasePayer:
    _require(actor, "leases.draft", lease)
    _require_open(lease)
    try:
        phone = normalize_phone(phone)
    except InvalidPhoneNumber as exc:
        raise ValidationError({"phone": str(exc)}) from None
    if lease.lease_tenants.filter(Q(tenant__phone=phone) | Q(tenant__alt_phone=phone)).exists():
        raise ValidationError({"phone": _("That is already a tenant's phone on this lease.")})
    try:
        with transaction.atomic():
            payer = LeasePayer.objects.create(organization=lease.organization, lease=lease, phone=phone,
                                              name=(name or "").strip(), created_by=actor.user)
    except IntegrityError:
        raise ValidationError({"phone": _("That phone is already a payer on this lease.")}) from None
    _audit("lease.payer_add", actor, lease, request, {"payer": [None, phone]})
    return payer


@transaction.atomic
def remove_payer(actor: Membership, payer: LeasePayer, request=None) -> None:
    lease = payer.lease
    _require(actor, "leases.draft", lease)
    _require_open(lease)
    phone = payer.phone
    payer.delete()
    _audit("lease.payer_remove", actor, lease, request, {"payer": [phone, None]})


def can_see_lease_money(membership: Membership, lease: Lease) -> bool:
    return can(membership, "leases.view", lease.unit.property)
