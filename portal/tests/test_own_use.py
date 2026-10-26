"""The portal's Water and electricity page and the tenant's private own-meter notes (D-219)."""

import datetime
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse

from accounts.tests.factories import make_org, make_property
from billing.models import InvoiceLine
from meters import services as meter_services
from meters.models import MeterCharge
from meters.tests.conftest import make_meter, read
from portal import own_use
from portal.models import OwnUse
from portal.tests.test_portal import join
from reports.tests.test_income import make_lease

pytestmark = pytest.mark.django_db
D = datetime.date
TODAY = D(2026, 6, 15)
ELEC, WATER = OwnUse.Utility.ELECTRICITY, OwnUse.Utility.WATER
READING, TOPUP = OwnUse.Kind.READING, OwnUse.Kind.TOPUP


@pytest.fixture
def owner():
    return make_org()


@pytest.fixture
def a1(owner):
    return make_lease(owner, make_property(owner.organization, name="Acacia Court"), code="A1")


@pytest.fixture
def b7(owner):
    return make_lease(owner, make_property(owner.organization, name="Birch Court"), code="B7")


def add(user, lease, utility, kind, on, **kw):
    return own_use.add_entry(user, lease, utility=utility, kind=kind, on=on, **kw)


def test_units_come_from_the_differences_between_readings(owner, a1):
    add(owner.user, a1, ELEC, READING, D(2026, 4, 30), value="1000", amount="")
    add(owner.user, a1, ELEC, READING, D(2026, 5, 31), value="1120", amount="")
    add(owner.user, a1, ELEC, READING, D(2026, 6, 10), value="1150.5", amount="3000")
    summary = own_use.summarize(a1, ELEC, TODAY)
    may, june = summary.months[-2], summary.months[-1]
    assert (may.units, june.units) == (Decimal("120"), Decimal("30.5"))
    assert june.cost == Decimal("3000")
    assert summary.change_pct == -75
    assert summary.price_per_unit == (Decimal("3000") / Decimal("150.5")).quantize(Decimal("0.01"))


def test_a_token_buyer_gets_units_and_money_from_each_purchase(owner, a1):
    add(owner.user, a1, ELEC, TOPUP, D(2026, 6, 1), value="20", amount="600")
    add(owner.user, a1, ELEC, TOPUP, D(2026, 6, 11), value="20", amount="620")
    june = own_use.summarize(a1, ELEC, TODAY).months[-1]
    assert (june.units, june.cost) == (Decimal("40"), Decimal("1220"))


def test_days_left_uses_the_usual_daily_use(owner, a1):
    add(owner.user, a1, ELEC, TOPUP, D(2026, 6, 1), value="20", amount="600")
    add(owner.user, a1, ELEC, TOPUP, D(2026, 6, 11), value="20", amount="600")
    # 20 kWh over 10 days is 2 a day; the last 20 kWh lasts 10 days from 11 June, so 6 more from 15 June.
    assert own_use.summarize(a1, ELEC, TODAY).days_left == 6
    assert own_use.summarize(a1, WATER, TODAY).days_left is None


def test_each_utility_is_kept_apart(owner, a1):
    add(owner.user, a1, ELEC, TOPUP, D(2026, 6, 1), value="20", amount="600")
    assert not own_use.summarize(a1, WATER, TODAY).has_data


@pytest.mark.parametrize("kw", [
    dict(kind=READING, on=D(2026, 6, 1), value=""),
    dict(kind=TOPUP, on=D(2026, 6, 1), value="", amount=""),
    dict(kind=READING, on=D(2026, 6, 1), value="abc"),
    dict(kind=READING, on=D(2026, 6, 1), value="-5"),
    dict(kind=TOPUP, on=D(2026, 6, 1), amount="NaN"),
    dict(kind=TOPUP, on=D(2999, 1, 1), amount="100"),
    dict(kind="OTHER", on=D(2026, 6, 1), amount="100"),
])
def test_bad_entries_are_refused(owner, a1, kw):
    with pytest.raises(ValidationError):
        own_use.add_entry(owner.user, a1, utility=ELEC, **kw)
    assert not OwnUse.objects.exists()


def test_the_page_shows_the_billed_water_and_the_private_notes(client, owner, a1):
    user = join(owner, a1)
    prop, unit = a1.unit.property, a1.unit
    meter = make_meter(owner, prop, [unit])
    meter_services.approve(owner, read(owner, meter, D(2025, 12, 31), 100), today=D(2025, 12, 31))
    meter_services.approve(owner, read(owner, meter, D(2026, 1, 31), 112), today=D(2026, 1, 31))
    assert MeterCharge.objects.filter(lease=a1).exists()
    client.force_login(user)
    url = reverse("portal:utilities", args=[a1.public_id])
    response = client.post(url, {"utility": "ELECTRICITY", "kind": "TOPUP", "on": "2026-01-20", "value": "25",
                                 "amount": "750", "note": "KPLC"})
    assert response.status_code == 302
    page = client.get(url)
    body = page.content.decode()
    assert page.status_code == 200
    assert "Billed by the office" in body and "My own meters" in body and "KPLC" in body
    assert "Query" not in body
    assert OwnUse.objects.get().created_by == user


def test_the_notes_are_private_to_the_tenant(client, owner, a1, b7):
    user_a, user_b = join(owner, a1), join(owner, b7)
    entry = add(user_a, a1, ELEC, TOPUP, D(2026, 6, 1), value="20", amount="600")
    client.force_login(user_b)
    assert client.get(reverse("portal:utilities", args=[a1.public_id])).status_code == 404
    assert client.post(reverse("portal:own_use_delete", args=[entry.public_id])).status_code == 404
    assert OwnUse.objects.filter(pk=entry.pk).exists()
    client.force_login(owner.user)
    assert client.get(reverse("portal:utilities", args=[a1.public_id])).status_code == 404


def test_the_notes_never_reach_an_invoice(owner, a1):
    add(owner.user, a1, ELEC, TOPUP, D(2026, 6, 1), value="20", amount="600")
    assert not InvoiceLine.objects.filter(invoice__lease=a1).filter(description__icontains="electric").exists()


def test_a_tenant_removes_their_own_entry(client, owner, a1):
    user = join(owner, a1)
    entry = add(user, a1, WATER, TOPUP, D(2026, 6, 1), amount="500")
    client.force_login(user)
    assert client.post(reverse("portal:own_use_delete", args=[entry.public_id])).status_code == 302
    assert not OwnUse.objects.exists()
