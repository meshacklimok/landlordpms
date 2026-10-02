"""Metered water (D-057): readings, flags, approval, the charges and how they are billed."""

import datetime
from decimal import Decimal

import pytest
from django.core.exceptions import PermissionDenied, ValidationError

from accounts.tests.factories import add_member
from billing import invoicing
from billing.models import ChargeType, InvoiceLine
from billing.tests.test_invoicing import bill
from leases.models import Lease
from meters import services
from meters.models import MeterCharge, MeterReading

from .conftest import jpeg, let, make_meter, read

pytestmark = pytest.mark.django_db
D = datetime.date
DEC31, JAN31, FEB = D(2025, 12, 31), D(2026, 1, 31), D(2026, 2, 1)
Status, Flag = MeterReading.Status, MeterReading.Flag


def started(owner, prop, *, value=100, **meter_kw):
    """A let unit on its own meter with an approved baseline on 31 Dec. Returns (lease, meter)."""
    lease, unit = let(owner, prop)
    meter = make_meter(owner, prop, [unit], **meter_kw)
    services.approve(owner, read(owner, meter, DEC31, value), today=DEC31)
    return lease, meter


# ---------------------------------------------------------------------------
# Recording
# ---------------------------------------------------------------------------


def test_the_first_reading_starts_the_count_and_needs_no_photo(owner, prop):
    _lease, unit = let(owner, prop)
    meter = make_meter(owner, prop, [unit])
    reading = read(owner, meter, DEC31, "123.4")
    assert reading.is_baseline and reading.previous is None
    assert not reading.photo
    assert reading.status == Status.SUBMITTED
    assert services.approve(owner, reading) == []


def test_a_photo_is_kept_when_given(owner, prop, media):
    _lease, unit = let(owner, prop)
    meter = make_meter(owner, prop, [unit])
    reading = read(owner, meter, DEC31, 5, photo=jpeg())
    assert reading.photo.name.startswith("meter-readings/") and reading.photo.name.endswith(".jpg")


def test_a_reading_must_come_after_the_last_and_not_in_the_future(owner, prop):
    _lease, meter = started(owner, prop)
    with pytest.raises(ValidationError, match="Date this one after it"):
        read(owner, meter, DEC31, 110)
    with pytest.raises(ValidationError, match="future"):
        read(owner, meter, D(2026, 2, 2), 110, today=FEB)
    with pytest.raises(ValidationError, match="at most 3 decimal"):
        read(owner, meter, JAN31, "110.1234")


def test_a_replaced_meter_needs_a_note_and_starts_a_new_count(owner, prop):
    _lease, meter = started(owner, prop)
    with pytest.raises(ValidationError, match="old meter"):
        read(owner, meter, JAN31, 3, replaced=True)
    reading = read(owner, meter, JAN31, 3, replaced=True, note="Old meter stopped at 140")
    assert reading.is_baseline and reading.flags == []
    assert services.charges_for(reading) == []


def test_recording_needs_the_capability(owner, prop):
    _lease, meter = started(owner, prop)
    viewer = add_member(owner.organization, "viewer", all_properties=True)
    with pytest.raises(PermissionDenied):
        read(viewer, meter, JAN31, 110)


def test_a_round_saves_all_or_nothing(owner, prop):
    _l1, meter1 = started(owner, prop)
    _l2, u2 = let(owner, prop, code="A2")
    meter2 = make_meter(owner, prop, [u2], label="W2")
    entries = [services.RoundEntry(meter1, "110"), services.RoundEntry(meter2, "abc")]
    saved, errors = services.record_round(owner, JAN31, entries)
    assert saved == [] and list(errors) == [meter2.pk]
    assert not MeterReading.objects.filter(meter=meter1, read_on=JAN31).exists()
    saved, errors = services.record_round(owner, JAN31, [services.RoundEntry(meter1, "110"),
                                                          services.RoundEntry(meter2, "7")])
    assert len(saved) == 2 and errors == {}


# ---------------------------------------------------------------------------
# Flags
# ---------------------------------------------------------------------------


def test_a_lower_reading_is_flagged_and_cannot_be_approved(owner, prop):
    _lease, meter = started(owner, prop)
    reading = read(owner, meter, JAN31, 90)
    assert reading.flags == [Flag.LOWER]
    with pytest.raises(ValidationError, match="lower than the last"):
        services.approve(owner, reading, note="looks right")


def test_no_use_while_let_is_flagged_and_needs_a_note(owner, prop):
    _lease, meter = started(owner, prop)
    reading = read(owner, meter, JAN31, 100)
    assert reading.flags == [Flag.ZERO]
    with pytest.raises(ValidationError, match="flagged"):
        services.approve(owner, reading)
    assert services.approve(owner, reading, note="Tenant away all month") == []


def test_much_higher_than_usual_and_a_long_gap_are_flagged(owner, prop):
    _lease, meter = started(owner, prop)
    services.approve(owner, read(owner, meter, JAN31, 110), today=JAN31)   # 10 m³ in 31 days
    reading = read(owner, meter, D(2026, 2, 28), 160)                      # 50 m³ in 28 days
    assert reading.flags == [Flag.HIGH]
    later = read(owner, meter, D(2026, 4, 30), 170)
    assert Flag.LONG_GAP in later.flags


def test_a_reading_waits_for_the_one_before_it(owner, prop):
    _lease, unit = let(owner, prop)
    meter = make_meter(owner, prop, [unit])
    read(owner, meter, DEC31, 100)
    second = read(owner, meter, JAN31, 110)
    with pytest.raises(ValidationError, match="before it"):
        services.approve(owner, second)


def test_approve_many_skips_flagged_readings(owner, prop):
    _lease, meter = started(owner, prop)
    _l2, u2 = let(owner, prop, code="A2")
    meter2 = make_meter(owner, prop, [u2], label="W2")
    services.approve(owner, read(owner, meter2, DEC31, 50), today=DEC31)
    clean, flagged = read(owner, meter, JAN31, 110, ), read(owner, meter2, JAN31, 50)
    done, skipped = services.approve_many(owner, [clean, flagged], today=JAN31)
    assert (done, skipped) == (1, 1)
    clean.refresh_from_db()
    flagged.refresh_from_db()
    assert clean.status == Status.APPROVED and flagged.status == Status.SUBMITTED


# ---------------------------------------------------------------------------
# Charges
# ---------------------------------------------------------------------------


def test_own_meter_charge_is_use_times_rate_billed_the_next_month(owner, prop):
    lease, meter = started(owner, prop, rate="95.50")
    [charge] = services.approve(owner, read(owner, meter, JAN31, "112.5"), today=D(2026, 1, 10))
    assert charge.amount == Decimal("1193.75")
    assert charge.lease == lease and charge.billing_month == FEB
    assert (charge.service_start, charge.service_end) == (D(2026, 1, 1), JAN31)
    assert "100.000 → 112.500 m³ = 12.500 m³ × 95.50" in charge.description


def test_a_shared_meter_splits_by_weight_and_skips_vacant_days(owner, prop):
    l1, u1 = let(owner, prop, code="A1")
    l2, u2 = let(owner, prop, code="A2", start=D(2026, 1, 16))
    meter = make_meter(owner, prop, [(u1, "2"), (u2, "1")], rate="90", split="WEIGHTED")
    services.approve(owner, read(owner, meter, DEC31, 0), today=DEC31)
    charges = services.approve(owner, read(owner, meter, D(2026, 1, 30), 30), today=D(2026, 1, 10))
    by_lease = {c.lease_id: c for c in charges}
    assert by_lease[l1.pk].amount == Decimal("1800.00")          # 2/3 of 2,700
    assert by_lease[l2.pk].amount == Decimal("450.00")           # 1/3 of 2,700 for 15 of 30 days
    assert "unit A2 share 1 of 3" in by_lease[l2.pk].description
    assert "15 of 30 days" in by_lease[l2.pk].description


def test_the_minimum_charge_applies_per_unit_share(owner, prop):
    _l1, u1 = let(owner, prop, code="A1")
    _l2, u2 = let(owner, prop, code="A2")
    meter = make_meter(owner, prop, [u1, u2], rate="10", minimum="500")
    services.approve(owner, read(owner, meter, DEC31, 0), today=DEC31)
    charges = services.approve(owner, read(owner, meter, JAN31, 30), today=D(2026, 1, 10))
    assert [c.amount for c in charges] == [Decimal("500.00"), Decimal("500.00")]
    assert all("minimum charge" in c.description for c in charges)


def test_a_prepaid_meter_is_never_billed(owner, prop):
    _lease, meter = started(owner, prop, kind="PREPAID", rate="0")
    assert services.approve(owner, read(owner, meter, JAN31, 150), today=JAN31) == []


def test_the_rate_is_fixed_on_approval(owner, prop):
    _lease, meter = started(owner, prop, rate="100")
    reading = read(owner, meter, JAN31, 110)
    services.approve(owner, reading, today=D(2026, 1, 10))
    services.update_meter(owner, meter, units=[(u.unit, u.weight) for u in meter.served.all()], label="W1",
                          serial="", kind="POSTPAID", split="EQUAL", rate="200", minimum_charge="")
    reading.refresh_from_db()
    assert reading.rate == Decimal("100.00")
    assert reading.charges.get().amount == Decimal("1000.00")


def test_a_lease_ending_before_the_next_month_is_billed_in_its_last_month(owner, prop):
    lease, meter = started(owner, prop)
    Lease.all_objects.filter(pk=lease.pk).update(end_date=D(2026, 1, 20))
    [charge] = services.approve(owner, read(owner, meter, JAN31, 110), today=D(2026, 1, 10))
    assert charge.billing_month == D(2026, 1, 1)
    assert charge.service_end == D(2026, 1, 20)
    assert charge.amount == Decimal("645.16")                    # 1,000 for 20 of 31 days


# ---------------------------------------------------------------------------
# Billing
# ---------------------------------------------------------------------------


def test_the_charge_goes_out_with_next_months_rent(owner, prop):
    lease, meter = started(owner, prop)
    [charge] = services.approve(owner, read(owner, meter, JAN31, 110), today=JAN31)
    assert not charge.lines.exists()                             # February not billed yet: it waits
    invoice = bill(lease, FEB)
    water = invoice.lines.get(meter_charge=charge)
    assert water.amount == Decimal("1000.00")
    assert water.charge_type.category == ChargeType.Category.METERED_WATER
    assert invoice.lines.count() == 2
    assert bill(lease, FEB) is None or invoice.lines.count() == 2  # idempotent


def test_a_charge_for_a_month_already_billed_gets_its_own_invoice(owner, prop):
    lease, meter = started(owner, prop)
    rent = bill(lease, FEB)
    [charge] = services.approve(owner, read(owner, meter, JAN31, 110), today=D(2026, 2, 2))
    line = InvoiceLine.objects.get(meter_charge=charge)
    assert line.invoice != rent
    assert line.invoice.lines.count() == 1


def test_two_readings_in_one_month_are_both_billed(owner, prop):
    lease, meter = started(owner, prop)
    services.approve(owner, read(owner, meter, D(2026, 1, 15), 105), today=D(2026, 1, 10))
    services.approve(owner, read(owner, meter, JAN31, 110), today=D(2026, 1, 10))
    invoice = bill(lease, FEB)
    assert sorted(line.amount for line in invoice.lines.filter(meter_charge__isnull=False)) == [
        Decimal("500.00"), Decimal("500.00")]


def test_a_charge_is_billed_once(owner, prop):
    lease, meter = started(owner, prop)
    [charge] = services.approve(owner, read(owner, meter, JAN31, 110), today=D(2026, 2, 2))
    invoicing.bill_meter_charges(owner.organization, today=D(2026, 2, 3))
    bill(lease, FEB)
    assert InvoiceLine.objects.filter(meter_charge=charge, is_void=False).count() == 1


# ---------------------------------------------------------------------------
# Reject and undo
# ---------------------------------------------------------------------------


def test_rejecting_needs_a_reason(owner, prop):
    _lease, meter = started(owner, prop)
    reading = read(owner, meter, JAN31, 90)
    with pytest.raises(ValidationError, match="why"):
        services.reject(owner, reading, reason=" ")
    services.reject(owner, reading, reason="Misread")
    reading.refresh_from_db()
    assert reading.status == Status.REJECTED
    # The next reading is measured from the last live one, not the rejected one.
    again = read(owner, meter, D(2026, 2, 1), 110)
    assert again.previous.value == Decimal("100.000")


def test_undo_cancels_unbilled_charges_and_is_blocked_once_invoiced(owner, prop):
    lease, meter = started(owner, prop)
    reading = read(owner, meter, JAN31, 110)
    [charge] = services.approve(owner, reading, today=JAN31)
    reading.refresh_from_db()
    assert services.can_undo(owner, reading)
    services.undo_approval(owner, reading)
    charge.refresh_from_db()
    assert charge.cancelled_at is not None
    reading.refresh_from_db()
    assert reading.status == Status.SUBMITTED and reading.rate is None
    invoice = bill(lease, FEB)
    assert not invoice.lines.filter(meter_charge__isnull=False).exists()

    services.approve(owner, reading, today=D(2026, 2, 2))
    reading.refresh_from_db()
    assert not services.can_undo(owner, reading)
    with pytest.raises(ValidationError, match="Void the invoice"):
        services.undo_approval(owner, reading)
    assert MeterCharge.objects.filter(reading=reading, cancelled_at__isnull=True).count() == 1


def test_a_meter_with_waiting_readings_cannot_be_archived(owner, prop):
    _lease, meter = started(owner, prop)
    read(owner, meter, JAN31, 110)
    with pytest.raises(ValidationError, match="waiting readings"):
        services.archive_meter(owner, meter)


def test_a_meter_serves_only_units_of_its_property(owner, prop):
    from accounts.tests.factories import make_property
    other = make_property(owner.organization)
    _lease, stranger = let(owner, other)
    with pytest.raises(ValidationError):
        make_meter(owner, prop, [stranger])


def test_a_lease_with_unbilled_water_is_not_settled(owner, prop):
    from billing import jobs
    lease, meter = started(owner, prop)
    services.approve(owner, read(owner, meter, JAN31, 110), today=JAN31)
    assert jobs.is_settled(lease) is False
