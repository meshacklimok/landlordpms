"""Webhooks (D-083)."""

import datetime
import hashlib
import hmac
import json
import socket

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone

from accounts.tests.factories import add_member, make_org, make_property
from accounts.tests.test_isolation import login
from api import webhooks
from api.models import WebhookDelivery, WebhookEndpoint
from audit.models import AuditEvent
from billing.tests.test_invoicing import FEB, bill, make_lease
from core import crypto
from core.jobs import JOBS
from payments import services as payment_services
from payments.models import Payment

pytestmark = pytest.mark.django_db

D = datetime.date
URL = "https://hooks.example.com/lpms"
ALL = list(WebhookEndpoint.Event.values)
PUBLIC_IP = "93.184.216.34"
REAL_POST = webhooks._post  # the autouse fixture replaces it


@pytest.fixture(autouse=True)
def _network(settings, monkeypatch):
    """No real DNS or HTTP: every host resolves to PUBLIC_IP unless a test says otherwise, and every POST is
    recorded and answered with `answers` (default 200)."""
    settings.WEBHOOKS_ALLOW_PRIVATE = False
    addresses = {}

    def resolve(host, port, *args, **kwargs):
        ip = addresses.get(host, PUBLIC_IP)
        if ip is None:
            raise socket.gaierror("not found")
        return [(socket.AF_INET6 if ":" in ip else socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port))]

    sent = []

    def post(url, body, headers, address=None):
        sent.append({"url": url, "body": body, "headers": headers, "address": address})
        answer = post.answers.pop(0) if post.answers else 200
        if isinstance(answer, Exception):
            raise answer
        return answer, "ok"

    post.answers = []
    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    monkeypatch.setattr(webhooks, "_post", post)
    return {"addresses": addresses, "sent": sent, "post": post}


@pytest.fixture
def net(_network):
    return _network


@pytest.fixture
def owner():
    return make_org(name="Acme")


@pytest.fixture
def prop(owner):
    return make_property(owner.organization, name="Acacia")


def later(minutes=3):
    return timezone.now() + datetime.timedelta(minutes=minutes)


def bodies(net):
    return [json.loads(s["body"]) for s in net["sent"]]


# ---------------------------------------------------------------------------
# Endpoints and safety
# ---------------------------------------------------------------------------


def test_adding_an_endpoint_keeps_the_secret_encrypted_and_starts_after_the_latest_event(owner, prop):
    make_lease(owner, prop, code="A1")
    latest = AuditEvent.objects.filter(organization=owner.organization).order_by("-pk").first()
    endpoint, secret = webhooks.create_endpoint(owner, url=f" {URL} ", events=["payment.confirmed"] * 2)
    assert secret.startswith("whsec_") and secret not in endpoint.secret_encrypted
    assert crypto.decrypt(endpoint.secret_encrypted) == secret
    assert endpoint.url == URL and endpoint.events == ["payment.confirmed"] and endpoint.membership == owner
    assert endpoint.last_audit_id == latest.pk
    event = AuditEvent.objects.get(action="webhook.create", object_id=str(endpoint.public_id))
    assert secret not in json.dumps(event.changes)


def test_adding_endpoints_is_checked(owner, prop):
    for bad in ({"url": URL, "events": []}, {"url": URL, "events": ["lease.archived"]},
                {"url": "http://hooks.example.com/x", "events": ALL}):
        with pytest.raises(ValidationError):
            webhooks.create_endpoint(owner, **bad)
    manager = add_member(owner.organization, "manager", properties=[prop])
    with pytest.raises(PermissionDenied):
        webhooks.create_endpoint(manager, url=URL, events=ALL)
    for n in range(webhooks.MAX_ENDPOINTS):
        webhooks.create_endpoint(owner, url=f"{URL}/{n}", events=ALL)
    with pytest.raises(ValidationError):
        webhooks.create_endpoint(owner, url=URL, events=ALL)


@pytest.mark.parametrize("url, ip", [
    ("http://hooks.example.com/", PUBLIC_IP),
    ("https://hooks.example.com:8443/", PUBLIC_IP),
    ("https://user:pw@hooks.example.com/", PUBLIC_IP),
    ("https://hooks.example.com/", "10.0.0.5"),
    ("https://hooks.example.com/", "127.0.0.1"),
    ("https://hooks.example.com/", "169.254.169.254"),
    ("https://hooks.example.com/", "100.64.0.1"),
    ("https://hooks.example.com/", "::1"),
    ("https://hooks.example.com/", "::ffff:192.168.1.1"),
    ("https://hooks.example.com/", None),
    ("ftp://hooks.example.com/", PUBLIC_IP),
])
def test_only_public_https_addresses_are_accepted(net, url, ip):
    net["addresses"]["hooks.example.com"] = ip
    with pytest.raises(ValidationError):
        webhooks.check_url(url)


def test_public_addresses_pass_and_development_may_allow_private_ones(net, settings):
    webhooks.check_url("https://hooks.example.com:443/x")
    net["addresses"]["localhost"] = "127.0.0.1"
    with pytest.raises(ValidationError):
        webhooks.check_url("http://localhost:8000/hook")
    settings.WEBHOOKS_ALLOW_PRIVATE = True
    webhooks.check_url("http://localhost:8000/hook")


def test_the_post_goes_to_the_checked_address_not_a_second_lookup(net, owner):
    endpoint, _secret = webhooks.create_endpoint(owner, url=URL, events=ALL)
    webhooks.ping(owner, endpoint)
    assert net["sent"][-1]["address"] == PUBLIC_IP


def test_the_pinned_connection_ignores_dns(monkeypatch):
    seen = []

    def connect(where, *args, **kwargs):
        seen.append(where)
        raise ConnectionRefusedError("stop here")

    monkeypatch.setattr(socket, "create_connection", connect)
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: pytest.fail("looked the name up again"))
    with pytest.raises(OSError):
        REAL_POST(URL, b"{}", {}, PUBLIC_IP)
    assert seen == [(PUBLIC_IP, 443)]


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------


def test_payments_and_invoices_become_signed_deliveries(net, owner, prop):
    lease = make_lease(owner, prop, code="A1")
    endpoint, secret = webhooks.create_endpoint(owner, url=URL, events=["payment.confirmed", "invoice.issued"])
    bill(lease, FEB)
    payment = payment_services.record_payment(owner, lease, amount=5000, method=Payment.Method.CASH,
                                              paid_at=D(2026, 2, 3))
    # Events are picked up once they are two minutes old.
    assert webhooks.run()["queued"] == 0 and net["sent"] == []
    counts = webhooks.run(later())
    assert counts["queued"] == 2 and counts["sent"] == 2
    first, second = net["sent"]
    assert [b["type"] for b in bodies(net)] == ["invoice.issued", "payment.confirmed"]
    data = bodies(net)[1]
    assert data["data"]["object"]["id"] == str(payment.public_id) and data["data"]["object"]["amount"] == "5000.00"
    delivery = WebhookDelivery.objects.get(event="payment.confirmed")
    assert data["id"] == str(delivery.public_id) and second["headers"]["X-LPMS-Delivery"] == data["id"]
    assert second["headers"]["X-LPMS-Event"] == "payment.confirmed" and second["url"] == URL
    stamp, signature = (part.split("=", 1)[1] for part in second["headers"]["X-LPMS-Signature"].split(","))
    expected = hmac.new(secret.encode(), f"{stamp}.".encode() + second["body"], hashlib.sha256).hexdigest()
    assert hmac.compare_digest(signature, expected)
    assert delivery.status == WebhookDelivery.Status.SENT and delivery.attempts == 1
    endpoint.refresh_from_db()
    assert endpoint.last_success_at is not None
    # Nothing is sent twice.
    assert webhooks.run(later())["queued"] == 0 and len(net["sent"]) == 2


def test_only_chosen_events_of_this_organization_after_the_endpoint_was_added(net, owner, prop):
    lease = make_lease(owner, prop, code="A1")
    bill(lease, FEB)  # before the endpoint: no backfill
    webhooks.create_endpoint(owner, url=URL, events=["payment.reversed"])
    other = make_org(name="Other")
    other_lease = make_lease(other, make_property(other.organization, name="Baobab"), code="Z9")
    payment_services.record_payment(other, other_lease, amount=100, method=Payment.Method.CASH,
                                    paid_at=D(2026, 2, 3))
    payment_services.record_payment(owner, lease, amount=100, method=Payment.Method.CASH, paid_at=D(2026, 2, 3))
    assert webhooks.run(later())["queued"] == 0 and net["sent"] == []


def test_a_failing_endpoint_is_retried_then_failed_then_can_be_sent_again(net, owner, prop):
    lease = make_lease(owner, prop, code="A1")
    endpoint, _secret = webhooks.create_endpoint(owner, url=URL, events=["invoice.issued"])
    bill(lease, FEB)
    net["post"].answers = [500, OSError("timed out")] + [302] * 5
    now = later()
    assert webhooks.run(now)["will_retry"] == 1
    delivery = WebhookDelivery.objects.get()
    assert delivery.next_attempt_at == now + datetime.timedelta(minutes=1) and delivery.last_status_code == 500
    for _retry in webhooks.RETRY_DELAYS:
        now = delivery.next_attempt_at
        webhooks.run(now)
        delivery.refresh_from_db()
    assert delivery.status == WebhookDelivery.Status.FAILED and delivery.attempts == 7
    assert len(net["sent"]) == 7 and len({s["headers"]["X-LPMS-Delivery"] for s in net["sent"]}) == 1
    endpoint.refresh_from_db()
    assert endpoint.consecutive_failures == 7 and not endpoint.is_paused
    redone = webhooks.redeliver(owner, delivery)
    assert redone.status == WebhookDelivery.Status.SENT and len(net["sent"]) == 8
    endpoint.refresh_from_db()
    assert endpoint.consecutive_failures == 0
    with pytest.raises(ValidationError):
        webhooks.redeliver(owner, redone)
    with pytest.raises(ValidationError):
        webhooks.redeliver(make_org(name="Other"), redone)


def test_many_failures_pause_the_endpoint_and_resuming_sends_what_waited(net, owner, prop):
    lease = make_lease(owner, prop, code="A1")
    endpoint, _secret = webhooks.create_endpoint(owner, url=URL, events=["invoice.issued"])
    bill(lease, FEB)
    WebhookEndpoint.objects.filter(pk=endpoint.pk).update(consecutive_failures=webhooks.AUTO_PAUSE_AFTER - 1)
    net["post"].answers = [503]
    webhooks.run(later())
    endpoint.refresh_from_db()
    assert endpoint.is_paused and "25" in endpoint.pause_reason
    assert webhooks.run(later(60))["sent"] == 0 and len(net["sent"]) == 1
    webhooks.resume(owner, endpoint)
    assert webhooks.run(later(60))["sent"] == 1 and len(net["sent"]) == 2


def test_events_wait_while_the_maker_cannot_send(net, owner, prop):
    lease = make_lease(owner, prop, code="A1")
    endpoint, _secret = webhooks.create_endpoint(owner, url=URL, events=["invoice.issued"])
    owner.is_active = False
    owner.save(update_fields=["is_active"])
    bill(lease, FEB)
    assert webhooks.run(later())["queued"] == 0
    endpoint.refresh_from_db()
    assert "no longer manage" in webhooks.blocked_reason(endpoint)
    owner.is_active = True
    owner.save(update_fields=["is_active"])
    assert webhooks.run(later())["sent"] == 1


def test_an_endpoint_now_pointing_at_a_private_address_is_not_called(net, owner, prop):
    lease = make_lease(owner, prop, code="A1")
    webhooks.create_endpoint(owner, url=URL, events=["invoice.issued"])
    bill(lease, FEB)
    net["addresses"]["hooks.example.com"] = "10.1.2.3"
    assert webhooks.run(later())["will_retry"] == 1 and net["sent"] == []
    assert "private network" in WebhookDelivery.objects.get().last_error


def test_ping_purge_and_the_job(net, owner):
    endpoint, _secret = webhooks.create_endpoint(owner, url=URL, events=ALL)
    delivery = webhooks.ping(owner, endpoint)
    assert delivery.status == WebhookDelivery.Status.SENT and bodies(net)[0]["type"] == "ping"
    net["post"].answers = [500]
    assert webhooks.ping(owner, endpoint).status == WebhookDelivery.Status.FAILED  # no retries for a test
    assert webhooks.purge(timezone.now() + webhooks.KEEP + datetime.timedelta(days=1)) == 2
    assert "send_webhooks" in JOBS
    call_command("send_webhooks")


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------


def test_the_pages_add_show_the_secret_once_and_manage_an_endpoint(client, net, owner):
    login(client, owner)
    assert b"staff/webhooks/" in client.get(reverse("accounts:staff")).content
    page = client.get(reverse("api:webhooks"))
    assert page.status_code == 200 and b"No webhooks yet." in page.content
    response = client.post(reverse("api:webhooks"), {"url": URL, "events": ["payment.confirmed"]})
    assert response.status_code == 200 and response["Cache-Control"] == "no-store"
    secret = response.context["secret"]
    assert secret.encode() in response.content
    endpoint = WebhookEndpoint.objects.get()
    detail = client.get(reverse("api:webhook", args=[endpoint.public_id]))
    assert detail.status_code == 200 and secret.encode() not in detail.content
    client.post(reverse("api:webhook", args=[endpoint.public_id]), {"events": ["lease.ended", "tenant.created"]})
    endpoint.refresh_from_db()
    assert endpoint.events == ["lease.ended", "tenant.created"]
    action = lambda name: client.post(reverse("api:webhook_action", args=[endpoint.public_id, name]))  # noqa: E731
    assert action("ping").status_code == 302 and len(net["sent"]) == 1
    action("pause")
    endpoint.refresh_from_db()
    assert endpoint.is_paused
    action("resume")
    endpoint.refresh_from_db()
    assert not endpoint.is_paused
    assert action("explode").status_code == 404
    assert client.post(reverse("api:webhooks"), {"url": "http://x.example.com", "events": []}).context["form"].errors
    action("delete")
    assert not WebhookEndpoint.objects.exists()
    assert AuditEvent.objects.filter(action="webhook.delete").count() == 1


def test_staff_without_organization_manage_and_other_organizations_are_kept_out(client, net, owner, prop):
    endpoint, _secret = webhooks.create_endpoint(owner, url=URL, events=ALL)
    delivery = webhooks.ping(owner, endpoint)
    manager = add_member(owner.organization, "manager", properties=[prop])
    login(client, manager)
    assert client.get(reverse("api:webhooks")).status_code == 403
    assert client.get(reverse("api:webhook", args=[endpoint.public_id])).status_code == 403
    assert b"staff/webhooks" not in client.get(reverse("accounts:staff")).content
    client.logout()
    login(client, make_org(name="Other"))
    assert client.get(reverse("api:webhook", args=[endpoint.public_id])).status_code == 404
    assert client.post(reverse("api:webhook_action", args=[endpoint.public_id, "delete"])).status_code == 404
    assert client.post(reverse("api:redeliver", args=[delivery.public_id])).status_code == 404
    assert WebhookEndpoint.objects.filter(pk=endpoint.pk).exists()
