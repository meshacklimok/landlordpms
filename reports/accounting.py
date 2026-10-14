"""The accounting export (D-075): a double-entry journal and registers for the landlord's accountant.

- Accrual basis, one balanced journal per event: invoices, payments, balances brought forward,
  adjustments and corrections from the rent ledger; the deposit sub-ledger; approved expenses;
  payments to owners (whole organization only, as in the cash flow).
- Nothing is rewritten: a later void or reversal is its own journal, dated when it happened.
- Account names are fixed English strings so the accountant's mapping keeps working; only charge
  type, expense category and payment account names come from the organization.
- Receivables in the journal follow the rent ledger exactly, entry by entry.
"""

import csv
import datetime
import io
import zipfile
from dataclasses import dataclass, field
from decimal import Decimal

from django.db.models import Prefetch
from django.utils import timezone

from accounts.models import Membership, Organization
from accounts.permissions import accessible_property_ids, require
from billing.models import ChargeType, DepositEntry, Invoice, InvoiceLine, LedgerEntry
from core.money import ZERO
from expenses.models import Expense
from leases.models import Lease, LeaseTenant
from payments.models import Payment
from properties.models import Property

from .finance import _properties
from .metrics import month_end
from .models import OwnerRemittance

RECEIVABLE = "Accounts receivable"
VAT = "VAT payable"
DEPOSIT_CLEARING = "Deposit clearing"
DEPOSITS_HELD = "Tenant deposits held"
DEPOSIT_TRANSFERS = "Deposit transfers"
DEPOSIT_DEDUCTIONS = "Income - Deposit deductions"
OPENING = "Opening balance equity"
ADJUSTMENTS = "Rent adjustments"
UNRECORDED = "Money account not recorded"
OWNERS = "Payments to owners"
ROUNDING = "Invoice rounding"

# Where money moved when no payment account was chosen, by method code (payments, expenses and
# payments to owners share the codes).
METHOD_ACCOUNTS = {"MPESA": "M-Pesa", "BANK": "Bank", "CHEQUE": "Bank", "CASH": "Cash", "OTHER": "Other money account"}

INCOME, EXPENSE, BANK = "Income", "Expense", "Bank"
ACCOUNT_TYPES = {
    RECEIVABLE: "Accounts receivable",
    VAT: "Other current liability",
    DEPOSIT_CLEARING: "Other current asset",
    DEPOSITS_HELD: "Other current liability",
    DEPOSIT_TRANSFERS: "Other current liability",
    OPENING: "Equity",
    ADJUSTMENTS: INCOME,
    OWNERS: "Equity",
    ROUNDING: INCOME,
}

TYPE_ORDER = ["Accounts receivable", BANK, "Other current asset", "Other current liability", "Equity", INCOME,
              EXPENSE]

FILES = ("journal.csv", "accounts.csv", "invoices.csv", "payments.csv", "expenses.csv")


def account_type(name: str) -> str:
    if name in ACCOUNT_TYPES:
        return ACCOUNT_TYPES[name]
    if name.startswith("Income - "):
        return INCOME
    if name.startswith("Expense - "):
        return EXPENSE
    return BANK


def payment_account_name(payment: Payment) -> str:
    if payment.payment_account_id:
        return payment.payment_account.display_name
    return METHOD_ACCOUNTS.get(payment.method, METHOD_ACCOUNTS["OTHER"])


# ---------------------------------------------------------------------------
# Journals
# ---------------------------------------------------------------------------


@dataclass
class Line:
    account: str
    amount: Decimal  # positive = debit, negative = credit
    description: str = ""
    name: str = ""
    place: str = ""

    @property
    def debit(self) -> Decimal:
        return self.amount if self.amount > 0 else ZERO

    @property
    def credit(self) -> Decimal:
        return -self.amount if self.amount < 0 else ZERO


@dataclass
class Journal:
    number: str
    date: datetime.date
    lines: list[Line]

    @property
    def balanced(self) -> bool:
        return sum((line.amount for line in self.lines), ZERO) == 0


@dataclass
class AccountTotal:
    name: str
    type: str
    debit: Decimal = ZERO
    credit: Decimal = ZERO

    @property
    def net(self) -> Decimal:
        return self.debit - self.credit


@dataclass
class Export:
    organization: Organization
    start: datetime.date
    end: datetime.date
    scope: Property | None
    whole_organization: bool
    journals: list[Journal] = field(default_factory=list)
    invoices: list = field(default_factory=list)
    payments: list = field(default_factory=list)
    expenses: list = field(default_factory=list)

    @property
    def currency(self) -> str:
        return self.organization.currency

    @property
    def label(self) -> str:
        if self.start == self.end:
            return f"{self.start:%b %Y}"
        return f"{self.start:%b %Y} – {self.end:%b %Y}"

    @property
    def filename(self) -> str:
        return f"accounting-{self.start:%Y-%m}-to-{self.end:%Y-%m}.zip"

    def accounts(self) -> list[AccountTotal]:
        totals: dict[str, AccountTotal] = {}
        for journal in self.journals:
            for line in journal.lines:
                row = totals.setdefault(line.account, AccountTotal(line.account, account_type(line.account)))
                row.debit += line.debit
                row.credit += line.credit
        return sorted(totals.values(), key=lambda a: (TYPE_ORDER.index(a.type), a.name.lower()))

    @property
    def total_debit(self) -> Decimal:
        return sum((line.debit for j in self.journals for line in j.lines), ZERO)

    @property
    def total_credit(self) -> Decimal:
        return sum((line.credit for j in self.journals for line in j.lines), ZERO)


def _tenant_name(lease: Lease) -> str:
    tenant = lease.primary_tenant
    return tenant.name if tenant else (lease.number or "")


def _where(lease: Lease) -> str:
    return f"{lease.number or ''} {lease.unit.code}".strip()


def _invoice_lines(invoice: Invoice, sign: int) -> list[tuple[str, Decimal, str]]:
    """(account, signed contra amount, description) for each line; sign 1 for an issue, -1 for a void."""
    out = []
    for line in invoice.lines.all():
        if line.charge_type.category == ChargeType.Category.DEPOSIT:
            account = DEPOSIT_CLEARING
        else:
            account = f"Income - {line.charge_type.name}"
        out.append((account, -sign * line.amount, line.description))
        if line.tax_amount:
            out.append((VAT, -sign * line.tax_amount, line.description))
    return out


def _ledger_contra(entry: LedgerEntry) -> list[tuple[str, Decimal, str]]:
    """The other side of a rent ledger entry: (account, signed amount, description), summing to -amount."""
    Kind = LedgerEntry.Kind
    if entry.kind == Kind.REVERSAL:
        return [(account, -amount, text) for account, amount, text in _ledger_contra(entry.reversal_of)]
    if entry.kind in (Kind.INVOICE, Kind.INVOICE_VOID):
        rows = _invoice_lines(entry.invoice, 1 if entry.kind == Kind.INVOICE else -1)
        diff = -entry.amount - sum((amount for _a, amount, _t in rows), ZERO)
        if diff:
            rows.append((ROUNDING, diff, ""))
        return rows
    if entry.kind in (Kind.PAYMENT, Kind.PAYMENT_REVERSAL):
        account = payment_account_name(entry.payment)
    elif entry.kind == Kind.OPENING_BALANCE:
        account = OPENING
    elif entry.kind == Kind.DEPOSIT_APPLIED:
        account = DEPOSITS_HELD
    else:
        account = ADJUSTMENTS
    return [(account, -entry.amount, "")]


def _ledger_number(entry: LedgerEntry) -> str:
    Kind = LedgerEntry.Kind
    if entry.kind == Kind.INVOICE:
        return entry.invoice.number
    if entry.kind == Kind.INVOICE_VOID:
        return f"{entry.invoice.number}-V"
    if entry.kind in (Kind.PAYMENT, Kind.PAYMENT_REVERSAL):
        receipt = getattr(entry.payment, "receipt", None)
        number = receipt.number if receipt else f"PAY-{entry.payment_id}"
        return number if entry.kind == Kind.PAYMENT else f"{number}-R"
    return f"LE-{entry.pk}"


def _ledger_text(entry: LedgerEntry, lease: Lease) -> str:
    Kind = LedgerEntry.Kind
    where = _where(lease)
    if entry.kind == Kind.INVOICE:
        return f"Invoice {entry.invoice.number}, {where}"
    if entry.kind == Kind.INVOICE_VOID:
        return f"Invoice {entry.invoice.number} voided, {where}"
    if entry.kind in (Kind.PAYMENT, Kind.PAYMENT_REVERSAL):
        text = "Payment" if entry.kind == Kind.PAYMENT else "Payment reversed"
        ref = f", ref {entry.payment.reference}" if entry.payment.reference else ""
        return f"{text} {_ledger_number(entry).removesuffix('-R')}{ref}, {where}"
    label = str(Kind(entry.kind).label)
    reason = f": {entry.reason}" if entry.reason else ""
    return f"{label}, {where}{reason}"


def _ledger_journals(org: Organization, properties, first: datetime.date, last: datetime.date) -> list[Journal]:
    lines = Prefetch("invoice__lines", queryset=InvoiceLine.objects.select_related("charge_type").order_by("pk"))
    tenants = Prefetch("lease__lease_tenants", queryset=LeaseTenant.objects.select_related("tenant"))
    entries = (LedgerEntry.objects.filter(organization=org, lease__unit__property__in=properties,
                                          entry_date__gte=first, entry_date__lte=last)
               .select_related("lease__unit__property", "invoice", "payment__payment_account", "payment__receipt",
                               "reversal_of__invoice", "reversal_of__payment__payment_account")
               .prefetch_related(lines, tenants)
               .order_by("entry_date", "pk"))
    journals = []
    for entry in entries:
        lease = entry.lease
        name, place = _tenant_name(lease), lease.unit.property.name
        text = _ledger_text(entry, lease)
        rows = [Line(RECEIVABLE, entry.amount, text, name, place)]
        for account, amount, detail in _ledger_contra(entry):
            if amount:
                rows.append(Line(account, amount, detail or text, name, place))
        journals.append(Journal(_ledger_number(entry), entry.entry_date, rows))
    return journals


DEPOSIT_CONTRA = {
    DepositEntry.Kind.RECEIVED: DEPOSIT_CLEARING,
    DepositEntry.Kind.DEDUCTION: DEPOSIT_DEDUCTIONS,
    DepositEntry.Kind.REFUNDED: UNRECORDED,
    DepositEntry.Kind.TRANSFERRED: DEPOSIT_TRANSFERS,
}


def _deposit_journals(org: Organization, properties, first: datetime.date, last: datetime.date) -> list[Journal]:
    """The deposit sub-ledger. A deduction put towards the balance is already in the rent ledger's journal."""
    tenants = Prefetch("lease__lease_tenants", queryset=LeaseTenant.objects.select_related("tenant"))
    entries = (DepositEntry.objects.filter(organization=org, lease__unit__property__in=properties,
                                           entry_date__gte=first, entry_date__lte=last)
               .select_related("lease__unit__property", "reversal_of").prefetch_related(tenants)
               .order_by("entry_date", "pk"))
    journals = []
    for entry in entries:
        original = entry.reversal_of if entry.kind == DepositEntry.Kind.REVERSAL else entry
        if original.ledger_entry_id:
            continue
        lease = entry.lease
        name, place = _tenant_name(lease), lease.unit.property.name
        deposit = str(DepositEntry.DepositType(entry.deposit_type).label)
        kind = str(DepositEntry.Kind(entry.kind).label)
        text = f"{deposit}: {kind}, {_where(lease)}"
        if entry.reason:
            text = f"{text}: {entry.reason}"
        journals.append(Journal(f"DEP-{entry.pk}", entry.entry_date, [
            Line(DEPOSITS_HELD, -entry.amount, text, name, place),
            Line(DEPOSIT_CONTRA[original.kind], entry.amount, text, name, place),
        ]))
    return journals


def _local_date(moment: datetime.datetime) -> datetime.date:
    return timezone.localtime(moment).date()


def _expense_journals(expenses: list[Expense], first: datetime.date, last: datetime.date) -> list[Journal]:
    journals = []
    for e in expenses:
        name = e.supplier.name if e.supplier_id else ""
        text = f"Expense {e.number}: {e.description}"
        money = METHOD_ACCOUNTS.get(e.method, METHOD_ACCOUNTS["OTHER"])
        cost = f"Expense - {e.category.name}"
        if first <= e.paid_on <= last:
            journals.append(Journal(e.number, e.paid_on, [Line(cost, e.amount, text, name, e.property.name),
                                                          Line(money, -e.amount, text, name, e.property.name)]))
        if e.voided_at and first <= _local_date(e.voided_at) <= last:
            text = f"Expense {e.number} voided: {e.void_reason}"
            journals.append(Journal(f"{e.number}-V", _local_date(e.voided_at), [
                Line(cost, -e.amount, text, name, e.property.name),
                Line(money, e.amount, text, name, e.property.name)]))
    return journals


def _owner_journals(org: Organization, first: datetime.date, last: datetime.date) -> list[Journal]:
    journals = []
    for r in OwnerRemittance.objects.filter(organization=org).select_related("owner").order_by("paid_on", "pk"):
        money = METHOD_ACCOUNTS.get(r.method, METHOD_ACCOUNTS["OTHER"])
        text = f"Payment to owner for {r.month:%b %Y}" + (f", ref {r.reference}" if r.reference else "")
        if first <= r.paid_on <= last:
            journals.append(Journal(f"OP-{r.pk}", r.paid_on, [Line(OWNERS, r.amount, text, r.owner.name),
                                                             Line(money, -r.amount, text, r.owner.name)]))
        if r.voided_at and first <= _local_date(r.voided_at) <= last:
            text = f"Payment to owner voided: {r.void_reason}"
            journals.append(Journal(f"OP-{r.pk}-V", _local_date(r.voided_at), [
                Line(OWNERS, -r.amount, text, r.owner.name), Line(money, r.amount, text, r.owner.name)]))
    return journals


# ---------------------------------------------------------------------------
# The export
# ---------------------------------------------------------------------------


def build(membership: Membership, start: datetime.date, end: datetime.date, prop: Property | None = None) -> Export:
    """Everything in the ZIP, for the months start..end (first days) and the member's properties."""
    require(membership, "reports.view_financial")
    org = membership.organization
    properties = _properties(membership, prop)
    first, last = start, month_end(end)
    whole = prop is None and accessible_property_ids(membership) is None
    out = Export(organization=org, start=start, end=end, scope=prop, whole_organization=whole)

    tenants = Prefetch("lease__lease_tenants", queryset=LeaseTenant.objects.select_related("tenant"))
    out.invoices = list(Invoice.objects.filter(organization=org, lease__unit__property__in=properties,
                                               issue_date__gte=first, issue_date__lte=last)
                        .select_related("lease__unit__property").prefetch_related(tenants)
                        .order_by("issue_date", "number"))
    out.payments = list(Payment.objects.filter(organization=org, lease__unit__property__in=properties,
                                               status__in=(Payment.Status.CONFIRMED, Payment.Status.REVERSED),
                                               paid_at__gte=first, paid_at__lte=last)
                        .select_related("lease__unit__property", "tenant", "payment_account", "receipt")
                        .prefetch_related(tenants).order_by("paid_at", "pk"))
    expenses = list(Expense.objects.for_org(org).filter(property__in=properties, approved_at__isnull=False)
                    .select_related("property", "category", "supplier").order_by("paid_on", "pk"))
    out.expenses = [e for e in expenses if first <= e.paid_on <= last]

    journals = _ledger_journals(org, properties, first, last) + _deposit_journals(org, properties, first, last)
    journals += _expense_journals(expenses, first, last)
    if whole:
        journals += _owner_journals(org, first, last)
    out.journals = sorted(journals, key=lambda j: (j.date, j.number))
    return out


def _money(value: Decimal) -> str:
    return f"{value:.2f}" if value else ""


def _date(value) -> str:
    return value.isoformat() if value else ""


def _write(archive: zipfile.ZipFile, name: str, rows) -> None:
    buffer = io.StringIO()
    buffer.write("﻿")  # BOM so Excel reads UTF-8
    writer = csv.writer(buffer)
    for row in rows:
        writer.writerow(row)
    archive.writestr(name, buffer.getvalue())


def _journal_rows(export: Export):
    yield ["Journal no", "Date", "Account", "Debit", "Credit", "Description", "Name", "Property"]
    for j in export.journals:
        for line in j.lines:
            yield [j.number, _date(j.date), line.account, _money(line.debit), _money(line.credit), line.description,
                   line.name, line.place]


def _account_rows(export: Export):
    yield ["Account", "Type", "Debit", "Credit", "Net"]
    for a in export.accounts():
        yield [a.name, a.type, f"{a.debit:.2f}", f"{a.credit:.2f}", f"{a.net:.2f}"]
    yield ["Total", "", f"{export.total_debit:.2f}", f"{export.total_credit:.2f}",
           f"{export.total_debit - export.total_credit:.2f}"]


def _invoice_rows(export: Export):
    yield ["Invoice no", "Issue date", "Due date", "Period start", "Period end", "Tenant", "Lease", "Property",
           "Unit", "Subtotal", "Tax", "Total", "Paid", "Status", "Voided on"]
    for i in export.invoices:
        yield [i.number, _date(i.issue_date), _date(i.due_date), _date(i.period_start), _date(i.period_end),
               _tenant_name(i.lease), i.lease.number, i.lease.unit.property.name, i.lease.unit.code,
               f"{i.subtotal:.2f}", f"{i.tax_total:.2f}", f"{i.total:.2f}", f"{i.amount_paid:.2f}",
               i.get_status_display(), _date(_local_date(i.voided_at)) if i.voided_at else ""]


def _payment_rows(export: Export):
    yield ["Receipt no", "Date paid", "Tenant", "Lease", "Property", "Unit", "Method", "Money account",
           "Reference", "Amount", "Status", "Reversed on"]
    for p in export.payments:
        receipt = getattr(p, "receipt", None)
        yield [receipt.number if receipt else "", _date(p.paid_at), p.tenant.name if p.tenant_id
               else _tenant_name(p.lease), p.lease.number, p.lease.unit.property.name, p.lease.unit.code,
               p.get_method_display(), payment_account_name(p), p.reference, f"{p.amount:.2f}",
               p.get_status_display(), _date(_local_date(p.reversed_at)) if p.reversed_at else ""]


def _expense_rows(export: Export):
    yield ["Expense no", "Date paid", "Supplier", "KRA PIN", "Category", "Property", "Description", "Method",
           "Money account", "Reference", "Amount", "Status", "Voided on"]
    for e in export.expenses:
        yield [e.number, _date(e.paid_on), e.supplier.name if e.supplier_id else "",
               e.supplier.kra_pin if e.supplier_id else "", e.category.name, e.property.name, e.description,
               e.get_method_display(), METHOD_ACCOUNTS.get(e.method, METHOD_ACCOUNTS["OTHER"]), e.reference,
               f"{e.amount:.2f}", e.get_status_display(), _date(_local_date(e.voided_at)) if e.voided_at else ""]


README = """Accounting export: {org}
Months: {label}
Properties: {scope}
Currency: {currency}

journal.csv   Double-entry journal, one balanced journal per event (accrual basis).
              Debit and credit columns; import into QuickBooks Online as journal entries
              (Journal no, Date, Account, Debits, Credits, Description, Name, Location).
accounts.csv  Every account used, a suggested account type, and the period's debits,
              credits and net: a trial balance of the movements in these months.
invoices.csv  Invoices issued in these months.
payments.csv  Payments received in these months, including ones later reversed.
expenses.csv  Approved expenses paid in these months, including ones later voided.

Voids and reversals are journals of their own, on the day they happened, so months
already closed in your books do not change.
Deposits are held for tenants and are not income. Deposit clearing nets to zero when a
billed deposit is also recorded as held.
{owners}
"""


def to_zip(export: Export) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("README.txt", README.format(
            org=export.organization.name, label=export.label, currency=export.currency,
            scope=export.scope.name if export.scope else "All your properties",
            owners=("Payments to owners are included." if export.whole_organization else
                    "Payments to owners are left out: they belong to an owner, not a property.")))
        _write(archive, "journal.csv", _journal_rows(export))
        _write(archive, "accounts.csv", _account_rows(export))
        _write(archive, "invoices.csv", _invoice_rows(export))
        _write(archive, "payments.csv", _payment_rows(export))
        _write(archive, "expenses.csv", _expense_rows(export))
    return out.getvalue()
