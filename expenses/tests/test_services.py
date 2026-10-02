"""Expenses, suppliers and categories (D-067): the rules."""

import datetime
from decimal import Decimal

import pytest
from django.core.exceptions import PermissionDenied, ValidationError

from accounts.tests.factories import add_member, make_org, make_property
from audit.models import AuditEvent
from expenses import services
from expenses.models import Expense, ExpenseCategory, Supplier

from .conftest import PAID, jpeg, pdf, spend

pytestmark = pytest.mark.django_db

Status = Expense.Status


# ---------------------------------------------------------------------------
# Recording and approval
# ---------------------------------------------------------------------------


def test_an_approver_records_an_approved_numbered_expense(owner, prop, repairs):
    e = spend(owner, prop, repairs, "2,500.50", reference="sgh 12 abc")
    assert e.status == Status.APPROVED and e.approved_by == owner.user
    assert e.number == f"EXP-{datetime.date.today().year}-000001"
    assert e.amount == Decimal("2500.50") and e.reference_key == "SGH12ABC"
    assert AuditEvent.objects.filter(action="expenses.record").count() == 1
    assert spend(owner, prop, repairs).number.endswith("000002")


def test_a_manager_records_and_it_waits_for_approval(owner, org, prop, repairs):
    manager = add_member(org, "manager", properties=[prop])
    e = spend(manager, prop, repairs)
    assert e.status == Status.SUBMITTED and e.approved_at is None
    assert not services.can_approve(manager, e)
    with pytest.raises(PermissionDenied):
        services.approve(manager, e)
    assert list(services.to_approve(owner)) == [e]
    services.approve(owner, e)
    e.refresh_from_db()
    assert e.status == Status.APPROVED and e.approved_by == owner.user


def test_an_accountant_approves_only_on_their_properties(owner, org, prop, repairs):
    other = make_property(org, name="Baobab Flats")
    accountant = add_member(org, "accountant", properties=[prop])
    manager = add_member(org, "manager", all_properties=True)
    here, there = spend(manager, prop, repairs), spend(manager, other, repairs)
    assert list(services.to_approve(accountant)) == [here]
    with pytest.raises(PermissionDenied):
        services.approve(accountant, there)
    # Recording on their own property is approved at once.
    assert spend(accountant, prop, repairs).status == Status.APPROVED


def test_roles_without_submit_cannot_record(org, prop, repairs):
    for key in ("caretaker", "viewer", "leasing_agent"):
        member = add_member(org, key, all_properties=True)
        assert services.recordable_properties(member) == []
        with pytest.raises(PermissionDenied):
            spend(member, prop, repairs)


def test_a_member_cannot_record_outside_their_properties(org, prop, repairs):
    manager = add_member(org, "manager", properties=[make_property(org)])
    with pytest.raises(PermissionDenied):
        spend(manager, prop, repairs)


def test_another_organizations_property_or_category_is_refused(owner, prop, repairs):
    stranger = make_org()
    with pytest.raises(PermissionDenied):
        spend(stranger, prop, repairs)
    theirs = make_property(stranger.organization)
    with pytest.raises(ValidationError) as exc:
        spend(stranger, theirs, repairs)
    assert "category" in exc.value.error_dict


@pytest.mark.parametrize("amount", ["0", "-5", "abc", "", "100000000"])
def test_bad_amounts_are_refused(owner, prop, repairs, amount):
    with pytest.raises(ValidationError) as exc:
        spend(owner, prop, repairs, amount)
    assert "amount" in exc.value.error_dict
    assert not Expense.objects.exists()


def test_amount_is_rounded_to_cents(owner, prop, repairs):
    assert spend(owner, prop, repairs, "0.01").amount == Decimal("0.01")


def test_other_fields_are_checked(owner, org, prop, repairs):
    supplier = services.create_supplier(owner, name="Otieno Plumbing")
    services.archive_supplier(owner, supplier)
    with pytest.raises(ValidationError) as exc:
        spend(owner, prop, repairs, description="  ", method="BITCOIN", paid_on=datetime.date(2999, 1, 1),
              supplier=supplier)
    assert set(exc.value.error_dict) == {"description", "method", "paid_on", "supplier"}


def test_the_future_is_judged_by_today(owner, prop, repairs):
    today = datetime.date(2025, 3, 1)
    spend(owner, prop, repairs, paid_on=today, today=today)
    with pytest.raises(ValidationError):
        spend(owner, prop, repairs, paid_on=today + datetime.timedelta(days=1), today=today)


def test_an_archived_property_is_refused(owner, prop, repairs):
    prop.archive(owner.user)
    with pytest.raises(ValidationError) as exc:
        spend(owner, prop, repairs)
    assert "property" in exc.value.error_dict


def test_a_reference_is_used_once_ignoring_case_and_spaces(owner, org, prop, repairs):
    first = spend(owner, prop, repairs, reference="SGH12ABC")
    with pytest.raises(ValidationError) as exc:
        spend(owner, make_property(org), repairs, reference=" sgh 12abc ")
    assert first.number in exc.value.messages[0]
    # Blank references never clash.
    spend(owner, prop, repairs)
    spend(owner, prop, repairs)
    # Once voided, the reference is free again.
    services.void(owner, first, reason="Recorded twice")
    assert spend(owner, prop, repairs, reference="SGH12ABC").status == Status.APPROVED


def test_the_same_reference_in_another_organization_is_fine(owner, prop, repairs):
    spend(owner, prop, repairs, reference="SGH12ABC")
    other = make_org()
    theirs = services.categories(other.organization).first()
    spend(other, make_property(other.organization), theirs, reference="SGH12ABC")


def test_reject_and_void_need_a_reason_and_the_right_status(owner, org, prop, repairs):
    manager = add_member(org, "manager", all_properties=True)
    e = spend(manager, prop, repairs)
    with pytest.raises(ValidationError):
        services.reject(owner, e, reason=" ")
    with pytest.raises(ValidationError):
        services.void(owner, e, reason="Not yet approved")
    services.reject(owner, e, reason="Wrong property")
    e.refresh_from_db()
    assert (e.status, e.reject_reason, e.rejected_by) == (Status.REJECTED, "Wrong property", owner.user)
    with pytest.raises(ValidationError):
        services.approve(owner, e)

    ok = spend(owner, prop, repairs)
    with pytest.raises(ValidationError):
        services.reject(owner, ok, reason="Too late")
    with pytest.raises(PermissionDenied):
        services.void(manager, ok, reason="Mine")
    services.void(owner, ok, reason="Recorded twice")
    ok.refresh_from_db()
    assert ok.status == Status.VOIDED and not services.can_void(owner, ok)
    actions = set(AuditEvent.objects.filter(action__startswith="expenses.").values_list("action", flat=True))
    assert {"expenses.record", "expenses.reject", "expenses.void"} <= actions


# ---------------------------------------------------------------------------
# Receipts
# ---------------------------------------------------------------------------


def test_a_photo_receipt_is_re_encoded(owner, prop, repairs):
    e = spend(owner, prop, repairs, receipt=jpeg("phone.png"))
    assert e.receipt.name.endswith(".jpg") and not e.receipt_is_pdf


def test_a_pdf_receipt_is_kept(owner, prop, repairs):
    e = spend(owner, prop, repairs, receipt=pdf("scan.pdf"))
    assert e.receipt_is_pdf
    with e.receipt.open("rb") as f:
        assert f.read(5) == b"%PDF-"


def test_other_files_are_refused(owner, prop, repairs):
    from django.core.files.uploadedfile import SimpleUploadedFile

    with pytest.raises(ValidationError) as exc:
        spend(owner, prop, repairs, receipt=SimpleUploadedFile("x.exe", b"MZ\x90\x00", content_type="image/jpeg"))
    assert "receipt" in exc.value.error_dict
    with pytest.raises(ValidationError) as exc:
        spend(owner, prop, repairs, receipt=pdf(size=services.MAX_PDF_BYTES + 1))
    assert "10 MB" in exc.value.messages[0]
    assert not Expense.objects.exists()


def test_a_receipt_is_added_later_once(owner, org, prop, repairs):
    manager = add_member(org, "manager", all_properties=True)
    other_manager = add_member(org, "manager", all_properties=True)
    e = spend(manager, prop, repairs)
    assert services.can_add_receipt(manager, e) and not services.can_add_receipt(other_manager, e)
    with pytest.raises(PermissionDenied):
        services.add_receipt(other_manager, e, jpeg())
    services.add_receipt(manager, e, jpeg())
    e.refresh_from_db()
    assert e.receipt and not services.can_add_receipt(owner, e)
    with pytest.raises(ValidationError):
        services.add_receipt(owner, e, pdf())
    assert AuditEvent.objects.filter(action="expenses.receipt").count() == 1


# ---------------------------------------------------------------------------
# Categories and suppliers
# ---------------------------------------------------------------------------


def test_default_categories_are_created_once(org):
    assert services.categories(org).count() == len(services.DEFAULT_CATEGORIES)
    services.ensure_categories(org)
    assert ExpenseCategory.all_objects.filter(organization=org).count() == len(services.DEFAULT_CATEGORIES)


def test_categories_are_managed_by_organization_wide_approvers(owner, org, prop, repairs):
    for m in (add_member(org, "manager", all_properties=True), add_member(org, "accountant", properties=[prop])):
        assert not services.can_manage_categories(m)
        with pytest.raises(PermissionDenied):
            services.create_category(m, name="Pest control")
    accountant = add_member(org, "accountant", all_properties=True)
    pest = services.create_category(accountant, name="  Pest   control ")
    assert pest.name == "Pest control"
    with pytest.raises(ValidationError):
        services.create_category(owner, name="pest CONTROL")
    with pytest.raises(ValidationError):
        services.rename_category(owner, repairs, name="Pest Control")
    services.rename_category(owner, repairs, name="Repairs")
    services.archive_category(owner, pest)
    assert pest not in services.categories(org)
    with pytest.raises(ValidationError):
        spend(owner, prop, pest)
    services.restore_category(owner, pest)
    assert pest in services.categories(org)


def test_the_last_category_cannot_be_archived(owner, org):
    cats = list(services.categories(org))
    for c in cats[:-1]:
        services.archive_category(owner, c)
    with pytest.raises(ValidationError):
        services.archive_category(owner, cats[-1])


def test_suppliers_need_contractors_manage(owner, org):
    manager = add_member(org, "manager", all_properties=True)
    services.create_supplier(manager, name="Otieno Plumbing")
    for key in ("accountant", "caretaker"):
        with pytest.raises(PermissionDenied):
            services.create_supplier(add_member(org, key, all_properties=True), name="X")


def test_supplier_fields_are_checked_and_edits_audited(owner):
    with pytest.raises(ValidationError) as exc:
        services.create_supplier(owner, name="Otieno", kra_pin="12345")
    assert "kra_pin" in exc.value.error_dict
    s = services.create_supplier(owner, name="Otieno", kra_pin="a012345678z", phone=" 0712 ")
    assert (s.kra_pin, s.phone) == ("A012345678Z", "0712")
    services.update_supplier(owner, s, name="Otieno Plumbing", kra_pin=s.kra_pin, phone=s.phone)
    event = AuditEvent.objects.get(action="expenses.supplier_edit")
    assert event.changes == {"name": ["Otieno", "Otieno Plumbing"]}
    stranger = make_org()
    with pytest.raises(PermissionDenied):
        services.update_supplier(stranger, s, name="Mine")
    with pytest.raises(PermissionDenied):
        services.archive_supplier(stranger, s)


def test_supplier_totals_count_approved_expenses_the_member_can_see(owner, org, prop, repairs):
    other = make_property(org)
    s = services.create_supplier(owner, name="Otieno")
    spend(owner, prop, repairs, "1000", supplier=s)
    spend(owner, other, repairs, "500", supplier=s)
    services.void(owner, spend(owner, prop, repairs, "9999", supplier=s), reason="Twice")
    spend(add_member(org, "manager", all_properties=True), prop, repairs, "7000", supplier=s)  # waiting
    assert services.supplier_totals(owner) == {s.pk: (2, Decimal("1500.00"))}
    assert services.supplier_totals(add_member(org, "accountant", properties=[prop])) == {
        s.pk: (1, Decimal("1000.00"))}
    assert Supplier.objects.count() == 1


def test_totals_split_approved_and_waiting(owner, org, prop, repairs):
    security = services.categories(org).get(name="Security")
    spend(owner, prop, repairs, "1000")
    spend(owner, prop, security, "3000")
    spend(add_member(org, "manager", all_properties=True), prop, repairs, "250")
    t = services.totals(Expense.objects.all())
    assert (t.approved, t.approved_count, t.waiting, t.waiting_count) == (
        Decimal("4000.00"), 2, Decimal("250.00"), 1)
    assert [r["category__name"] for r in t.by_category] == ["Security", "Repairs and maintenance"]
    w = services.waiting_summary(org, [prop], PAID, PAID)
    assert (w.waiting, w.waiting_count) == (Decimal("250.00"), 1)
