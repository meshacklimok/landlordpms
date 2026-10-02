"""Communications pages: render, act through services, and stay inside the member's scope."""

import pytest
from django.urls import reverse

from accounts.tests.factories import add_member, make_org, make_property
from accounts.tests.test_isolation import login
from billing.tests.test_invoicing import make_lease
from notifications import catalog, delivery, services
from notifications.models import Message, MessageTemplate, OrganizationNotificationRule

pytestmark = pytest.mark.django_db

Status = Message.Status


@pytest.fixture
def owner():
    return make_org()


@pytest.fixture
def org(owner):
    return owner.organization


@pytest.fixture
def prop(org):
    return make_property(org)


@pytest.fixture
def lease(owner, prop):
    return make_lease(owner, prop)


@pytest.fixture
def tenant(lease):
    return lease.primary_tenant


def announce(org, tenant, text="Water is off.", **kw):
    return delivery.notify(org, "announcement", tenant=tenant, context={"text": text}, send_now=False, **kw)


def pending_review(org, user, n=0):
    return delivery.notify(org, "payment_pending_review", user=user, dedupe_key=f"p:{n}",
                           context={"amount": "KES 5,000.00", "tenant_name": "W", "unit": "A1", "recorded_by": "X"})


def test_pages_render(client, owner, org, tenant):
    login(client, owner)
    m = announce(org, tenant)
    log = reverse("notifications:log")
    for url in (log, log + "?status=waiting&q=Water&type=announcement",
                log + f"?tenant={tenant.public_id}",
                reverse("notifications:detail", args=[m.public_id]), reverse("notifications:settings"),
                reverse("notifications:templates"), reverse("notifications:inbox"),
                reverse("notifications:template_edit", args=["announcement", "SMS", "sw"]),
                reverse("notifications:template_edit", args=["payment_pending_review", "IN_APP", "en"]),
                reverse("tenants:detail", args=[tenant.public_id])):
        assert client.get(url).status_code == 200, url


def test_log_filters_and_counts(client, owner, org, tenant):
    login(client, owner)
    a = announce(org, tenant, "Water is off.")
    b = announce(org, tenant, "Lift repairs.")
    Message.objects.filter(pk=b.pk).update(status=Status.FAILED)
    r = client.get(reverse("notifications:log"))
    assert r.context["counts"] == {"all": 2, "waiting": 1, "sent": 0, "failed": 1, "skipped": 0}
    assert list(client.get(reverse("notifications:log") + "?status=failed").context["page"]) == [b]
    assert list(client.get(reverse("notifications:log") + "?q=water").context["page"]) == [a]
    assert not client.get(reverse("notifications:log") + "?tenant=nope").context["page"].object_list


def test_log_is_scoped_to_visible_tenants(client, org, tenant, prop):
    other = make_lease(org.memberships.first(), make_property(org), code="B1")
    announce(org, tenant)
    hidden = announce(org, other.primary_tenant)
    manager = add_member(org, "manager", properties=[prop])
    login(client, manager)
    page = client.get(reverse("notifications:log")).context["page"]
    assert [m.tenant for m in page] == [tenant]
    assert client.get(reverse("notifications:detail", args=[hidden.public_id])).status_code == 404


def test_staff_messages_stay_out_of_the_log(client, owner, org):
    login(client, owner)
    staff = pending_review(org, owner.user)
    assert client.get(reverse("notifications:detail", args=[staff.public_id])).status_code == 404


def test_other_org_message_is_404(client, owner, org, tenant):
    m = announce(org, tenant)
    login(client, make_org())
    assert client.get(reverse("notifications:detail", args=[m.public_id])).status_code == 404


def test_capabilities(client, org):
    accountant = add_member(org, "accountant", all_properties=True)
    login(client, accountant)
    for name in ("log", "settings", "templates"):
        assert client.get(reverse(f"notifications:{name}")).status_code == 403, name
    assert client.get(reverse("notifications:inbox")).status_code == 200
    manager = add_member(org, "manager", all_properties=True)
    login(client, manager)
    assert client.get(reverse("notifications:templates")).status_code == 200
    assert client.get(reverse("notifications:settings")).status_code == 403


def test_retry(client, owner, org, tenant, outbox, django_capture_on_commit_callbacks):
    login(client, owner)
    m = announce(org, tenant)
    Message.objects.filter(pk=m.pk).update(status=Status.FAILED, attempts=3)
    with django_capture_on_commit_callbacks(execute=True):
        r = client.post(reverse("notifications:detail", args=[m.public_id]))
    assert r.status_code == 302
    m.refresh_from_db()
    assert m.status == Status.SENT and len(outbox) == 1


def test_settings_save_rule_and_reset(client, owner, org):
    login(client, owner)
    url = reverse("notifications:settings")
    r = client.post(url, {"type": "rent_due_soon", "enabled": "on", "offsets": "7, 3", "action": "save"})
    assert r.status_code == 302
    assert delivery.effective_rule(org, catalog.get("rent_due_soon")).offsets == (7, 3)
    r = client.post(url, {"type": "rent_due_soon", "action": "reset"})
    assert not OrganizationNotificationRule.objects.exists()

    client.post(url, {"type": "announcement", "action": "save"})  # switch unticked
    assert not delivery.effective_rule(org, catalog.get("announcement")).enabled

    r = client.post(url, {"type": "rent_due_soon", "enabled": "on", "offsets": "99", "action": "save"})
    assert r.status_code == 400 and "between" in r.content.decode()
    assert client.post(url, {"type": "nope"}).status_code == 404


def test_settings_quiet_hours(client, owner, org):
    login(client, owner)
    url = reverse("notifications:settings")
    assert client.post(url, {"action": "quiet", "start": "22:00", "end": "06:30"}).status_code == 302
    org.refresh_from_db()
    assert f"{org.quiet_hours_start:%H:%M}-{org.quiet_hours_end:%H:%M}" == "22:00-06:30"
    assert client.post(url, {"action": "quiet", "start": "late", "end": ""}).status_code == 400


def test_template_edit_save_error_and_reset(client, owner, org):
    login(client, owner)
    url = reverse("notifications:template_edit", args=["announcement", "SMS", "en"])
    r = client.post(url, {"body": "{text} -- {org_name}", "action": "save"})
    assert r.status_code == 302
    assert services.live_template(org, "announcement", "SMS", "en").body == "{text} -- {org_name}"

    r = client.post(url, {"body": "{rent}", "action": "save"})
    assert r.status_code == 400 and "{rent}" in r.content.decode()

    client.post(url, {"action": "reset"})
    assert not MessageTemplate.objects.exists()


@pytest.mark.parametrize("args", [("nope", "SMS", "en"), ("announcement", "WHATSAPP", "en"),
                                  ("payment_pending_review", "IN_APP", "sw")])
def test_template_edit_unknown_target_is_404(client, owner, args):
    login(client, owner)
    assert client.get(reverse("notifications:template_edit", args=args)).status_code == 404


def test_bell_counts_and_inbox_marks_read(client, owner, org):
    pending_review(org, owner.user, 1)
    pending_review(org, owner.user, 2)
    login(client, owner)
    r = client.get(reverse("tenants:list"))
    assert r.context["unread_count"] == 2
    r = client.get(reverse("notifications:inbox"))
    assert len(r.context["items"]) == 2 and all(i["unread"] for i in r.context["items"])
    assert services.unread_count(owner.user, org) == 0
    assert client.get(reverse("tenants:list")).context["unread_count"] == 0


def test_tenant_stop_and_resume_sms(client, owner, org, tenant):
    login(client, owner)
    url = reverse("notifications:tenant_channel", args=[tenant.public_id, "sms"])
    assert client.post(url).status_code == 302
    assert not services.channel_allowed(tenant, catalog.SMS)
    r = client.get(reverse("tenants:detail", args=[tenant.public_id]))
    assert not r.context["sms_allowed"] and "Resume SMS" in r.content.decode()
    client.post(url, {"allowed": "on"})
    assert services.channel_allowed(tenant, catalog.SMS)


def test_tenant_opt_out_scope(client, org, tenant):
    accountant = add_member(org, "accountant", all_properties=True)
    login(client, accountant)
    url = reverse("notifications:tenant_channel", args=[tenant.public_id, "sms"])
    assert client.post(url).status_code == 403
    login(client, make_org())
    assert client.post(url).status_code == 404
    assert services.channel_allowed(tenant, catalog.SMS)
