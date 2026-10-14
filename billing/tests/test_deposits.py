"""The deposit sub-ledger (doc 14 A1, D-016)."""

import datetime
from decimal import Decimal

import pytest
from django.core.exceptions import PermissionDenied, ValidationError

from accounts.tests.factories import add_member, make_org, make_property
from audit.models import AuditEvent
from billing import deposits, invoicing
from billing.models import DepositEntry, LedgerEntry
from leases import services as lease_services
from properties import services as property_services

from .test_invoicing import FEB, JAN, MAR, bill, make_lease

pytestmark = pytest.mark.django_db

D = datetime.date
RENT, WATER = DepositEntry.DepositType.RENT, DepositEntry.DepositType.WATER


@pytest.fixture
def owner():
    return make_org()


@pytest.fixture
def lease(owner):
    return make_lease(owner, make_property(owner.organization))


def test_deposits_are_held_per_type_and_never_touch_the_rent_balance(owner, lease):
    deposits.record_received(owner, lease, amount="30,000", entry_date=JAN, reference="QK12AB")
    deposits.record_received(owner, lease, amount=2000, deposit_type=WATER, entry_date=JAN)
    bill(lease, FEB)
    assert deposits.held(lease) == Decimal("32000.00")
    assert deposits.held_by_type(lease) == {RENT: Decimal("30000.00"), WATER: Decimal("2000.00")}
    # No fake zero balance: rent due and deposit held are shown apart.
    assert invoicing.lease_balance(lease) == Decimal("15000.00")
    assert AuditEvent.objects.filter(action="deposit.received").count() == 2


def test_a_deduction_needs_a_reason_the_capability_and_money_held(owner, lease):
    deposits.record_received(owner, lease, amount=30000, entry_date=JAN)
    with pytest.raises(ValidationError):
        deposits.deduct(owner, lease, amount=500, reason="  ")
    with pytest.raises(ValidationError):
        deposits.deduct(owner, lease, amount=30001, reason="Too much")
    with pytest.raises(ValidationError):
        deposits.deduct(owner, lease, amount=100, deposit_type=WATER, reason="None held")
    accountant = add_member(owner.organization, "accountant", all_properties=True)
    deposits.record_received(accountant, lease, amount=1000, entry_date=JAN)  # may record ...
    with pytest.raises(PermissionDenied):
        deposits.deduct(accountant, lease, amount=500, reason="Broken window")  # ... but not deduct
    entry = deposits.deduct(owner, lease, amount=4500, reason="Broken window")
    assert entry.amount == Decimal("-4500.00") and entry.ledger_entry is None
    assert deposits.held(lease) == Decimal("26500.00")


def test_a_deduction_applied_to_the_balance_credits_the_rent_ledger(owner, lease):
    deposits.record_received(owner, lease, amount=30000, entry_date=JAN)
    bill(lease, FEB)
    entry = deposits.deduct(owner, lease, amount=15000, reason="Last month's rent", apply_to_balance=True)
    assert entry.ledger_entry.kind == LedgerEntry.Kind.DEPOSIT_APPLIED
    assert entry.ledger_entry.amount == Decimal("-15000.00")
    assert invoicing.lease_balance(lease) == 0 and deposits.held(lease) == Decimal("15000.00")
    # Correcting it puts both ledgers back.
    deposits.reverse(owner, entry, reason="Tenant paid in cash")
    assert invoicing.lease_balance(lease) == Decimal("15000.00") and deposits.held(lease) == Decimal("30000.00")
    with pytest.raises(ValidationError):
        deposits.reverse(owner, entry, reason="again")


def test_refund_cannot_exceed_what_is_held(owner, lease):
    deposits.record_received(owner, lease, amount=10000, entry_date=JAN)
    with pytest.raises(ValidationError):
        deposits.refund(owner, lease, amount="10000.01")
    deposits.refund(owner, lease, amount=10000, reference="MPESA-REF")
    assert deposits.held(lease) == 0
    received = DepositEntry.objects.get(lease=lease, kind=DepositEntry.Kind.RECEIVED)
    with pytest.raises(ValidationError):  # reversing the receipt would leave a negative deposit
        deposits.reverse(owner, received, reason="Typo")


def test_deposit_moves_with_the_tenant_to_a_new_unit(owner, lease):
    deposits.record_received(owner, lease, amount=30000, entry_date=JAN)
    deposits.record_received(owner, lease, amount=2000, deposit_type=WATER, entry_date=JAN)
    other = property_services.create_unit(owner, lease.unit.property, code="B9")
    draft = lease_services.transfer_lease(owner, lease, unit=other, start_date=MAR, rent=18000)
    new = lease_services.activate_lease(owner, draft)
    assert deposits.held(lease) == 0
    assert deposits.held_by_type(new) == {RENT: Decimal("30000.00"), WATER: Decimal("2000.00")}
    out = DepositEntry.objects.get(lease=lease, kind=DepositEntry.Kind.TRANSFERRED, deposit_type=RENT)
    into = DepositEntry.objects.get(lease=new, kind=DepositEntry.Kind.TRANSFERRED, deposit_type=RENT)
    assert out.counterpart == into and into.counterpart == out and into.entry_date == MAR
    assert new.number in out.reason
    with pytest.raises(ValidationError):
        deposits.reverse(owner, into, reason="no")
    statement = deposits.clearance_statement(lease)
    assert statement["held"] == 0
    assert [(t["code"], t["received"], t["transferred"]) for t in statement["types"]] == [
        (RENT, Decimal("30000.00"), Decimal("-30000.00")), (WATER, Decimal("2000.00"), Decimal("-2000.00"))]


def test_deposits_respect_organization_scope_and_drafts(owner, lease):
    with pytest.raises(PermissionDenied):
        deposits.record_received(make_org(), lease, amount=1)
    scoped = add_member(owner.organization, "manager", properties=[make_property(owner.organization)])
    with pytest.raises(PermissionDenied):
        deposits.record_received(scoped, lease, amount=1)
    with pytest.raises(ValidationError):
        deposits.record_received(owner, lease, amount=1, entry_date=D.today() + datetime.timedelta(days=1))
    with pytest.raises(ValidationError):
        deposits.record_received(owner, lease, amount=0)


def test_deposit_rows_are_append_only(owner, lease):
    entry = deposits.record_received(owner, lease, amount=100)
    entry.amount = Decimal("1")
    with pytest.raises(ValueError):
        entry.save()
    with pytest.raises(ValueError):
        entry.delete()


def test_billers_that_are_not_landlords_hold_no_deposits_and_have_no_move_out(client, owner, lease):
    from django.urls import reverse

    from accounts.tests.test_isolation import login
    from leases import paperwork

    login(client, owner)
    account = reverse("billing:lease_account", args=[lease.public_id])
    assert b"Deposit clearance" in client.get(account).content

    org = owner.organization
    org.org_type = org.Type.ESTATE
    org.save(update_fields=["org_type"])
    lease.refresh_from_db()
    with pytest.raises(ValidationError):
        deposits.record_received(owner, lease, amount=1000)
    page = client.get(account).content
    assert b"Deposit clearance" not in page and b"Deposit held" not in page
    assert paperwork.move_out_applies(lease) is False
    assert client.get(reverse("leases:move_out", args=[lease.public_id])).status_code == 404


def test_a_biller_can_choose_to_hold_deposits(client, owner, lease):
    from django.urls import reverse

    from accounts import services as account_services
    from accounts.tests.test_isolation import login
    from leases import paperwork

    login(client, owner)
    org = owner.organization
    org.org_type = org.Type.WATER
    org.save(update_fields=["org_type"])
    settings_url = reverse("accounts:organization")
    assert b"We hold deposits" in client.get(settings_url).content

    account_services.update_organization(owner, holds_deposits=True)
    org.refresh_from_db()
    lease.refresh_from_db()
    deposits.record_received(owner, lease, amount=1000)
    account = reverse("billing:lease_account", args=[lease.public_id])
    assert b"Deposit clearance" in client.get(account).content
    # Choosing deposits does not bring back the tenancy move-out statement.
    assert paperwork.move_out_applies(lease) is False


def test_landlords_are_not_asked_about_deposits(client, owner):
    from django.urls import reverse

    from accounts.tests.test_isolation import login

    login(client, owner)
    assert b"We hold deposits" not in client.get(reverse("accounts:organization")).content


# ---------------------------------------------------------------------------
# The owner's deposit rules (D-150)
# ---------------------------------------------------------------------------


def _png():
    import io

    from django.core.files.uploadedfile import SimpleUploadedFile
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (20, 20), "white").save(buf, "PNG")
    return SimpleUploadedFile("quote.png", buf.getvalue(), content_type="image/png")


def _rules(owner, **values):
    org = owner.organization
    for field, value in values.items():
        setattr(org, field, value)
    org.save()


def test_the_owner_can_limit_the_headings_a_deduction_may_use(owner, lease):
    deposits.record_received(owner, lease, amount=30000, entry_date=JAN)
    _rules(owner, deposit_deduction_headings=["DAMAGE", "CLEANING"])
    lease.organization.refresh_from_db()
    with pytest.raises(ValidationError) as exc:
        deposits.deduct(owner, lease, amount=500, reason="Painting")
    assert "heading" in exc.value.message_dict
    with pytest.raises(ValidationError):
        deposits.deduct(owner, lease, amount=500, reason="Painting", heading="KEYS")
    entry = deposits.deduct(owner, lease, amount=500, reason="Cracked tile", heading="DAMAGE")
    assert entry.heading == "DAMAGE"


def test_without_a_limit_the_heading_is_optional(owner, lease):
    deposits.record_received(owner, lease, amount=30000, entry_date=JAN)
    assert deposits.deduct(owner, lease, amount=500, reason="Anything").heading == ""


def test_the_owner_can_require_proof_for_a_deduction(owner, lease):
    deposits.record_received(owner, lease, amount=30000, entry_date=JAN)
    _rules(owner, deposit_proof_required=True)
    lease.organization.refresh_from_db()
    with pytest.raises(ValidationError) as exc:
        deposits.deduct(owner, lease, amount=500, reason="Window")
    assert "evidence" in exc.value.message_dict
    entry = deposits.deduct(owner, lease, amount=500, reason="Window", evidence=_png())
    assert entry.evidence and AuditEvent.objects.filter(action="deposit.deduct").count() == 1
    with pytest.raises(ValidationError):
        deposits.deduct(owner, lease, amount=500, reason="Window", evidence=_not_a_picture())


def _not_a_picture():
    from django.core.files.uploadedfile import SimpleUploadedFile

    return SimpleUploadedFile("x.png", b"not a picture", content_type="image/png")


def test_the_owner_can_forbid_putting_a_deduction_towards_rent_owed(owner, lease):
    deposits.record_received(owner, lease, amount=30000, entry_date=JAN)
    bill(lease, FEB)
    _rules(owner, deposit_allow_offset=False)
    lease.organization.refresh_from_db()
    with pytest.raises(ValidationError):
        deposits.deduct(owner, lease, amount=1000, reason="Rent", apply_to_balance=True)
    assert deposits.deduct(owner, lease, amount=1000, reason="Damage").ledger_entry is None


def test_a_deposit_falls_due_the_owners_days_after_the_lease_ends(owner, lease):
    assert deposits.settle_by(lease) is None  # still running
    lease.status, lease.ended_on = lease.Status.ENDED, D(2026, 3, 31)
    assert deposits.settle_by(lease) == D(2026, 4, 30)  # the default 30 days
    lease.organization.deposit_refund_days = 14
    assert deposits.settle_by(lease) == D(2026, 4, 14)


def test_the_owner_sets_the_rules_on_the_settings_page(client, owner):
    from django.urls import reverse

    from accounts.tests.test_isolation import login

    login(client, owner)
    page = client.get(reverse("accounts:organization"))
    assert page.status_code == 200 and "Settle a deposit within" in page.content.decode()
    data = {"name": "Acme", "org_type": owner.organization.org_type, "invoice_lead_days": "5",
            "late_fee_mode": "OFF", "deposit_refund_days": "21", "deposit_deduction_headings": ["DAMAGE", "KEYS"],
            "deposit_proof_required": "on"}
    response = client.post(reverse("accounts:organization"), data)
    assert response.status_code == 302
    owner.organization.refresh_from_db()
    assert owner.organization.deposit_refund_days == 21
    assert owner.organization.deposit_deduction_headings == ["DAMAGE", "KEYS"]
    assert owner.organization.deposit_proof_required and not owner.organization.deposit_allow_offset
