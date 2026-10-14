"""Joint ownership: owners' shares of a property and each owner's statement (D-078)."""

import datetime
from decimal import Decimal

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.urls import reverse

from accounts.tests.factories import add_member, make_org, make_property
from accounts.tests.test_isolation import login
from audit.models import AuditEvent
from expenses import services as expense_services
from properties import services as property_services
from properties.models import Property, PropertyShare, format_percent
from reports import owners, statements
from reports.pdf import render_statement
from reports.tests.test_income import bill, make_lease, pay

pytestmark = pytest.mark.django_db

D = datetime.date
JAN = D(2025, 1, 1)
P = Decimal


def money(value):
    return Decimal(value).quantize(Decimal("0.01"))


@pytest.fixture(autouse=True)
def media(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path


@pytest.fixture
def owner():
    return make_org()


@pytest.fixture
def org(owner):
    return owner.organization


@pytest.fixture
def jane(owner):
    return property_services.create_owner(owner, name="Jane Wanjiru", email="jane@example.com")


@pytest.fixture
def peter(owner):
    return property_services.create_owner(owner, name="Peter Kamau", phone="+254712000222")


@pytest.fixture
def prop(owner, org):
    prop = make_property(org, name="Acacia Court")
    return property_services.update_property(owner, prop, management_fee_percent=P("10"))


@pytest.fixture
def joint(owner, prop, jane, peter):
    """Jane 60%, Peter 40%."""
    return property_services.set_shares(owner, prop, [(peter, P("40")), (jane, P("60"))])


@pytest.fixture
def january(owner, org, joint):
    """20,000 rent collected, a 10% fee and a 2,000 repair: 16,000 due on the property."""
    lease = make_lease(owner, joint, code="A1")
    bill(lease, JAN)
    pay(owner, lease, 20000, D(2025, 1, 10))
    repairs = expense_services.categories(org).get(name="Repairs and maintenance")
    expense_services.record_expense(owner, joint, category=repairs, amount="2000", paid_on=D(2025, 1, 15),
                                    reference="", description="Gate lock", method="MPESA")
    return lease


def st(member, who, month=JAN):
    return statements.statement(member, str(who.public_id), month)


# ---------------------------------------------------------------------------
# Setting shares
# ---------------------------------------------------------------------------


def test_the_largest_share_is_the_main_owner(owner, joint, jane, peter):
    assert joint.owner == jane
    assert property_services.shares_of(joint) == [(jane, P("60.00")), (peter, P("40.00"))]
    event = AuditEvent.objects.get(action="property.shares", object_id=str(joint.public_id))
    assert event.changes["owners"] == [{}, {"Jane Wanjiru": "60", "Peter Kamau": "40"}]


@pytest.mark.parametrize("rows, message", [
    ([("jane", "60"), ("peter", "30")], "add up to 90%"),
    ([("jane", "50"), ("jane", "50")], "only once"),
    ([("jane", "80")], "sole owner holds 100%"),
    ([("jane", "0"), ("peter", "100")], "more than 0"),
])
def test_bad_shares_are_refused(owner, prop, jane, peter, rows, message):
    people = {"jane": jane, "peter": peter}
    with pytest.raises(ValidationError) as exc:
        property_services.set_shares(owner, prop, [(people[k], P(v)) for k, v in rows])
    assert message in " ".join(exc.value.messages)
    assert not PropertyShare.objects.exists()


def test_another_organizations_owner_or_a_scoped_member_is_refused(owner, org, prop, jane):
    stranger = property_services.create_owner(make_org(), name="Someone Else")
    with pytest.raises(PermissionDenied):
        property_services.set_shares(owner, prop, [(jane, P("50")), (stranger, P("50"))])
    elsewhere = add_member(org, "manager", properties=[make_property(org, name="Baobab Flats")])
    with pytest.raises(PermissionDenied):
        property_services.set_shares(elsewhere, prop, [(jane, P("100"))])


def test_back_to_one_owner_and_to_none(owner, joint, jane, peter):
    property_services.set_shares(owner, joint, [(peter, P("100"))])
    joint.refresh_from_db()
    assert joint.owner == peter and not joint.shares.exists()
    property_services.set_shares(owner, joint, [])
    joint.refresh_from_db()
    assert joint.owner is None and property_services.shares_of(joint) == []


def test_the_main_owner_can_change_only_to_a_co_owner(owner, org, joint, peter):
    property_services.update_property(owner, joint, owner=peter)
    assert Property.objects.get(pk=joint.pk).owner == peter
    outsider = property_services.create_owner(owner, name="Otieno Holdings Ltd")
    for new in (outsider, None):
        with pytest.raises(ValidationError) as exc:
            property_services.update_property(owner, joint, owner=new)
        assert "joint owners" in str(exc.value)


def test_format_percent():
    assert [format_percent(P(v)) for v in ("50.00", "33.50", "100", "0.25")] == ["50", "33.5", "100", "0.25"]


# ---------------------------------------------------------------------------
# Statements
# ---------------------------------------------------------------------------


def test_each_owner_gets_their_share_of_what_is_due(owner, org, jane, peter, january):
    solo = make_property(org, name="Baobab Flats")
    property_services.update_property(owner, solo, owner=jane)
    lease = make_lease(owner, solo, code="B1", rent=5000)
    bill(lease, JAN)
    pay(owner, lease, 5000, D(2025, 1, 12))

    mine = st(owner, jane)
    acacia, baobab = mine.blocks
    assert (acacia.share, acacia.joint, acacia.whole_due, acacia.due) == (P("60"), True, money(16000), money(9600))
    assert (baobab.joint, baobab.due) == (False, money(5000))
    # The figures are for the whole properties; the other owners' shares come off at the end.
    assert (mine.collected, mine.fee, mine.expenses) == (money(25000), money(2000), money(2000))
    assert (mine.whole_due, mine.others_share, mine.due) == (money(21000), money(6400), money(14600))
    assert any("owned jointly: Jane Wanjiru holds 60%" in n for n in mine.notes)

    his = st(owner, peter)
    assert [b.property.name for b in his.blocks] == ["Acacia Court"]
    assert (his.due, his.others_share) == (money(6400), money(9600))


def test_three_way_shares_round_each_owner(owner, org, prop, jane, peter):
    third = property_services.create_owner(owner, name="Amina Hassan")
    property_services.set_shares(owner, prop, [(jane, P("33.33")), (peter, P("33.33")), (third, P("33.34"))])
    lease = make_lease(owner, prop, code="A1", rent=10000)
    bill(lease, JAN)
    pay(owner, lease, 10000, D(2025, 1, 10))  # 9,000 due after the fee
    assert [st(owner, o).due for o in (jane, peter, third)] == [money("2999.70"), money("2999.70"),
                                                                  money("3000.60")]


def test_co_owners_are_statement_choices_and_counted_as_seen(owner, org, prop, joint, jane, peter):
    keys = [key for key, _label in statements.owner_choices(owner)]
    assert str(jane.public_id) in keys and str(peter.public_id) in keys and statements.NO_OWNER not in keys
    other = make_property(org, name="Baobab Flats")
    property_services.set_shares(owner, other, [(peter, P("50")), (make_owner(owner), P("50"))])
    scoped = add_member(org, "accountant", properties=[prop])
    # Peter's other property is out of sight, so his figures are partial.
    assert statements.fully_sees(scoped, jane) and not statements.fully_sees(scoped, peter)
    assert owners.visible_owner(scoped, peter.public_id) is None
    assert not st(scoped, peter).shows_remittances


def make_owner(actor):
    return property_services.create_owner(actor, name="Amina Hassan")


def test_co_owners_are_on_the_statements_to_send(owner, january):
    assert owners.to_send(owner, D(2025, 2, 10)) == (2, JAN)


def test_sending_saves_the_owners_part(owner, jane, january):
    send = owners.send_statement(owner, st(owner, jane))
    assert (send.collected, send.due) == (money(20000), money(9600))
    assert render_statement(st(owner, jane)).startswith(b"%PDF")


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------


def test_the_shares_page(client, owner, prop, jane, peter):
    login(client, owner)
    url = reverse("properties:shares", args=[prop.public_id])
    page = client.get(url)
    assert page.status_code == 200 and page.context["formset"].total_form_count() == 4
    data = {"shares-TOTAL_FORMS": "3", "shares-INITIAL_FORMS": "0",
            "shares-0-owner": jane.pk, "shares-0-percent": "70",
            "shares-1-owner": peter.pk, "shares-1-percent": "20",
            "shares-2-owner": "", "shares-2-percent": ""}
    bad = client.post(url, data)
    assert bad.status_code == 400 and "they must add up to 100%" in bad.content.decode()
    data["shares-1-percent"] = "30"
    assert client.post(url, data).status_code == 302
    detail = client.get(reverse("properties:detail", args=[prop.public_id])).content.decode()
    assert "Jane Wanjiru 70%, Peter Kamau 30%" in detail
    owners_page = client.get(reverse("properties:owners"))
    counts = {o.name: o.property_count for o in owners_page.context["owners"]}
    assert counts == {"Jane Wanjiru": 1, "Peter Kamau": 1}
    edit = client.get(reverse("properties:owner_edit", args=[peter.public_id])).content.decode()
    assert "Acacia Court" in edit and "30% share" in edit


def test_the_shares_page_needs_manage_and_scope(client, org, prop):
    viewer = add_member(org, "viewer", all_properties=True)
    login(client, viewer)
    assert client.get(reverse("properties:shares", args=[prop.public_id])).status_code == 403
    login(client, make_org())
    assert client.get(reverse("properties:shares", args=[prop.public_id])).status_code == 404


def test_the_statement_page_shows_the_share(client, owner, peter, january):
    login(client, owner)
    page = client.get(reverse("reports:owner_statement"), {"owner": peter.public_id, "month": "2025-01"})
    body = page.content.decode()
    assert "Less other owners" in body and "Owned jointly: 40% share" in body
    assert page.context["st"].due == money(6400)
