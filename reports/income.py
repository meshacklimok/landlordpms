"""The annual rental income pack (D-050): what was billed and received in a year, and a tax estimate.

A helper for the owner and their tax adviser, never a filing. Views stay thin; the rules live here.

- Billed: live invoice lines by the month they bill (billing_month), deposits left out.
- Received: confirmed payments dated in the year. Reversed payments are left out, whenever reversed.
  Each allocation is split across its invoice's live lines by their share of it, so a payment
  towards an invoice of rent and water counts partly as rent. Money not yet applied to an invoice
  is counted as rent and shown on its own line; deposits are never income here.
- The estimate is taxable rent (rent received plus unapplied money) for each month times the rate in
  force that month for the organization's tax residence. Rates are dated and [VERIFY].
- Only the properties the member can see are counted.
"""

import datetime
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db.models import DateField, Min, Sum
from django.db.models.functions import Cast, Coalesce, TruncMonth
from django.utils import timezone
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy

from accounts.models import Membership, Organization
from accounts.permissions import accessible_property_ids, require, visible_properties
from audit import services as audit
from billing.models import ChargeType, Invoice, InvoiceLine
from core.money import ZERO, format_money, round_money
from payments.models import Payment
from properties.models import Property

D = datetime.date
Residence = Organization.TaxResidence
Category = ChargeType.Category

# Monthly rental income tax, by date in force [VERIFY with KRA or a tax adviser before relying on it].
# Resident: 10% from 2016, 7.5% from 1 Jan 2024 (Finance Act 2023).
# Non-resident: a 10% final tax on gross rent from 1 Jul 2026 (Finance Act 2026, section 6B).
RATES: dict[str, tuple[tuple[datetime.date, Decimal], ...]] = {
    Residence.RESIDENT: ((D(2016, 1, 1), Decimal("0.10")), (D(2024, 1, 1), Decimal("0.075"))),
    Residence.NON_RESIDENT: ((D(2026, 7, 1), Decimal("0.10")),),
}
# The resident monthly rental income regime covers this yearly gross rent [VERIFY].
RESIDENT_BAND = (Decimal("288000"), Decimal("15000000"))

DISCLAIMER = gettext_lazy("Estimate only. Confirm with your tax adviser before filing.")


def rate_on(residence: str, day: datetime.date) -> Decimal | None:
    rate = None
    for start, value in RATES.get(residence, ()):
        if day >= start:
            rate = value
    return rate


def percent(rate: Decimal | None) -> str:
    """0.075 → 7.5%; no rate → a dash."""
    return "—" if rate is None else f"{float(rate * 100):g}%"


def _bucket(category: str) -> str:
    if category == Category.RENT:
        return "rent"
    if category == Category.DEPOSIT:
        return "deposit"
    return "other"


@dataclass
class Row:
    label: str
    key: object = None
    rent_billed: Decimal = ZERO
    other_billed: Decimal = ZERO
    received: Decimal = ZERO
    rent_received: Decimal = ZERO
    other_received: Decimal = ZERO
    deposit_received: Decimal = ZERO
    unapplied: Decimal = ZERO
    rate: Decimal | None = None
    tax: Decimal | None = None

    @property
    def rate_label(self) -> str:
        return percent(self.rate)

    @property
    def taxable(self) -> Decimal:
        return self.rent_received + self.unapplied

    def add_received(self, part: "Split") -> None:
        self.received += part.amount
        self.rent_received += part.rent
        self.other_received += part.other
        self.deposit_received += part.deposit
        self.unapplied += part.unapplied


@dataclass
class Split:
    """How one payment divides: rent, other charges, deposit and money not yet applied."""

    payment: Payment
    rent: Decimal = ZERO
    other: Decimal = ZERO
    deposit: Decimal = ZERO
    unapplied: Decimal = ZERO

    @property
    def amount(self) -> Decimal:
        return self.payment.amount


@dataclass
class Pack:
    organization: Organization
    year: int
    residence: str
    months: list[Row]
    properties: list[Row]
    total: Row
    receipts: list[Split]
    notes: list[str] = field(default_factory=list)

    @property
    def residence_label(self) -> str:
        return Residence(self.residence).label

    @property
    def currency(self) -> str:
        return self.organization.currency

    @property
    def rates(self) -> list[tuple[str, str]]:
        """The rates used, e.g. [("Jan – Jun", "—"), ("Jul – Dec", "10%")], for the summary."""
        spans: list[list] = []
        for row in self.months:
            if spans and spans[-1][2] == row.rate:
                spans[-1][1] = row.key
            else:
                spans.append([row.key, row.key, row.rate])
        return [(f"{a:%b}" if a == b else f"{a:%b} – {b:%b}", percent(rate)) for a, b, rate in spans]


def years(membership: Membership) -> list[int]:
    """Years that have billing or payments, newest first, always including this year."""
    org = membership.organization
    first = [d for d in (
        Payment.objects.filter(organization=org).aggregate(d=Min("paid_at"))["d"],
        Invoice.objects.filter(organization=org).aggregate(d=Min("period_start"))["d"],
    ) if d]
    this_year = timezone.localdate().year
    start = min([d.year for d in first] + [this_year])
    return list(range(this_year, start - 1, -1))


def income_pack(membership: Membership, year: int) -> Pack:
    require(membership, "reports.view_financial")
    org = membership.organization
    properties = visible_properties(membership, Property.all_objects.all())
    start, end = D(year, 1, 1), D(year, 12, 31)
    months = {D(year, m, 1): Row(label=f"{D(year, m, 1):%b %Y}", key=D(year, m, 1)) for m in range(1, 13)}
    by_property: dict[int, Row] = {}

    def prop_row(prop: Property) -> Row:
        if prop.pk not in by_property:
            by_property[prop.pk] = Row(label=prop.name, key=prop)
        return by_property[prop.pk]

    # Billed, by the month each line bills.
    lines = (
        InvoiceLine.objects.filter(organization=org, is_void=False, lease__unit__property__in=properties,
                                   invoice__status__in=[Invoice.Status.ISSUED, Invoice.Status.PARTIALLY_PAID,
                                                        Invoice.Status.PAID])
        .annotate(month=Coalesce("billing_month", Cast(TruncMonth("invoice__period_start"), DateField())))
        .filter(month__gte=start, month__lte=end)
        .values("month", "lease__unit__property", "charge_type__category")
        .annotate(total=Sum("amount"))
    )
    prop_objects = {p.pk: p for p in properties}
    for r in lines:
        bucket = _bucket(r["charge_type__category"])
        if bucket == "deposit":
            continue
        name = "rent_billed" if bucket == "rent" else "other_billed"
        for row in (months[r["month"]], prop_row(prop_objects[r["lease__unit__property"]])):
            setattr(row, name, getattr(row, name) + r["total"])

    # Received, split by what each payment paid.
    payments = list(
        Payment.objects.filter(organization=org, status=Payment.Status.CONFIRMED, paid_at__gte=start,
                               paid_at__lte=end, lease__unit__property__in=properties)
        .select_related("lease__unit__property", "tenant", "receipt")
        .prefetch_related("allocations").order_by("paid_at", "pk")
    )
    shares = _invoice_shares(payments)
    receipts = [_split(p, shares) for p in payments]
    for part in receipts:
        p = part.payment
        months[D(p.paid_at.year, p.paid_at.month, 1)].add_received(part)
        prop_row(p.lease.unit.property).add_received(part)

    total = Row(label=_("Total"))
    for month, row in months.items():
        row.rate = rate_on(org.landlord_tax_residence, month)
        row.tax = round_money(row.taxable * row.rate) if row.rate is not None else None
        for name in ("rent_billed", "other_billed", "received", "rent_received", "other_received",
                     "deposit_received", "unapplied"):
            setattr(total, name, getattr(total, name) + getattr(row, name))
    taxed = [row.tax for row in months.values() if row.tax is not None]
    total.tax = sum(taxed, ZERO) if taxed else None

    pack = Pack(organization=org, year=year, residence=org.landlord_tax_residence, months=list(months.values()),
                properties=sorted(by_property.values(), key=lambda r: r.label.lower()), total=total,
                receipts=receipts)
    pack.notes = _notes(pack, membership)
    return pack


def _invoice_shares(payments) -> dict[int, dict[str, Decimal]]:
    ids = {a.invoice_id for p in payments for a in p.allocations.all()}
    shares: dict[int, dict[str, Decimal]] = defaultdict(lambda: defaultdict(lambda: ZERO))
    for r in (InvoiceLine.objects.filter(invoice_id__in=ids, is_void=False)
              .values("invoice_id", "charge_type__category").annotate(total=Sum("amount"))):
        shares[r["invoice_id"]][_bucket(r["charge_type__category"])] += r["total"]
    return shares


def _split(payment: Payment, shares) -> Split:
    part = Split(payment=payment)
    applied = ZERO
    for a in payment.allocations.all():
        applied += a.amount
        parts = shares.get(a.invoice_id, {})
        whole = sum(parts.values(), ZERO)
        if not whole:
            part.other += a.amount
            continue
        rent = round_money(a.amount * parts.get("rent", ZERO) / whole)
        deposit = round_money(a.amount * parts.get("deposit", ZERO) / whole)
        part.rent += rent
        part.deposit += deposit
        part.other += a.amount - rent - deposit
    part.unapplied = payment.amount - applied
    return part


def _notes(pack: Pack, membership: Membership) -> list[str]:
    notes = []
    today = timezone.localdate()
    over = pack.year < today.year
    if not over:
        notes.append(_("%(year)s is not over yet: the figures run to today.") % {"year": pack.year})
    if accessible_property_ids(membership) is not None:
        notes.append(_("Only the properties you can see are included."))
    if pack.total.unapplied:
        notes.append(_("%(amount)s was received but not yet applied to an invoice. It is counted as rent.")
                     % {"amount": format_money(pack.total.unapplied, pack.currency)})
    if any(row.rate is None for row in pack.months):
        notes.append(_("There is no rate in the estimate for some months of the year, so no tax is "
                       "shown for them."))
    low, high = RESIDENT_BAND
    taxable = pack.total.taxable
    if pack.residence == Residence.RESIDENT and (taxable > high or (over and taxable < low)):
        notes.append(_("Rent for the year is outside %(low)s to %(high)s, the band the monthly rental "
                       "income tax covers. Ask your tax adviser how it is taxed.")
                     % {"low": format_money(low, pack.currency), "high": format_money(high, pack.currency)})
    notes.append(_("Expenses are not tracked yet, so the pack shows gross income only."))
    notes.append(str(DISCLAIMER))
    return notes


def set_tax_residence(actor: Membership, residence: str, *, request=None) -> None:
    require(actor, "organization.manage")
    if residence not in Residence.values:
        raise ValidationError(_("Choose resident or non-resident."))
    org = actor.organization
    if org.landlord_tax_residence == residence:
        return
    changes = {"landlord_tax_residence": [org.landlord_tax_residence, residence]}
    org.landlord_tax_residence = residence
    org.save(update_fields=["landlord_tax_residence", "updated_at"])
    audit.record("organization.tax_residence", actor=actor.user, organization=org, obj=org, request=request,
                 changes=changes)
