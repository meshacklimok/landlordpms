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
    assert [r["name"] for r in t.by_category] == ["Security", "Repairs and maintenance"]
    w = services.waiting_summary(org, [prop], PAID, PAID)
    assert (w.waiting, w.waiting_count) == (Decimal("250.00"), 1)


def test_totals_break_down_approved_spending(owner, org, prop, repairs):
    """D-110: by property and supplier, the average and spending a month count approved expenses only."""
    other = make_property(org, name="Baobab Flats")
    s = services.create_supplier(owner, name="Kamau Plumbing")
    spend(owner, prop, repairs, "1000", supplier=s)
    spend(owner, other, repairs, "3000", paid_on=PAID - datetime.timedelta(days=40))
    spend(add_member(org, "manager", all_properties=True), prop, repairs, "250")  # waiting
    t = services.totals(Expense.objects.all())
    assert [(r["name"], r["total"], r["n"]) for r in t.by_property] == [
        ("Baobab Flats", Decimal("3000.00"), 1), ("Acacia Court", Decimal("1000.00"), 1)]
    assert [(r["name"], r["n"]) for r in t.by_supplier] == [("Kamau Plumbing", 1)]
    assert (t.months, t.average, t.monthly) == (2, Decimal("2000.00"), Decimal("2000.00"))
    empty = services.totals(Expense.objects.none())
    assert empty.average is None and empty.monthly is None


def _edit(actor, expense, **kw):
    values = {"prop": expense.property, "category": expense.category, "description": expense.description,
              "amount": expense.amount, "paid_on": expense.paid_on, "method": expense.method,
              "reference": expense.reference, "supplier": expense.supplier}
    values.update(kw)
    return services.update_expense(actor, expense, **values)


def test_an_owner_corrects_an_approved_expense_and_it_stays_approved(owner, org, prop, repairs):
    other = make_property(org, name="Baobab Flats")
    e = spend(owner, prop, repairs, "2500", reference="ABC123")
    _edit(owner, e, amount="2,750", prop=other, reference="abc 124", description="Gate lock and hinges")
    e.refresh_from_db()
    assert (e.amount, e.property, e.reference_key, e.status) == (Decimal("2750"), other, "ABC124", Status.APPROVED)
    event = AuditEvent.objects.get(action="expenses.edit")
    assert event.changes["amount"] == ["2500.00", "2750.00"]
    assert event.changes["property"] == ["Acacia Court", "Baobab Flats"] and "reference_key" not in event.changes
    _edit(owner, e)  # nothing changed, nothing recorded
    assert AuditEvent.objects.filter(action="expenses.edit").count() == 1


def test_the_recorder_edits_only_until_it_is_approved(owner, org, prop, repairs):
    manager = add_member(org, "manager", properties=[prop])
    e = spend(manager, prop, repairs)
    assert services.can_edit(manager, e)
    _edit(manager, e, amount="3000")
    e.refresh_from_db()
    assert e.amount == 3000 and e.status == Status.SUBMITTED  # still needs a checker
    assert not services.can_edit(add_member(org, "manager", properties=[prop]), e)  # not theirs
    services.approve(owner, e)
    e.refresh_from_db()
    assert not services.can_edit(manager, e)
    with pytest.raises(PermissionDenied):
        _edit(manager, e, amount="1")


def test_edits_keep_the_same_checks(owner, org, prop, repairs):
    first = spend(owner, prop, repairs, reference="QWE1")
    second = spend(owner, prop, repairs)
    with pytest.raises(ValidationError) as exc:
        _edit(owner, second, reference="qwe 1", amount="0")
    assert {"reference", "amount"} <= set(exc.value.message_dict)
    _edit(owner, first, reference="QWE1", amount="10")  # its own reference is not a duplicate
    # A category archived since recording may stay, but not be newly chosen.
    old = ExpenseCategory.objects.create(organization=org, name="Old")
    e = spend(owner, prop, old)
    services.archive_category(owner, old)
    _edit(owner, e, amount="99")
    with pytest.raises(ValidationError):
        _edit(owner, second, category=old)
    # Voided or rejected expenses are history.
    services.void(owner, first, reason="Duplicate")
    first.refresh_from_db()
    assert not services.can_edit(owner, first)


def test_moving_to_a_property_outside_scope_is_refused(owner, org, prop, repairs):
    other = make_property(org, name="Baobab Flats")
    manager = add_member(org, "manager", properties=[prop])
    e = spend(manager, prop, repairs)
    with pytest.raises(PermissionDenied):
        _edit(manager, e, prop=other)


# ---------------------------------------------------------------------------
# One payment shared by several properties (D-197)
# ---------------------------------------------------------------------------


def _three(org):
    return [make_property(org, name=n) for n in ("Acacia", "Baobab", "Cedar")]


def test_equal_shares_add_up_to_what_was_paid(owner, org, repairs):
    a, b, c = _three(org)
    made = services.record_split(owner, [a, b, c], rule="equal", category=repairs, description="Guard",
                                 amount="10000", paid_on=PAID, method="CASH", reference="ab12")
    assert [e.amount for e in made] == [Decimal("3333.33"), Decimal("3333.33"), Decimal("3333.34")]
    assert sum(e.amount for e in made) == Decimal("10000")
    assert len({e.split_key for e in made}) == 1 and made[0].split_total == Decimal("10000")
    assert made[0].reference_key == "AB12" and made[1].reference_key == "" and all(e.status == Status.APPROVED for e in made)
    assert len(services.split_group(made[1])) == 3
    assert services.totals(Expense.objects.all()).approved == Decimal("10000")


def test_shares_by_units_and_by_percent(owner, org, repairs):
    from properties import services as property_services
    a, b, _c = _three(org)
    for code in ("1", "2", "3"):
        property_services.create_unit(owner, a, code=code)
    property_services.create_unit(owner, b, code="1")
    made = services.record_split(owner, [a, b], rule="units", category=repairs, description="Guard", amount="4000",
                                 paid_on=PAID, method="CASH")
    assert [e.amount for e in made] == [Decimal("3000"), Decimal("1000")]
    made = services.record_split(owner, [a, b], rule="percent", percents={a.pk: Decimal("70"), b.pk: Decimal("30")},
                                 category=repairs, description="Bins", amount="1000", paid_on=PAID, method="CASH")
    assert [e.amount for e in made] == [Decimal("700"), Decimal("300")]
    with pytest.raises(ValidationError):
        services.record_split(owner, [a, b], rule="percent", percents={a.pk: Decimal("70"), b.pk: Decimal("20")},
                              category=repairs, description="Bins", amount="1000", paid_on=PAID, method="CASH")
    with pytest.raises(ValidationError):  # no units to count
        services.record_split(owner, [a, _c], rule="units", category=repairs, description="x", amount="1000",
                              paid_on=PAID, method="CASH")


def test_a_split_needs_two_properties_and_a_big_enough_amount(owner, org, repairs):
    a, b, _c = _three(org)
    with pytest.raises(ValidationError):
        services.record_split(owner, [a], rule="equal", category=repairs, description="x", amount="100",
                              paid_on=PAID, method="CASH")
    with pytest.raises(ValidationError):
        services.record_split(owner, [a, b, _c], rule="equal", category=repairs, description="x", amount="0.02",
                              paid_on=PAID, method="CASH")
    assert not Expense.objects.exists()


def test_shares_are_approved_rejected_and_voided_together(owner, org, repairs):
    a, b, _c = _three(org)
    manager = add_member(org, "manager", properties=[a])
    with pytest.raises(PermissionDenied):  # a manager may not record on a property outside their scope
        services.record_split(manager, [a, b], rule="equal", category=repairs, description="x", amount="100",
                              paid_on=PAID, method="CASH")
    both = add_member(org, "manager", properties=[a, b])
    made = services.record_split(both, [a, b], rule="equal", category=repairs, description="Guard", amount="1000",
                                 paid_on=PAID, method="CASH")
    assert {e.status for e in made} == {Status.SUBMITTED}
    assert not services.can_approve(both, made[0])
    services.approve(owner, made[1])
    assert {e.status for e in Expense.objects.all()} == {Status.APPROVED}
    assert not services.can_edit(owner, made[0])
    services.void(owner, made[0], reason="Recorded twice")
    assert {e.status for e in Expense.objects.all()} == {Status.VOIDED}
    again = services.record_split(both, [a, b], rule="equal", category=repairs, description="Guard", amount="1000",
                                  paid_on=PAID, method="CASH")
    services.reject(owner, again[0], reason="Not ours")
    assert Expense.objects.filter(status=Status.REJECTED).count() == 2


def test_one_approver_for_only_one_property_cannot_approve_a_share(owner, org, repairs):
    a, b, _c = _three(org)
    both = add_member(org, "manager", properties=[a, b])
    made = services.record_split(both, [a, b], rule="equal", category=repairs, description="Guard", amount="1000",
                                 paid_on=PAID, method="CASH")
    accountant = add_member(org, "accountant", properties=[a])
    assert not services.can_approve(accountant, made[0])
    with pytest.raises(PermissionDenied):
        services.approve(accountant, made[0])
