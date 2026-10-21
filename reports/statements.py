"""The monthly owner statement (D-053): what was billed and collected on an owner's properties,
the management fee (a share of rent, a fixed monthly amount or both, D-127), what is due to the owner
and what was paid out to them (D-058).

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
from properties.models import (
    Property,
    PropertyOwner,
    PropertyShare,
    Unit,
    format_percent,
    owned_by,
    owner_ids_of,
)

from .income import _invoice_shares, _split
from .metrics import LIVE_INVOICE, add_months, month_end, month_start
from .models import OwnerRemittance

NO_OWNER = "none"


def manages_in(prop: Property, month: datetime.date) -> bool:
    """Whether the property was under management in the month: from the month it was added up to and
    including the month it was archived. The fixed fee is charged only for those months."""
    if month < month_start(timezone.localtime(prop.created_at).date()):
        return False
    return prop.archived_at is None or month <= month_start(timezone.localtime(prop.archived_at).date())


def fixed_fee(prop: Property, month: datetime.date) -> Decimal:
    return (prop.management_fee_fixed or ZERO) if manages_in(prop, month) else ZERO


def fee_for(prop: Property, rent: Decimal, month: datetime.date) -> Decimal:
    """The management fee for a month (D-127): the percentage of rent collected plus any fixed monthly fee.
    The one rule for statements and the agency fee report."""
    pct = prop.management_fee_percent
    return (round_money(rent * pct / 100) if pct else ZERO) + fixed_fee(prop, month)


def fee_label(prop: Property, currency: str) -> str:
    """How the fee is worked out, e.g. "10% of rent + KES 2,000.00 a month"."""
    parts = []
    if prop.management_fee_percent:
        parts.append(_("%(pct)s%% of rent") % {"pct": format_percent(prop.management_fee_percent)})
    if prop.management_fee_fixed:
        parts.append(_("%(amount)s a month") % {"amount": format_money(prop.management_fee_fixed, currency)})
    return " + ".join(parts)


@dataclass
class Line:
    """One lease on the statement."""

    lease: Lease
    tenant: str = ""
    billed: Decimal = ZERO
    rent: Decimal = ZERO  # rent collected, with money not yet applied to an invoice
    utilities: Decimal = ZERO  # water and electricity collected
    other: Decimal = ZERO  # other charges collected
    deposit: Decimal = ZERO
    balance: Decimal = ZERO

    @property
    def collected(self) -> Decimal:
        return self.rent + self.utilities + self.other


@dataclass
class Block:
    """One property: its leases, occupancy, fee and expenses."""

    property: Property
    month: datetime.date
    currency: str = "KES"
    lines: list[Line] = field(default_factory=list)
    expenses: list = field(default_factory=list)  # approved Expense rows paid in the month
    rentable: int = 0
    occupied: int = 0
    share: Decimal = Decimal(100)  # the statement owner's percentage of a jointly owned property (D-078)

    def _sum(self, name) -> Decimal:
        return sum((getattr(line, name) for line in self.lines), ZERO)

    @property
    def billed(self):
        return self._sum("billed")

    @property
    def rent(self):
        return self._sum("rent")

    @property
    def utilities(self):
        return self._sum("utilities")

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
    def fixed_fee(self) -> Decimal:
        return fixed_fee(self.property, self.month)

    @property
    def fee(self) -> Decimal:
        return fee_for(self.property, self.rent, self.month)

    @property
    def fee_label(self) -> str:
        return fee_label(self.property, self.currency) if (self.fee_percent or self.fixed_fee) else ""

    @property
    def expense_total(self) -> Decimal:
        return sum((e.amount for e in self.expenses), ZERO)

    @property
    def joint(self) -> bool:
        return self.share < 100

    @property
    def whole_due(self) -> Decimal:
        """Collected less the fee and expenses, for the whole property."""
        return self.collected - self.fee - self.expense_total

    @property
    def due(self) -> Decimal:
        """The owner's part of what is due: all of it, or their share when owned jointly."""
        return round_money(self.whole_due * self.share / 100) if self.joint else self.whole_due


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
    def utilities(self):
        return self._sum("utilities")

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
    def whole_due(self) -> Decimal:
        """Collected less the fee and expenses. Negative when expenses were more than was collected."""
        return self._sum("whole_due")

    @property
    def joint(self) -> bool:
        return any(b.joint for b in self.blocks)

    @property
    def others_share(self) -> Decimal:
        """What goes to the other owners of jointly owned properties (D-078)."""
        return self.whole_due - self.due

    @property
    def due(self) -> Decimal:
        """Due to this owner: the whole of what is due, less the other owners' shares."""
        return self._sum("due")

    @property
    def has_fee(self) -> bool:
        return any(b.fee_label for b in self.blocks)

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
    return not Property.all_objects.filter(owned_by(owner)).exclude(pk__in=ids).exists()


def owner_choices(membership: Membership) -> list[tuple[str, str]]:
    """Owners of the properties the member can see, then properties with no owner set."""
    props = visible_properties(membership, Property.all_objects.all())
    owners = PropertyOwner.objects.filter(organization=membership.organization, pk__in=owner_ids_of(props))
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
        props = props.filter(owned_by(owner))
    return build(org, owner, props, month, show_tenants=can(membership, "tenants.view"),
                 remittances=owner is not None and fully_sees(membership, owner),
                 scoped=accessible_property_ids(membership) is not None)


def for_owner(owner: PropertyOwner, month: datetime.date) -> Statement | None:
    """The owner's own statement in the owner portal (D-128): every property they own, tenants unnamed."""
    props = Property.all_objects.filter(organization=owner.organization).filter(owned_by(owner))
    return build(owner.organization, owner, props, month, show_tenants=False, remittances=True, scoped=False,
                 for_owner=True)


def build(org: Organization, owner: PropertyOwner | None, props, month: datetime.date, *, show_tenants: bool,
          remittances: bool, scoped: bool, for_owner: bool = False) -> Statement | None:
    """The statement for these properties; callers decide which properties and what is shown."""
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
        row.utilities += part.utilities
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
    blocks = {p.pk: Block(property=p, month=month, currency=org.currency) for p in props}
    if owner is not None:
        for share in PropertyShare.objects.filter(property__in=props, owner=owner):
            blocks[share.property_id].share = share.percent
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
    if owner is not None and remittances:
        result.remittances = list(OwnerRemittance.objects.live().filter(owner=owner, month=month)
                                  .select_related("recorded_by").order_by("paid_on", "pk"))
    result.notes = _notes(result, last, expense_services.waiting_summary(org, props, month, last), scoped=scoped,
                          for_owner=for_owner)
    return result


def _notes(st: Statement, last: datetime.date, waiting, *, scoped: bool, for_owner: bool) -> list[str]:
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
    if st.whole_due < 0:
        notes.append(_("Expenses were more than was collected, so the amount due is negative."))
    for b in st.blocks:
        if b.joint:
            notes.append(_("%(property)s is owned jointly: %(owner)s holds %(share)s%%, so gets that share of what "
                           "is due on it. Figures above are for the whole property.")
                         % {"property": b.property.name, "owner": st.recipient, "share": format_percent(b.share)})
    if any(b.fixed_fee for b in st.blocks):
        notes.append(_("A fixed monthly fee is charged every month a property is managed, whatever is collected."))
    if scoped:
        notes.append(_("Only the properties you can see are included."))
    if st.owner is not None and not st.shows_remittances:
        notes.append(_("Payments to the owner are not shown: you do not see all of this owner's properties."))
    if for_owner:
        notes.append(_("Tenant names are left out to protect their privacy."))
    elif not st.show_tenants:
        notes.append(_("Tenant names are left out for your role."))
    notes.append(_("Figures as recorded on %(day)s: a late payment or a correction changes past months.")
                 % {"day": f"{timezone.localdate():%d %b %Y}"})
    return notes
