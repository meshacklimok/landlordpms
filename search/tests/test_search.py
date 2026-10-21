"""Global search and its suggestions (D-054)."""

import datetime
from decimal import Decimal

import pytest
from django.utils import timezone

from accounts.tests.factories import add_member, make_org, make_property
from billing.models import Invoice
from mpesa import services as mpesa_services
from mpesa.models import MpesaTransaction
from reports.tests.test_income import bill, make_lease, pay
from search import services

pytestmark = pytest.mark.django_db

D = datetime.date
JAN = D(2025, 1, 1)


@pytest.fixture
def owner():
    return make_org()


@pytest.fixture
def acacia(owner):
    return make_property(owner.organization, name="Acacia Court")


@pytest.fixture
def baobab(owner):
    return make_property(owner.organization, name="Baobab Flats")


@pytest.fixture
def a1(owner, acacia):
    """Tenant A1 on +254712000114, billed for January and paid with reference QAB12CD34E."""
    lease = make_lease(owner, acacia, code="A1")
    bill(lease, JAN)
    payment = pay(owner, lease, 20000, D(2025, 1, 6))
    payment.reference = "QAB12CD34E"
    payment.save(update_fields=["reference"])
    return lease


@pytest.fixture
def b7(owner, baobab):
    lease = make_lease(owner, baobab, code="B7")
    bill(lease, JAN)
    return lease


def keys(results):
    return [g.key for g in results.groups]


def labels(results, key):
    group = next((g for g in results.groups if g.key == key), None)
    return [h.label for h in group.hits] if group else []


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------


def test_short_queries_find_nothing(owner, a1):
    assert services.search(owner, "A").groups == [] and services.search(owner, "A").too_short
    assert services.search(owner, "   ").groups == []
    assert services.search(owner, None).q == ""


def test_query_is_tidied_and_capped():
    assert services.clean_query("  Tenant \n  A1 ") == "Tenant A1"
    assert len(services.clean_query("x" * 200)) == services.MAX_LENGTH


def test_a_tenant_by_name_and_by_phone(owner, a1):
    assert labels(services.search(owner, "tenant a1"), "tenants") == ["Tenant A1"]
    for typed in ("0712000114", "+254 712 000 114", "712000114", "0712 000", "000114"):
        assert labels(services.search(owner, typed), "tenants") == ["Tenant A1"], typed


def test_phone_digits():
    assert services.phone_digits("0712 34") == "71234"
    assert services.phone_digits("+254 712-34") == "71234"
    assert services.phone_digits("071") == ""  # too few to be useful
    assert services.phone_digits("A1 12345") == ""  # not a phone number


def test_the_groups_and_their_links(owner, a1):
    invoice = Invoice.objects.get(lease=a1)
    results = services.search(owner, invoice.number)
    assert labels(results, "invoices") == [invoice.number]
    assert results.groups[0].hits[0].url == f"/billing/invoices/{invoice.public_id}/"
    assert labels(services.search(owner, "QAB12"), "payments") == ["QAB12CD34E"]
    receipt = a1.payments.get().receipt.number
    assert labels(services.search(owner, receipt), "payments") == ["QAB12CD34E"]
    assert labels(services.search(owner, "acacia"), "properties") == ["Acacia Court"]
    unit_results = services.search(owner, "A1")
    assert "A1" in labels(unit_results, "units") and "Tenant A1" in labels(unit_results, "tenants")
    lease_hits = next(g for g in unit_results.groups if g.key == "leases").hits
    assert lease_hits[0].url == f"/leases/{a1.public_id}/" and "Tenant A1" in lease_hits[0].detail


def test_exact_and_leading_matches_come_first(owner, acacia):
    for code in ("XA10", "A10", "A100"):
        make_lease(owner, acacia, code=code)
    assert labels(services.search(owner, "A10"), "units") == ["A10", "A100", "XA10"]


def test_groups_are_capped_and_say_there_is_more(owner, acacia):
    for i in range(services.SUGGEST_LIMIT + 1):
        make_lease(owner, acacia, code=f"C{i}")
    group = next(g for g in services.search(owner, "Tenant C").groups if g.key == "tenants")
    assert len(group.hits) == services.SUGGEST_LIMIT and group.more
    assert group.list_url == "/tenants/?q=Tenant+C"
    page = next(g for g in services.search(owner, "Tenant C", limit=services.PAGE_LIMIT).groups
                if g.key == "tenants")
    assert len(page.hits) == services.SUGGEST_LIMIT + 1 and not page.more


def test_mpesa_by_code_account_and_phone(owner, a1):
    account = mpesa_services.add_account(owner, type="PAYBILL", number="600123", display_name="Rent Paybill")
    MpesaTransaction.objects.create(organization=owner.organization, payment_account=account,
                                    trans_id="TGH45JK67L", bill_ref="P1A1",
                                    amount=Decimal(5000), paid_at=timezone.now(), payer_phone="+254799111222")
    for typed in ("TGH45", "tgh45jk67l", "0799111222", "P1A1"):
        assert labels(services.search(owner, typed), "mpesa") == ["TGH45JK67L"], typed


# ---------------------------------------------------------------------------
# Who sees what
# ---------------------------------------------------------------------------


def test_nothing_from_another_organization(owner, a1):
    stranger = make_org()
    assert services.search(stranger, "Tenant A1").groups == []
    assert services.search(stranger, Invoice.objects.get(lease=a1).number).groups == []
    assert services.search(stranger, "QAB12CD34E").groups == []


def test_a_scoped_member_sees_only_their_properties(owner, a1, b7, acacia):
    manager = add_member(owner.organization, "manager", properties=[acacia])
    results = services.search(manager, "Tenant")
    assert labels(results, "tenants") == ["Tenant A1"]
    assert all("B7" not in h.label and "Baobab" not in h.detail for g in results.groups for h in g.hits)
    assert services.search(manager, Invoice.objects.get(lease=b7).number).groups == []
    assert services.search(manager, "Baobab").groups == []
    assert services.scoped(manager) and not services.scoped(owner)


def test_groups_follow_capabilities(owner, a1):
    caretaker = add_member(owner.organization, "caretaker", all_properties=True)
    assert services.search(caretaker, Invoice.objects.get(lease=a1).number).groups == []
    assert "payments" not in keys(services.search(caretaker, "QAB12"))
    viewer = add_member(owner.organization, "viewer", all_properties=True)
    results = services.search(viewer, "Tenant A1")
    assert "tenants" not in keys(results)
    # Without tenants.view, leases are not found by tenant name nor show it.
    assert all("Tenant A1" not in h.detail for g in services.search(viewer, "A1").groups for h in g.hits)


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------


def test_the_results_page(client, owner, a1):
    client.force_login(owner.user)
    page = client.get("/search/?q=tenant+a1").content.decode()
    assert "Tenant A1" in page and f"/tenants/{a1.lease_tenants.get().tenant.public_id}/" in page
    assert "Nothing matches" in client.get("/search/?q=zzzz").content.decode()
    assert "Type at least 2 characters" in client.get("/search/?q=z").content.decode()
    assert client.get("/search/").status_code == 200


def test_the_box_is_in_the_top_bar(client, owner):
    client.force_login(owner.user)
    page = client.get("/search/?q=abc").content.decode()
    assert 'data-suggest-url="/search/suggest/"' in page and "js/search.js" in page
    assert 'role="combobox"' in page and 'value="abc"' in page


def test_suggestions_as_json(client, owner, a1):
    client.force_login(owner.user)
    response = client.get("/search/suggest/?q=0712000114")
    assert response.status_code == 200 and response["Cache-Control"] == "private, no-store"
    data = response.json()
    assert data["q"] == "0712000114"
    tenants = next(g for g in data["groups"] if g["key"] == "tenants")
    assert tenants["title"] == "Tenants" and tenants["hits"][0]["label"] == "Tenant A1"
    assert set(tenants["hits"][0]) == {"label", "detail", "url"}
    assert client.get("/search/suggest/?q=a").json() == {"q": "a", "groups": []}


def test_suggestions_need_a_login(client):
    response = client.get("/search/suggest/?q=tenant")
    assert response.status_code == 302 and "/search/suggest/" in response["Location"]
