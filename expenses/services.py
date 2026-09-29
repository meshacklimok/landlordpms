"""Expenses, suppliers and categories (D-067). Views stay thin; the rules live here."""

import datetime
import re
import secrets
from dataclasses import dataclass, field
from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.base import ContentFile
from django.db import IntegrityError, transaction
from django.db.models import Count, Sum
from django.db.models.functions import Lower
from django.utils import timezone
from django.utils.translation import gettext as _
from django.utils.translation import gettext_noop

from accounts.models import Membership, Organization
from accounts.permissions import accessible_property_ids, can, require, visible_properties
from audit import services as audit
from core.money import ZERO, parse_money
from core.numbering import next_number
from inspections import photos
from properties.models import Property

from .models import Expense, ExpenseCategory, Supplier

Status = Expense.Status

# Created the first time an organization opens expenses (D-067 item 5). From then on they are the
# organization's own records, renamed like any other, so they are stored as written here.
DEFAULT_CATEGORIES = (
    gettext_noop("Repairs and maintenance"),
    gettext_noop("Water"),
    gettext_noop("Electricity"),
    gettext_noop("Security"),
    gettext_noop("Cleaning and garbage"),
    gettext_noop("Caretaker and staff wages"),
    gettext_noop("Insurance"),
    gettext_noop("Land rates and ground rent"),
    gettext_noop("Professional and legal fees"),
    gettext_noop("Other"),
)
MAX_PDF_BYTES = 10 * 1024 * 1024
MAX_AMOUNT = Decimal("99999999.99")


# ---------------------------------------------------------------------------
# Scope
# ---------------------------------------------------------------------------


def visible_expenses(membership: Membership, queryset=None):
    qs = (queryset if queryset is not None else Expense.objects.all()).for_org(membership.organization)
    return qs.filter(property__in=visible_properties(membership, Property.all_objects.all()))


def approvable_property_ids(membership: Membership) -> list[int]:
    return [p.pk for p in visible_properties(membership, Property.all_objects.all())
            if can(membership, "expenses.approve", p)]


def to_approve(membership: Membership):
    """Waiting expenses on the properties where the member may approve."""
    if not can(membership, "expenses.approve"):
        return Expense.objects.none()
    return visible_expenses(membership).filter(status=Status.SUBMITTED,
                                               property_id__in=approvable_property_ids(membership))


def recordable_properties(membership: Membership):
    """Live properties where the member may record an expense."""
    return [p for p in visible_properties(membership, Property.objects.all()).order_by("name")
            if can(membership, "expenses.submit", p)]


def approved(organization: Organization, properties, first: datetime.date, last: datetime.date):
    """Approved expenses on these properties paid between first and last: what reports count."""
    return Expense.objects.for_org(organization).filter(status=Status.APPROVED, property__in=properties,
                                                        paid_on__gte=first, paid_on__lte=last)


def _check(actor: Membership, prop: Property, capability: str) -> None:
    if prop.organization_id != actor.organization_id:
        raise PermissionDenied(_("That record belongs to another organization."))
    require(actor, capability, prop)


def _audit(action, actor, obj, request, changes=None):
    audit.record(action, actor=actor.user, organization=actor.organization, obj=obj, request=request,
                 changes=changes or {})


# ---------------------------------------------------------------------------
# Categories
# ---------------------------------------------------------------------------


def ensure_categories(organization: Organization) -> None:
    """Creates the default categories once, when the organization has never had any."""
    if ExpenseCategory.all_objects.filter(organization=organization).exists():
        return
    with transaction.atomic():
        ExpenseCategory.objects.bulk_create(
            [ExpenseCategory(organization=organization, name=name) for name in DEFAULT_CATEGORIES],
            ignore_conflicts=True)


def categories(organization: Organization):
    ensure_categories(organization)
    return ExpenseCategory.objects.for_org(organization).order_by("name")


def can_manage_categories(membership: Membership) -> bool:
    return can(membership, "expenses.approve") and accessible_property_ids(membership) is None


def _require_categories(actor: Membership) -> None:
    if not can_manage_categories(actor):
        raise PermissionDenied("expenses.approve")


def _clean_name(name: str, field_name: str, limit: int, message: str) -> str:
    name = " ".join((name or "").split())[:limit]
    if not name:
        raise ValidationError({field_name: message})
    return name


def _unique_category(org, name, exclude=None) -> None:
    qs = ExpenseCategory.objects.for_org(org).annotate(lname=Lower("name")).filter(lname=name.lower())
    if exclude is not None:
        qs = qs.exclude(pk=exclude.pk)
    if qs.exists():
        raise ValidationError({"name": _("There is already a category with that name.")})


@transaction.atomic
def create_category(actor: Membership, *, name: str, request=None) -> ExpenseCategory:
    _require_categories(actor)
    name = _clean_name(name, "name", 60, _("Give the category a name."))
    _unique_category(actor.organization, name)
    category = ExpenseCategory.objects.create(organization=actor.organization, name=name)
    _audit("expenses.category_create", actor, category, request, {"name": [None, name]})
    return category


@transaction.atomic
def rename_category(actor: Membership, category: ExpenseCategory, *, name: str, request=None) -> ExpenseCategory:
    _require_categories(actor)
    if category.organization_id != actor.organization_id:
        raise PermissionDenied("expenses.approve")
    name = _clean_name(name, "name", 60, _("Give the category a name."))
    if name != category.name:
        _unique_category(actor.organization, name, exclude=category)
        before, category.name = category.name, name
        category.save(update_fields=["name", "updated_at"])
        _audit("expenses.category_rename", actor, category, request, {"name": [before, name]})
    return category


@transaction.atomic
def archive_category(actor: Membership, category: ExpenseCategory, *, request=None) -> None:
    _require_categories(actor)
    if category.organization_id != actor.organization_id:
        raise PermissionDenied("expenses.approve")
    if ExpenseCategory.objects.for_org(actor.organization).exclude(pk=category.pk).count() == 0:
        raise ValidationError(_("Keep at least one category."))
    category.archive(actor.user)
    _audit("expenses.category_archive", actor, category, request, {"archived": [False, True]})


@transaction.atomic
def restore_category(actor: Membership, category: ExpenseCategory, *, request=None) -> None:
    _require_categories(actor)
    if category.organization_id != actor.organization_id:
        raise PermissionDenied("expenses.approve")
    _unique_category(actor.organization, category.name, exclude=category)
    category.restore()
    _audit("expenses.category_restore", actor, category, request, {"archived": [True, False]})


# ---------------------------------------------------------------------------
# Suppliers
# ---------------------------------------------------------------------------

SUPPLIER_FIELDS = ("name", "phone", "email", "kra_pin", "payment_details", "note")
KRA_PIN = re.compile(r"^[A-Z]\d{9}[A-Z]$")


def _require_suppliers(actor: Membership, supplier: Supplier | None = None) -> None:
    require(actor, "contractors.manage")
    if supplier is not None and supplier.organization_id != actor.organization_id:
        raise PermissionDenied("contractors.manage")


def _clean_supplier(**fields) -> dict:
    out = {
        "name": _clean_name(fields.get("name"), "name", 120, _("Give the supplier's name.")),
        "phone": (fields.get("phone") or "").strip()[:20],
        "email": (fields.get("email") or "").strip()[:254],
        "kra_pin": (fields.get("kra_pin") or "").strip().upper()[:11],
        "payment_details": (fields.get("payment_details") or "").strip()[:200],
        "note": (fields.get("note") or "").strip()[:300],
    }
    if out["kra_pin"] and not KRA_PIN.match(out["kra_pin"]):
        raise ValidationError({"kra_pin": _("A KRA PIN is a letter, 9 digits and a letter, like A012345678Z.")})
    return out


@transaction.atomic
def create_supplier(actor: Membership, *, request=None, **fields) -> Supplier:
    _require_suppliers(actor)
    fields = _clean_supplier(**fields)
    supplier = Supplier.objects.create(organization=actor.organization, created_by=actor.user, **fields)
    _audit("expenses.supplier_create", actor, supplier, request, {k: [None, v] for k, v in fields.items() if v})
    return supplier


@transaction.atomic
def update_supplier(actor: Membership, supplier: Supplier, *, request=None, **fields) -> Supplier:
    _require_suppliers(actor, supplier)
    fields = _clean_supplier(**fields)
    changes = {k: [getattr(supplier, k), v] for k, v in fields.items() if getattr(supplier, k) != v}
    if changes:
        for k, v in fields.items():
            setattr(supplier, k, v)
        supplier.save()
        _audit("expenses.supplier_edit", actor, supplier, request, changes)
    return supplier


@transaction.atomic
def archive_supplier(actor: Membership, supplier: Supplier, *, request=None) -> None:
    _require_suppliers(actor, supplier)
    supplier.archive(actor.user)
    _audit("expenses.supplier_archive", actor, supplier, request, {"archived": [False, True]})


@transaction.atomic
def restore_supplier(actor: Membership, supplier: Supplier, *, request=None) -> None:
    _require_suppliers(actor, supplier)
    supplier.restore()
    _audit("expenses.supplier_restore", actor, supplier, request, {"archived": [True, False]})


def supplier_totals(membership: Membership) -> dict[int, tuple[int, Decimal]]:
    """Supplier pk → (approved expenses, total paid), on the properties the member can see."""
    rows = (visible_expenses(membership).filter(status=Status.APPROVED, supplier__isnull=False)
            .values("supplier_id").annotate(n=Count("pk"), total=Sum("amount")))
    return {r["supplier_id"]: (r["n"], r["total"]) for r in rows}


# ---------------------------------------------------------------------------
# Receipts
# ---------------------------------------------------------------------------


def process_receipt(upload) -> ContentFile:
    """A PDF is checked and kept; anything else must be a photo and is re-encoded (D-067 item 4)."""
    head = upload.read(5)
    upload.seek(0)
    if head == b"%PDF-":
        if upload.size > MAX_PDF_BYTES:
            raise ValidationError({"receipt": _("The PDF is larger than 10 MB.")})
        return ContentFile(upload.read(), name=f"{secrets.token_hex(16)}.pdf")
    try:
        image, _w, _h = photos.process(upload)
    except ValidationError as exc:
        raise ValidationError({"receipt": _("Use a photo (JPEG or PNG) or a PDF.")
                               if "image" in getattr(exc, "error_dict", {}) else exc.messages}) from None
    return image


# ---------------------------------------------------------------------------
# Expenses
# ---------------------------------------------------------------------------


def reference_key(reference: str) -> str:
    return re.sub(r"\s+", "", reference or "").upper()[:60]


def _duplicate(org, key: str) -> Expense | None:
    if not key:
        return None
    return Expense.objects.for_org(org).filter(reference_key=key, status__in=Expense.LIVE).first()


@transaction.atomic
def record_expense(actor: Membership, prop: Property, *, category: ExpenseCategory, description: str, amount,
                   paid_on: datetime.date, method: str, reference: str = "", supplier: Supplier | None = None,
                   receipt=None, today: datetime.date | None = None, request=None) -> Expense:
    """Records a paid expense. Approved at once when the recorder may approve on the property (D-067 item 2)."""
    _check(actor, prop, "expenses.submit")
    today = today or timezone.localdate()
    org = actor.organization
    errors = {}
    if prop.is_archived:
        errors["property"] = _("This property is archived.")
    if category is None or category.organization_id != org.id or category.is_archived:
        errors["category"] = _("Choose a category.")
    if supplier is not None and (supplier.organization_id != org.id or supplier.is_archived):
        errors["supplier"] = _("Choose a supplier from the list.")
    description = " ".join((description or "").split())[:200]
    if not description:
        errors["description"] = _("Say what was paid for.")
    try:
        amount = parse_money(amount, allow_zero=False)
        if amount > MAX_AMOUNT:
            errors["amount"] = _("That amount is too large.")
    except ValidationError as exc:
        errors["amount"] = exc.messages[0]
    if paid_on is None:
        errors["paid_on"] = _("Enter the date paid.")
    elif paid_on > today:
        errors["paid_on"] = _("The date paid cannot be in the future.")
    if method not in Expense.Method.values:
        errors["method"] = _("Choose how it was paid.")
    reference = " ".join((reference or "").split())[:60]
    key = reference_key(reference)
    existing = _duplicate(org, key)
    if existing is not None:
        errors["reference"] = _("That reference is already on expense %(number)s.") % {"number": existing.number}
    if errors:
        raise ValidationError(errors)
    fields = {}
    if receipt:
        fields["receipt"] = process_receipt(receipt)
    now = timezone.now()
    at_once = can(actor, "expenses.approve", prop)
    if at_once:
        fields.update(status=Status.APPROVED, approved_at=now, approved_by=actor.user)
    try:
        with transaction.atomic():
            expense = Expense.objects.create(
                organization=org, property=prop, category=category, supplier=supplier, description=description,
                amount=amount, paid_on=paid_on, method=method, reference=reference, reference_key=key,
                recorded_by=actor.user, number=next_number(org, "expense", prefix="EXP", period=str(today.year)),
                **fields)
    except IntegrityError:
        raise ValidationError({"reference": _("That reference is already on another expense.")}) from None
    _audit("expenses.record", actor, expense, request, {
        "property": [None, prop.name], "category": [None, category.name], "amount": [None, str(amount)],
        "paid_on": [None, paid_on.isoformat()], "method": [None, method],
        **({"reference": [None, reference]} if reference else {}),
        **({"supplier": [None, supplier.name]} if supplier else {}),
        **({"receipt": [None, True]} if receipt else {}),
        "status": [None, expense.status]})
    return expense


def _lock(expense: Expense) -> Expense:
    return Expense.objects.select_for_update(of=("self",)).select_related("property").get(pk=expense.pk)


def can_approve(membership: Membership, expense: Expense) -> bool:
    return (expense.organization_id == membership.organization_id and expense.status == Status.SUBMITTED
            and can(membership, "expenses.approve", expense.property))


def can_void(membership: Membership, expense: Expense) -> bool:
    return (expense.organization_id == membership.organization_id and expense.status == Status.APPROVED
            and can(membership, "expenses.approve", expense.property))


def can_add_receipt(membership: Membership, expense: Expense) -> bool:
    if expense.organization_id != membership.organization_id or expense.receipt or not expense.is_live:
        return False
    if can(membership, "expenses.approve", expense.property):
        return True
    return expense.recorded_by_id == membership.user_id and can(membership, "expenses.submit", expense.property)


def _reason(reason: str, message: str) -> str:
    reason = (reason or "").strip()[:300]
    if not reason:
        raise ValidationError({"reason": message})
    return reason


@transaction.atomic
def approve(actor: Membership, expense: Expense, *, request=None) -> Expense:
    _check(actor, expense.property, "expenses.approve")
    expense = _lock(expense)
    if expense.status != Status.SUBMITTED:
        raise ValidationError(_("This expense is not waiting for approval."))
    expense.status = Status.APPROVED
    expense.approved_at = timezone.now()
    expense.approved_by = actor.user
    expense.save()
    _audit("expenses.approve", actor, expense, request, {"status": [Status.SUBMITTED, Status.APPROVED]})
    return expense


@transaction.atomic
def reject(actor: Membership, expense: Expense, *, reason: str, request=None) -> Expense:
    _check(actor, expense.property, "expenses.approve")
    expense = _lock(expense)
    if expense.status != Status.SUBMITTED:
        raise ValidationError(_("Only an expense waiting for approval can be rejected."))
    reason = _reason(reason, _("Say why the expense is rejected."))
    expense.status = Status.REJECTED
    expense.rejected_at = timezone.now()
    expense.rejected_by = actor.user
    expense.reject_reason = reason
    expense.save()
    _audit("expenses.reject", actor, expense, request,
           {"status": [Status.SUBMITTED, Status.REJECTED], "reason": [None, reason]})
    return expense


@transaction.atomic
def void(actor: Membership, expense: Expense, *, reason: str, request=None) -> Expense:
    _check(actor, expense.property, "expenses.approve")
    expense = _lock(expense)
    if expense.status != Status.APPROVED:
        raise ValidationError(_("Only an approved expense can be voided."))
    reason = _reason(reason, _("Say why the expense is voided."))
    expense.status = Status.VOIDED
    expense.voided_at = timezone.now()
    expense.voided_by = actor.user
    expense.void_reason = reason
    expense.save()
    _audit("expenses.void", actor, expense, request,
           {"status": [Status.APPROVED, Status.VOIDED], "reason": [None, reason]})
    return expense


@transaction.atomic
def add_receipt(actor: Membership, expense: Expense, receipt, *, request=None) -> Expense:
    expense = _lock(expense)
    if not can_add_receipt(actor, expense):
        if expense.receipt:
            raise ValidationError(_("This expense already has a receipt."))
        raise PermissionDenied("expenses.submit")
    if not receipt:
        raise ValidationError({"receipt": _("Choose a photo or a PDF of the receipt.")})
    expense.receipt = process_receipt(receipt)
    expense.save()
    _audit("expenses.receipt", actor, expense, request, {"receipt": [False, True]})
    return expense


# ---------------------------------------------------------------------------
# The list
# ---------------------------------------------------------------------------


@dataclass
class Totals:
    approved: Decimal = ZERO
    approved_count: int = 0
    waiting: Decimal = ZERO
    waiting_count: int = 0
    by_category: list = field(default_factory=list)


def totals(queryset) -> Totals:
    out = Totals()
    for r in queryset.filter(status__in=Expense.LIVE).values("status").annotate(n=Count("pk"), total=Sum("amount")):
        if r["status"] == Status.APPROVED:
            out.approved, out.approved_count = r["total"], r["n"]
        else:
            out.waiting, out.waiting_count = r["total"], r["n"]
    out.by_category = list(queryset.filter(status=Status.APPROVED).values("category__name")
                           .annotate(total=Sum("amount")).order_by("-total", "category__name"))
    return out


def waiting_summary(organization: Organization, properties, first: datetime.date, last: datetime.date) -> Totals:
    """Waiting expenses in a report's period, for its note."""
    qs = Expense.objects.for_org(organization).filter(property__in=properties, paid_on__gte=first,
                                                      paid_on__lte=last, status=Status.SUBMITTED)
    agg = qs.aggregate(n=Count("pk"), total=Sum("amount"))
    return Totals(waiting=agg["total"] or ZERO, waiting_count=agg["n"])
