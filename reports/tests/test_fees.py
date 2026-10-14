"""Fixed monthly fees and the agency fee income report (D-127)."""

import datetime
from decimal import Decimal

import pytest
from django.urls import reverse
from django.utils import timezone

from accounts import branches, services
from accounts.tests.factories import add_member, fresh, make_org, make_property
from accounts.tests.test_isolation import login
from properties import services as property_services
from properties.models import Property
from reports import fees, statements
from reports.tests.test_income import bill, make_lease, pay

pytestmark = pytest.mark.django_db

D = datetime.date
JAN, FEB, MAR = D(2025, 1, 1), D(2025, 2, 1), D(2025, 3, 1)
TODAY = D(2025, 4, 15)  # the report runs to March 2025


def money(value):
    return Decimal(value).quantize(Decimal("0.01"))


def _since(prop, day):
    Property.all_objects.filter(pk=prop.pk).update(
        created_at=timezone.make_aware(datetime.datetime.combine(day, datetime.time(9))))
    prop.refresh_from_db()
    return prop


@pytest.fixture
def owner():
    return make_org()


@pytest.fixture
def jane(owner):
    return property_services.create_owner(owner, name="Jane Wanjiru", phone="+254712000111")


@pytest.fixture
def prop(owner, jane):
    prop = _since(make_property(owner.organization, name="Acacia Court"), D(2024, 12, 5))
    return property_services.update_property(owner, prop, owner=jane, management_fee_percent=Decimal("10"),
                                             management_fee_fixed=Decimal("2000"))


def test_fixed_fee_is_added_to_the_percentage(owner, jane, prop):
    lease = make_lease(owner, prop)
    bill(lease, JAN)
    pay(owner, lease, 20000, D(2025, 1, 6))
    st = statements.statement(owner, str(jane.public_id), JAN)
    (block,) = st.blocks
    assert (block.fixed_fee, st.fee, st.due) == (money(2000), money(4000), money(16000))
    assert block.fee_label == "10% of rent + KES 2,000.00 a month"
    assert any("fixed monthly fee" in n for n in st.notes)


def test_fixed_fee_is_charged_with_nothing_collected(owner, jane, prop):
    make_lease(owner, prop)
    st = statements.statement(owner, str(jane.public_id), FEB)
    assert (st.collected, st.fee, st.due) == (money(0), money(2000), money(-2000))


def test_fixed_fee_only_while_managed(owner, prop):
    assert statements.fixed_fee(prop, D(2024, 11, 1)) == 0  # before it was added
    assert statements.fixed_fee(prop, D(2024, 12, 1)) == 2000  # the month it was added
    Property.all_objects.filter(pk=prop.pk).update(
        archived_at=timezone.make_aware(datetime.datetime(2025, 2, 10, 9)))
    prop.refresh_from_db()
    assert statements.fixed_fee(prop, FEB) == 2000  # the month it was archived
    assert statements.fixed_fee(prop, MAR) == 0


def test_fixed_fee_alone(owner, jane, prop):
    property_services.update_property(owner, prop, management_fee_percent=None)
    make_lease(owner, prop)
    st = statements.statement(owner, str(jane.public_id), JAN)
    assert st.has_fee and st.blocks[0].fee_label == "KES 2,000.00 a month"


@pytest.fixture
def agency(owner, jane, prop):
    """Two branches: Acacia (Westlands, 10% + 2,000) and Baobab (Karen, 8%, another owner)."""
    westlands = branches.create_branch(owner, name="Westlands")
    karen = branches.create_branch(owner, name="Karen")
    property_services.update_property(owner, prop, branch=westlands)
    tom = property_services.create_owner(owner, name="Tom Otieno")
    baobab = _since(make_property(owner.organization, name="Baobab Flats"), D(2024, 6, 1))
    property_services.update_property(owner, baobab, owner=tom, branch=karen, management_fee_percent=Decimal("8"))
    a = make_lease(owner, prop, code="A1")
    b = make_lease(owner, baobab, code="B1", rent=50000)
    for month in (JAN, FEB):
        bill(a, month)
        bill(b, month)
    pay(owner, a, 20000, D(2025, 1, 5))
    pay(owner, b, 50000, D(2025, 2, 5))
    return westlands, karen, baobab


def test_fee_report(owner, agency):
    rep = fees.report(owner, TODAY)
    assert rep.months[0] == D(2024, 4, 1) and rep.months[-1] == MAR
    # Acacia: 2,000 fixed for Dec, Jan, Feb, Mar plus 10% of 20,000 in January. Baobab: 8% of 50,000.
    acacia = next(r for r in rep.by_property if r.label == "Acacia Court")
    baobab = next(r for r in rep.by_property if r.label == "Baobab Flats")
    assert acacia.fee_total == money(10000) and acacia.rent_total == money(20000)
    assert baobab.fee_total == money(4000)
    assert rep.total.fee_total == money(14000)
    assert [r.label for r in rep.by_branch] == ["Westlands", "Karen"]
    assert [r.label for r in rep.by_owner] == ["Jane Wanjiru", "Tom Otieno"]
    assert rep.by_branch[0].share == Decimal("71.4")
    assert dict((m, f) for m, _r, f in rep.month_rows)[JAN] == money(4000)


def test_fee_report_matches_the_statements(owner, jane, agency):
    rep = fees.report(owner, TODAY)
    jane_row = next(r for r in rep.by_owner if r.label == "Jane Wanjiru")
    for i, month in enumerate(rep.months):
        st = statements.statement(owner, str(jane.public_id), month)
        assert jane_row.fees[i] == (st.fee if st else 0), month


def test_fee_report_respects_branch_scope(owner, agency):
    westlands, karen, baobab = agency
    m = add_member(owner.organization, "manager")
    services.set_property_scope(owner, m, all_properties=False, branches=[karen])
    rep = fees.report(fresh(m), TODAY)
    assert [r.label for r in rep.by_property] == ["Baobab Flats"] and rep.scoped


def test_fee_page_and_csv(client, owner, agency):
    login(client, owner)
    page = client.get(reverse("reports:fees"))
    assert page.status_code == 200
    assert "Westlands" in page.content.decode()
    response = client.get(reverse("reports:fees"), {"format": "csv"})
    assert response.status_code == 200 and "Acacia Court,Westlands,Jane Wanjiru" in response.content.decode("utf-8-sig")


def test_fee_page_needs_financial_reports(client, owner):
    caretaker = add_member(owner.organization, "caretaker", all_properties=True)
    login(client, caretaker)
    assert client.get(reverse("reports:fees")).status_code == 403


def test_property_form_saves_fixed_fee_and_branch(client, owner, prop):
    westlands = branches.create_branch(owner, name="Westlands")
    login(client, owner)
    data = {"name": prop.name, "category": prop.category, "county": prop.county or "", "sub_county": "",
            "area": "", "street": "", "owner": prop.owner.pk, "management_fee_percent": "10",
            "management_fee_fixed": "3500", "branch": westlands.pk}
    response = client.post(reverse("properties:edit", args=[prop.public_id]), data)
    assert response.status_code == 302, response.content.decode()[:2000]
    prop.refresh_from_db()
    assert (prop.management_fee_fixed, prop.branch) == (money(3500), westlands)
