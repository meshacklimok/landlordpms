"""The tenant's payment link: making it, rent messages, the public page, limits and staff controls (D-046 item 1)."""

import datetime

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.urls import reverse
from django.utils import timezone

from accounts.tests.factories import add_member, make_org, make_property
from accounts.tests.test_isolation import login
from billing import invoicing
from billing.invoicing import lease_balance
from billing.tests.test_invoicing import FEB, bill, make_lease
from leases.models import Lease
from mpesa import paylinks, services
from mpesa.daraja import FakeDarajaClient
from mpesa.models import MpesaTransaction, PayLink, StkRequest
from mpesa.tests.test_stk import callback, post_hook
from notifications import catalog
from notifications.models import Message
from payments.models import Payment, PaymentAccount

pytestmark = pytest.mark.django_db

Status = StkRequest.Status
SITE = "https://pms.example.com"


@pytest.fixture(autouse=True)
def _site(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    settings.SITE_URL = SITE


@pytest.fixture
def owner():
    return make_org(name="Acme")


@pytest.fixture
def org(owner):
    return owner.organization


@pytest.fixture
def prop(org):
    return make_property(org)


@pytest.fixture
def lease(owner, prop):
    lease = make_lease(owner, prop)
    bill(lease, FEB)
    return lease


def paybill(owner, passkey="pk"):
    account = services.add_account(owner, type="PAYBILL", number="600123", display_name="Paybill 600123")
    return services.save_credentials(owner, account, environment="SANDBOX", consumer_key="k", consumer_secret="s",
                                     passkey=passkey)


@pytest.fixture
def creds(owner):
    return paybill(owner)


@pytest.fixture
def link(lease, creds):
    return paylinks.link_for(lease)


def page(link):
    return reverse("pay_link", args=[link.token])


# ---------------------------------------------------------------------------
# The link and rent messages
# ---------------------------------------------------------------------------


def test_link_needs_a_paybill_that_can_send_prompts(owner, lease):
    paybill(owner, passkey="")
    assert paylinks.link_for(lease) is None and paylinks.message_url(lease) == ""
    assert not PayLink.objects.exists()


def test_link_is_made_once_per_lease(lease, creds):
    first = paylinks.link_for(lease)
    assert first is not None and paylinks.link_for(lease) == first
    assert paylinks.message_url(lease) == f"{SITE}/p/{first.token}/"


def test_rent_sms_carries_the_link_and_whatsapp_wording_does_not(owner, org, prop, creds):
    fresh = make_lease(owner, prop, due_day=5)
    invoicing.generate_month(org, FEB, today=datetime.date(2026, 1, 27))
    [m] = Message.objects.filter(type="invoice_issued")
    url = paylinks.message_url(fresh)
    assert url and m.body.endswith(f"{fresh.unit.payment_reference}. {url} {org.name}")
    for codename in ("invoice_issued", "rent_due_soon", "rent_overdue"):
        ntype = catalog.get(codename)
        assert "pay_link" in ntype.placeholders
        assert "{pay_link}" in ntype.default_body(catalog.SMS, catalog.SW)
        assert "pay_link" not in ntype.default_body(catalog.WHATSAPP, catalog.EN)


def test_rent_sms_without_a_link_reads_as_before(owner, org, prop):
    fresh = make_lease(owner, prop, due_day=5)
    invoicing.generate_month(org, FEB, today=datetime.date(2026, 1, 27))
    [m] = Message.objects.filter(type="invoice_issued")
    assert m.body.endswith(f"Pay with reference {fresh.unit.payment_reference}. {org.name}")


# ---------------------------------------------------------------------------
# The public page
# ---------------------------------------------------------------------------


def test_page_shows_what_is_owed_and_the_paybill(client, lease, link):
    response = client.get(page(link))
    body = response.content.decode()
    assert response.status_code == 200 and response["X-Robots-Tag"] == "noindex, nofollow"
    assert lease.unit.code in body and 'value="15000"' in body
    assert f"Paybill 600123, account {lease.unit.payment_reference}" in body
    assert lease.primary_tenant.phone not in body  # the page never shows who lives there


def test_unknown_token_shows_the_closed_page(client, link):
    response = client.get(reverse("pay_link", args=["nope"]))
    assert response.status_code == 404 and "not working" in response.content.decode()


def test_tenant_sends_a_prompt_and_it_is_confirmed_on_the_lease(client, lease, link, creds):
    response = client.post(page(link), {"phone": "0712345602", "amount": "5000"})
    req = StkRequest.objects.get()
    assert response.status_code == 302 and response["Location"] == reverse(
        "pay_link_status", args=[link.token, req.public_id])
    assert req.requested_by is None and req.pay_link == link and req.lease == lease
    assert "Check your phone" in client.get(response["Location"]).content.decode()

    post_hook(client, creds, callback(req, amount="5000"))
    tx = MpesaTransaction.objects.get()
    assert tx.status == MpesaTransaction.Status.MATCHED and tx.payment.lease == lease
    assert tx.payment.status == Payment.Status.CONFIRMED and lease_balance(lease) == 10000
    body = client.get(response["Location"]).content.decode()
    assert "Paid." in body and tx.payment.receipt.number in body


def test_any_kenyan_number_may_pay_but_not_a_foreign_one(client, link):
    assert client.post(page(link), {"phone": "0799000111", "amount": "100"}).status_code == 302
    response = client.post(page(link), {"phone": "+14155550100", "amount": "100"})
    assert response.status_code == 400 and "Kenyan numbers only" in response.content.decode()


def test_safaricom_refusal_is_shown_on_the_page(client, link):
    FakeDarajaClient.fail = "Invalid Access Token"
    response = client.post(page(link), {"phone": "0712345602", "amount": "100"})
    assert response.status_code == 400 and "Invalid Access Token" in response.content.decode()
    assert StkRequest.objects.get().status == Status.FAILED


def test_status_page_of_another_link_is_not_found(client, owner, prop, lease, link, creds):
    req = paylinks.request_payment(link, phone="0712345602", amount=100)
    other = paylinks.link_for(make_lease(owner, prop, code="B22"))
    assert client.get(reverse("pay_link_status", args=[other.token, req.public_id])).status_code == 404


# ---------------------------------------------------------------------------
# Limits
# ---------------------------------------------------------------------------


def _made(link, phone, minutes_ago):
    req = StkRequest.objects.create(organization=link.organization, payment_account=PaymentAccount.objects.get(),
                                    lease=link.lease, pay_link=link, phone=phone, amount=100,
                                    account_reference="X", status=Status.FAILED)
    StkRequest.objects.filter(pk=req.pk).update(created_at=timezone.now() - datetime.timedelta(minutes=minutes_ago))


def test_a_link_sends_a_few_prompts_in_a_row_then_waits(link):
    for i in range(paylinks.PER_LINK_BURST):
        paylinks.request_payment(link, phone=f"07110002{i:02}", amount=100)
    with pytest.raises(ValidationError, match="few minutes"):
        paylinks.request_payment(link, phone="0711000299", amount=100)


def test_a_link_has_a_daily_ceiling(link):
    for i in range(paylinks.PER_LINK_DAY):
        _made(link, f"+2547110003{i:02}", minutes_ago=60 + i)
    with pytest.raises(ValidationError, match="today"):
        paylinks.request_payment(link, phone="0711000399", amount=100)


def test_one_phone_gets_only_a_few_prompts_a_day_from_any_link(owner, prop, link):
    other = paylinks.link_for(make_lease(owner, prop, code="B22"))
    for i in range(paylinks.PER_PHONE_DAY):
        _made(link if i % 2 else other, "+254711000400", minutes_ago=60 + i)
    with pytest.raises(ValidationError) as e:
        paylinks.request_payment(link, phone="0711000400", amount=100)
    assert "phone" in e.value.error_dict


def test_page_is_limited_per_ip(client, link):
    for _i in range(paylinks.PER_IP_HOUR):
        client.get(page(link))
    assert client.get(page(link)).status_code == 429


# ---------------------------------------------------------------------------
# When the link stops working
# ---------------------------------------------------------------------------


def test_reset_replaces_the_link(client, owner, lease, link):
    old = link.token
    new = paylinks.reset(owner, lease)
    assert new.token != old and paylinks.find(old) is None and paylinks.find(new.token) == new
    assert client.get(reverse("pay_link", args=[old])).status_code == 404


def test_turned_off_link_leaves_rent_messages_without_it(owner, lease, link):
    paylinks.disable(owner, lease)
    assert paylinks.find(link.token) is None and paylinks.message_url(lease) == ""
    paylinks.enable(owner, lease)
    assert paylinks.find(link.token) is not None


def test_ended_lease_takes_payments_only_while_it_owes(lease, link):
    Lease.all_objects.filter(pk=lease.pk).update(status=Lease.Status.ENDED)
    assert paylinks.find(link.token) is not None
    Lease.all_objects.filter(pk=lease.pk).update(status=Lease.Status.RENEWED)
    assert paylinks.find(link.token) is None


def test_archived_lease_or_suspended_org_closes_the_link(org, lease, link):
    org.status = org.Status.FROZEN
    org.save()
    assert paylinks.find(link.token) is None


def test_only_staff_who_record_payments_manage_the_link(org, lease, link):
    with pytest.raises(PermissionDenied):
        paylinks.reset(add_member(org, "caretaker", all_properties=True), lease)
    with pytest.raises(PermissionDenied):
        paylinks.disable(make_org(name="Other"), lease)


def test_request_page_shows_and_manages_the_link(client, owner, lease, link):
    login(client, owner)
    url = reverse("mpesa:request", args=[lease.public_id])
    assert paylinks.url(link) in client.get(url).content.decode()
    client.post(url, {"action": "link_disable"})
    assert "Turned off" in client.get(url).content.decode()
    client.post(url, {"action": "link_enable"})
    client.post(url, {"action": "link_reset"})
    link.refresh_from_db()
    assert link.is_active and paylinks.url(link) in client.get(url).content.decode()
