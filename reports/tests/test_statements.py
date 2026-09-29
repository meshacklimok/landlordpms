"""Property owners and the monthly owner statement (D-053)."""

import datetime
from decimal import Decimal

import pytest
from django.core.exceptions import PermissionDenied, ValidationError

from accounts.tests.factories import add_member, make_org, make_property
from audit.models import AuditEvent
from properties import services as property_services
from properties.models import Property, PropertyOwner
from reports import statements
from reports.tests.test_income import bill, make_lease, pay

pytestmark = pytest.mark.django_db

D = datetime.date
JAN, FEB = D(2025, 1, 1), D(2025, 2, 1)


def money(value):
    return Decimal(value).quantize(Decimal("0.01"))


@pytest.fixture
def owner():
    return make_org()


@pytest.fixture
def jane(owner):
    return property_services.create_owner(owner, name="Jane Wanjiru", phone="+254712000111")


@pytest.fixture
def prop(owner, jane):
    prop = make_property(owner.organization, name="Acacia Court")
    return property_services.update_property(owner, prop, owner=jane, management_fee_percent=Decimal("10"))


@pytest.fixture
def january(owner, prop):
    """A1 pays rent and water in full; A2 pays nothing."""
    a1 = make_lease(owner, prop, code="A1", water=1000)
    a2 = make_lease(owner, prop, code="A2", rent=10000)
    bill(a1, JAN)
    bill(a2, JAN)
    pay(owner, a1, 21000, D(2025, 1, 10))
    pay(owner, a1, 21000, D(2025, 2, 3))  # February's money is not January's
    return a1, a2


def test_the_statement(owner, jane, january):
    st = statements.statement(owner, str(jane.public_id), JAN)
    assert st.recipient == "Jane Wanjiru" and st.label == "January 2025"
    (block,) = st.blocks
    assert (block.rentable, block.occupied) == (2, 2)
    a1, a2 = block.lines
    assert (a1.tenant, a1.billed, a1.rent, a1.other, a1.balance) == (
        "Tenant A1", money(21000), money(20000), money(1000), money(0))
    assert (a2.billed, a2.collected, a2.balance) == (money(10000), money(0), money(10000))
    # The fee is on rent only: 10% of 20,000.
    assert (st.collected, st.fee, st.due, st.billed, st.balance) == (
        money(21000), money(2000), money(19000), money(31000), money(10000))
    assert st.expenses == 0 and any("Expenses are approved expenses" in n for n in st.notes)


def test_unapplied_money_counts_as_rent(owner, jane, prop):
    lease = make_lease(owner, prop)
    bill(lease, JAN)
    pay(owner, lease, 25000, D(2025, 1, 6))  # 5,000 ahead
    st = statements.statement(owner, str(jane.public_id), JAN)
    assert (st.rent, st.fee, st.balance) == (money(25000), money(2500), money(-5000))


def test_no_fee_when_none_is_set(owner, jane, prop, january):
    property_services.update_property(owner, prop, management_fee_percent=None)
    st = statements.statement(owner, str(jane.public_id), JAN)
    assert (st.fee, st.due, st.has_fee) == (money(0), money(21000), False)


def test_properties_without_an_owner_get_their_own_statement(owner, jane, january):
    other = make_property(owner.organization, name="Baobab Flats")
    bill(make_lease(owner, other, code="B1", rent=5000), JAN)
    choices = statements.owner_choices(owner)
    assert [key for key, _label in choices] == [str(jane.public_id), statements.NO_OWNER]
    st = statements.statement(owner, statements.NO_OWNER, JAN)
    assert st.owner is None and st.recipient == owner.organization.name
    assert [b.property for b in st.blocks] == [other] and st.billed == money(5000)


def test_scope_and_tenant_names(owner, jane, prop, january):
    other = make_property(owner.organization, name="Baobab Flats")
    property_services.update_property(owner, other, owner=jane)
    make_lease(owner, other, code="B1")
    manager = add_member(owner.organization, "manager", properties=[other])
    st = statements.statement(manager, str(jane.public_id), JAN)
    assert [b.property for b in st.blocks] == [other]
    assert any("Only the properties you can see" in n for n in st.notes)
    viewer = add_member(owner.organization, "viewer", all_properties=True)
    st = statements.statement(viewer, str(jane.public_id), JAN)
    assert not st.show_tenants and st.blocks[0].lines[0].tenant == ""
    caretaker = add_member(owner.organization, "caretaker", all_properties=True)
    with pytest.raises(PermissionDenied):
        statements.statement(caretaker, str(jane.public_id), JAN)
    stranger = make_org()
    assert statements.statement(stranger, str(jane.public_id), JAN) is None


def test_default_month_is_last_month():
    assert statements.default_month(D(2025, 1, 15)) == D(2024, 12, 1)


# ---------------------------------------------------------------------------
# Owners
# ---------------------------------------------------------------------------


def test_owners_are_audited_and_stay_in_their_organization(owner, jane, prop):
    assert AuditEvent.objects.filter(action="property_owner.create").exists()
    property_services.update_owner(owner, jane, name="Jane W. Wanjiru")
    assert AuditEvent.objects.filter(action="property_owner.update").exists()
    with pytest.raises(ValidationError):
        property_services.create_owner(owner, name="  ")
    stranger = make_org()
    with pytest.raises(PermissionDenied):
        property_services.update_owner(stranger, jane, name="Mine now")
    theirs = property_services.create_owner(stranger, name="Someone else")
    with pytest.raises(PermissionDenied):
        property_services.update_property(owner, prop, owner=theirs)
    with pytest.raises(ValidationError):
        property_services.update_property(owner, prop, management_fee_percent=Decimal("120"))
    caretaker = add_member(owner.organization, "caretaker", all_properties=True)
    with pytest.raises(PermissionDenied):
        property_services.create_owner(caretaker, name="Nope")


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------


def test_the_page_and_pdf(client, owner, jane, january):
    client.force_login(owner.user)
    page = client.get(f"/reports/owner-statement/?owner={jane.public_id}&month=2025-01").content.decode()
    assert "Jane Wanjiru" in page and "KES 19,000.00" in page and "Tenant A1" in page
    assert "fee 10% of rent" in page and "Download PDF" in page
    response = client.get(f"/reports/owner-statement.pdf?owner={jane.public_id}&month=2025-01")
    assert response.status_code == 200 and response.content.startswith(b"%PDF")
    assert response["Content-Disposition"] == 'attachment; filename="owner-statement-jane-wanjiru-2025-01.pdf"'


def test_the_page_with_nothing(client):
    empty = make_org()
    client.force_login(empty.user)
    assert "No properties to show yet." in client.get("/reports/owner-statement/").content.decode()
    assert client.get("/reports/owner-statement.pdf").status_code == 404


def test_pages_are_refused_without_the_capabilities(client, owner, jane, prop):
    caretaker = add_member(owner.organization, "caretaker", all_properties=True)
    client.force_login(caretaker.user)
    assert client.get("/reports/owner-statement/").status_code == 403
    assert client.get("/reports/owner-statement.pdf").status_code == 403
    assert client.get("/properties/owners/").status_code == 403


def test_the_owner_pages(client, owner, jane, prop):
    client.force_login(owner.user)
    page = client.get("/properties/owners/").content.decode()
    assert "Jane Wanjiru" in page and "1 property" in page
    response = client.post("/properties/owners/", {"name": "Otieno Holdings Ltd", "email": "ops@otieno.example"})
    assert response.status_code == 302
    added = PropertyOwner.objects.get(name="Otieno Holdings Ltd")
    response = client.post(f"/properties/owners/{added.public_id}/", {"name": "Otieno Holdings"})
    assert response.status_code == 302
    edit = client.get(f"/properties/{prop.public_id}/edit/").content.decode()
    assert "Otieno Holdings" in edit and "Management fee" in edit
    detail = client.get(f"/properties/{prop.public_id}/").content.decode()
    assert "Owner: Jane Wanjiru" in detail and "Fee 10% of rent collected" in detail
    assert Property.objects.get(pk=prop.pk).owner == jane
