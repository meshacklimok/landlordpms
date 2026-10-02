"""The monthly owner statement (D-053): what was billed and collected on an owner's properties,
the management fee, what is due to the owner and what was paid out to them (D-058).

Live figures, drawn on request; nothing is stored. Collected is cash: confirmed payments dated
in the month, split across their invoices as in the income pack (D-050). Expenses are approved
expenses paid in the month (D-067), taken off what is due to the owner.
"""

import datetime
from dataclasses import dataclass, field
from decimal import Decimal

from django.db.models import Count, DateField, Prefetch, Q, Sum
from django.db.models.functions import Cast, Coalesce, TruncMonth
from django.utils import timezone
from django.utils.translation import gettext as _
from django.utils.translation import ngettext

from accounts.models import Membership, Organization
from accounts.permissions import accessible_property_ids, can, require, visible_properties
from billing.models import ChargeType, InvoiceLine, LedgerEntry
from core.money import ZERO, format_money, round_money
from expenses import services as expense_services
from leases.models import Lease, LeaseTenant
from leases.services import occupying_leases
from payments.models import Payment
from properties.models import Property, PropertyOwner, Unit

from .income import _invoice_shares, _split
from .metrics import LIVE_INVOICE, add_months, month_end, month_start
from .models import OwnerRemittance

NO_OWNER = "none"


@dataclass
class Line:
    """One lease on the statement."""

    lease: Lease
    tenant: str = ""
    billed: Decimal = ZERO
    rent: Decimal = ZERO  # rent collected, with money not yet applied to an invoice
    other: Decimal = ZERO
    deposit: Decimal = ZERO
    balance: Decimal = ZERO

    @property
    def collected(self) -> Decimal:
        return self.rent + self.other


@dataclass
class Block:
    """One property: its leases, occupancy, fee and expenses."""

    property: Property
    lines: list[Line] = field(default_factory=list)
    expenses: list = field(default_factory=list)  # approved Expense rows paid in the month
    rentable: int = 0
    occupied: int = 0

    def _sum(self, name) -> Decimal:
        return sum((getattr(line, name) for line in self.lines), ZERO)

    @property
    def billed(self):
        return self._sum("billed")

    @property
    def rent(self):
        return self._sum("rent")

    @property
    def other(self):
        return self._sum("other")

    @property
    def collected(self):
        return self._sum("collected")

    @property
    def deposit(self):
        return self._sum("deposit")

    @property
    def balance(self):
        return self._sum("balance")

    @property
    def fee_percent(self) -> Decimal | None:
        return self.property.management_fee_percent

    @property
    def fee(self) -> Decimal:
        pct = self.fee_percent
        return round_money(self.rent * pct / 100) if pct else ZERO

    @property
    def expense_total(self) -> Decimal:
        return sum((e.amount for e in self.expenses), ZERO)


@dataclass
class Statement:
    organization: Organization
    owner: PropertyOwner | None
    month: datetime.date
    blocks: list[Block]
    show_tenants: bool
    notes: list[str] = field(default_factory=list)
    generated_at: datetime.datetime = field(default_factory=timezone.now)
    # Live payments to the owner for the month (D-058). None when not shown: no owner, or the
    # member does not see all of the owner's properties, so the due figure is partial.
    remittances: list | None = None

    @property
    def recipient(self) -> str:
        return self.owner.name if self.owner else self.organization.name

    @property
    def currency(self) -> str:
        return self.organization.currency

    @property
    def label(self) -> str:
        return f"{self.month:%B %Y}"

    def _sum(self, name) -> Decimal:
        return sum((getattr(b, name) for b in self.blocks), ZERO)

    @property
    def billed(self):
        return self._sum("billed")

    @property
    def rent(self):
        return self._sum("rent")

    @property
    def other(self):
        return self._sum("other")

    @property
    def collected(self):
        return self._sum("collected")

    @property
    def fee(self):
        return self._sum("fee")

    @property
    def deposit(self):
        return self._sum("deposit")

    @property
    def balance(self):
        return self._sum("balance")

    @property
    def expenses(self):
        return self._sum("expense_total")

    @property
    def due(self) -> Decimal:
        """Collected less the fee and expenses. Negative when expenses were more than was collected."""
        return self.collected - self.fee - self.expenses

    @property
    def has_fee(self) -> bool:
        return any(b.fee_percent for b in self.blocks)

    @property
    def shows_remittances(self) -> bool:
        return self.remittances is not None

    @property
    def remitted(self) -> Decimal:
        return sum((r.amount for r in self.remittances or ()), ZERO)

    @property
    def remaining(self) -> Decimal:
        return self.due - self.remitted


def default_month(today: datetime.date | None = None) -> datetime.date:
    """Last month: the one a statement is usually sent for."""
    return add_months(month_start(today or timezone.localdate()), -1)


def fully_sees(membership: Membership, owner: PropertyOwner) -> bool:
    """Whether the member sees every property of this owner, archived ones included."""
    ids = accessible_property_ids(membership)
    if ids is None:
        return True
    return not Property.all_objects.filter(owner=owner).exclude(pk__in=ids).exists()


def owner_choices(membership: Membership) -> list[tuple[str, str]]:
    """Owners of the properties the member can see, then properties with no owner set."""
    props = visible_properties(membership, Property.all_objects.all())
    owners = PropertyOwner.objects.filter(organization=membership.organization, properties__in=props).distinct()
    choices = [(str(o.public_id), o.name) for o in owners.order_by("name", "pk")]
    if props.filter(owner__isnull=True).exists():
        choices.append((NO_OWNER, _("No owner set (%(org)s)") % {"org": membership.organization.name}))
    return choices


def statement(membership: Membership, owner_key: str, month: datetime.date) -> Statement | None:
    """None when the owner is not one the member can see."""
    require(membership, "reports.view_financial")
    org = membership.organization
    props = visible_properties(membership, Property.all_objects.all())
    if owner_key == NO_OWNER:
        owner = None
        props = props.filter(owner__isnull=True)
    else:
        owner = PropertyOwner.objects.filter(organization=org, public_id=owner_key).first()
        if owner is None:
            return None
        props = props.filter(owner=owner)
    props = list(props.order_by("name"))
    if not props:
        return None
    month = month_start(month)
    last = month_end(month)
    lines: dict[int, Line] = {}

    def line(lease_id) -> Line:
        return lines.setdefault(lease_id, Line(lease=None))

    # Billed, by the month each line bills; deposits are not rent.
    for r in (InvoiceLine.objects.filter(organization=org, is_void=False, lease__unit__property__in=props,
                                         invoice__status__in=LIVE_INVOICE)
              .exclude(charge_type__category=ChargeType.Category.DEPOSIT)
              .annotate(month=Coalesce("billing_month", Cast(TruncMonth("invoice__period_start"), DateField())))
              .filter(month=month).values("lease_id").annotate(total=Sum("amount"))):
        line(r["lease_id"]).billed += r["total"]

    # Collected: cash in the month, split by what each payment paid.
    payments = list(Payment.objects.filter(organization=org, status=Payment.Status.CONFIRMED, paid_at__gte=month,
                                           paid_at__lte=last, lease__unit__property__in=props)
                    .prefetch_related("allocations"))
    shares = _invoice_shares(payments)
    for p in payments:
        part = _split(p, shares)
        row = line(p.lease_id)
        row.rent += part.rent + part.unapplied
        row.other += part.other
        row.deposit += part.deposit

    # What each lease owed when the month closed.
    for r in (LedgerEntry.objects.filter(organization=org, lease__unit__property__in=props, entry_date__lte=last)
              .values("lease_id").annotate(total=Sum("amount"))):
        if r["total"]:
            line(r["lease_id"]).balance += r["total"]

    # Leases in their units at month end show even with nothing billed.
    occupying = occupying_leases(last).filter(unit__property__in=props)
    for lease_id in occupying.values_list("pk", flat=True):
        line(lease_id)

    leases = Lease.all_objects.filter(pk__in=lines).select_related("unit__property").prefetch_related(
        Prefetch("lease_tenants", LeaseTenant.objects.select_related("tenant")))
    show_tenants = can(membership, "tenants.view")
    blocks = {p.pk: Block(property=p) for p in props}
    for lease in leases:
        row = lines[lease.pk]
        row.lease = lease
        tenant = lease.primary_tenant
        row.tenant = tenant.name if (show_tenants and tenant) else ""
        blocks[lease.unit.property_id].lines.append(row)

    Manual = Unit.ManualStatus
    for r in (Unit.objects.filter(property__in=[p for p in props if not p.is_archived])
              .exclude(manual_status=Manual.INACTIVE).values("property_id")
              .annotate(rentable=Count("pk"), occupied=Count("pk", filter=Q(pk__in=occupying.values("unit_id"))))):
        blocks[r["property_id"]].rentable = r["rentable"]
        blocks[r["property_id"]].occupied = r["occupied"]

    for e in (expense_services.approved(org, props, month, last).select_related("category", "supplier")
              .order_by("paid_on", "pk")):
        blocks[e.property_id].expenses.append(e)

    for block in blocks.values():
        block.lines.sort(key=lambda row: (row.lease.unit.code, row.lease.start_date, row.lease.pk))
    result = Statement(organization=org, owner=owner, month=month, show_tenants=show_tenants,
                       blocks=[b for b in blocks.values() if b.lines or b.rentable or b.expenses])
    if owner is not None and fully_sees(membership, owner):
        result.remittances = list(OwnerRemittance.objects.live().filter(owner=owner, month=month)
                                  .select_related("recorded_by").order_by("paid_on", "pk"))
    result.notes = _notes(result, membership, last, expense_services.waiting_summary(org, props, month, last))
    return result


def _notes(st: Statement, membership: Membership, last: datetime.date, waiting) -> list[str]:
    notes = []
    if last >= timezone.localdate():
        notes.append(_("The month is not over yet; figures will change."))
    notes.append(_("Collected is money received in the month, whatever month it was for. Money not yet applied "
                   "to an invoice is counted as rent."))
    if st.deposit:
        notes.append(_("Deposits received are held and not included in the amount due to the owner."))
    notes.append(_("Expenses are approved expenses paid in the month, taken off what is due to the owner."))
    if waiting.waiting_count:
        notes.append(ngettext(
            "%(n)s expense of %(total)s is waiting for approval and is not taken off yet.",
            "%(n)s expenses totalling %(total)s are waiting for approval and are not taken off yet.",
            waiting.waiting_count) % {"n": waiting.waiting_count, "total": format_money(waiting.waiting, st.currency)})
    if st.due < 0:
        notes.append(_("Expenses were more than was collected, so the amount due is negative."))
    if accessible_property_ids(membership) is not None:
        notes.append(_("Only the properties you can see are included."))
    if st.owner is not None and not st.shows_remittances:
        notes.append(_("Payments to the owner are not shown: you do not see all of this owner's properties."))
    if not st.show_tenants:
        notes.append(_("Tenant names are left out for your role."))
    notes.append(_("Figures as recorded on %(day)s: a late payment or a correction changes past months.")
                 % {"day": f"{timezone.localdate():%d %b %Y}"})
    return notes
