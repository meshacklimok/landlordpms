"""Staff actions: rules, quiet hours, templates, tenant opt-outs, retries and the bell."""

import datetime

import pytest
from django.core.exceptions import PermissionDenied, ValidationError

from accounts.tests.factories import add_member, make_org
from audit.models import AuditEvent
from notifications import catalog, delivery, services
from notifications.models import ConsentRecord, Message, MessageTemplate, OrganizationNotificationRule
from tenants import services as tenant_services

pytestmark = pytest.mark.django_db

Status = Message.Status


@pytest.fixture
def owner():
    return make_org()


@pytest.fixture
def org(owner):
    return owner.organization


@pytest.fixture
def tenant(owner):
    return tenant_services.create_tenant(owner, name="Wanjiku", phone="0712345678")


def rule(org, codename):
    return delivery.effective_rule(org, catalog.get(codename))


# ---------------------------------------------------------------------------
# Rules and quiet hours
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value, expected", [("7, 3", (7, 3)), ("3 7 3", (7, 3)), ("0", (0,)), ([5], (5,))])
def test_parse_offsets(value, expected):
    assert services.parse_offsets(catalog.get("rent_due_soon"), value) == expected


@pytest.mark.parametrize("value", ["", "x", "1,2,3,4", "61", "-1"])
def test_parse_offsets_rejects(value):
    with pytest.raises(ValidationError):
        services.parse_offsets(catalog.get("rent_due_soon"), value)


def test_overdue_offsets_start_at_one():
    with pytest.raises(ValidationError):
        services.parse_offsets(catalog.get("rent_overdue"), "0")


def test_save_rule_stores_only_the_change(owner, org):
    services.save_rule(owner, "rent_due_soon", enabled=True, offsets="7, 3", include_co_tenants=True)
    assert rule(org, "rent_due_soon").offsets == (7, 3)
    assert rule(org, "rent_due_soon").include_co_tenants
    assert AuditEvent.objects.filter(action="notifications.rule").count() == 1

    # Back to the default: the row goes away.
    services.save_rule(owner, "rent_due_soon", enabled=True, offsets="3")
    assert not OrganizationNotificationRule.objects.filter(organization=org).exists()
    assert AuditEvent.objects.filter(action="notifications.rule").count() == 2


def test_save_rule_switch_off_and_no_op(owner, org):
    services.save_rule(owner, "announcement", enabled=False)
    assert not rule(org, "announcement").enabled
    services.save_rule(owner, "announcement", enabled=False)
    assert AuditEvent.objects.filter(action="notifications.rule").count() == 1


def test_mandatory_type_cannot_be_switched_off(monkeypatch, owner):
    ntype = catalog.get("announcement")
    monkeypatch.setitem(catalog.BY_CODENAME, "announcement",
                        catalog.NotificationType(**{**ntype.__dict__, "mandatory": True}))
    with pytest.raises(ValidationError):
        services.save_rule(owner, "announcement", enabled=False)


def test_rule_rejects_a_channel_without_wording(owner):
    with pytest.raises(ValidationError):
        services.save_rule(owner, "announcement", enabled=True, channels=[catalog.WHATSAPP])


def test_rules_need_organization_manage(org):
    manager = add_member(org, "manager", all_properties=True)
    with pytest.raises(PermissionDenied):
        services.save_rule(manager, "announcement", enabled=False)
    with pytest.raises(PermissionDenied):
        services.set_quiet_hours(manager, start=datetime.time(22), end=datetime.time(6))


def test_quiet_hours(owner, org):
    services.set_quiet_hours(owner, start=datetime.time(22), end=datetime.time(6))
    org.refresh_from_db()
    assert (org.quiet_hours_start, org.quiet_hours_end) == (datetime.time(22), datetime.time(6))
    event = AuditEvent.objects.get(action="notifications.quiet_hours")
    assert event.changes["quiet_hours"] == ["21:00–07:00", "22:00–06:00"]


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------


def test_save_template_versions_and_reset(owner, org):
    t1 = services.save_template(owner, "announcement", catalog.SMS, catalog.EN, body="{text} - {org_name} ")
    assert t1.body == "{text} - {org_name}"
    t2 = services.save_template(owner, "announcement", catalog.SMS, catalog.EN, body="{text}\n{org_name}")
    t1.refresh_from_db()
    assert t1.is_archived and not t2.is_archived
    assert services.live_template(org, "announcement", catalog.SMS, catalog.EN) == t2

    services.reset_template(owner, "announcement", catalog.SMS, catalog.EN)
    assert services.live_template(org, "announcement", catalog.SMS, catalog.EN) is None
    assert AuditEvent.objects.filter(action="notifications.template").count() == 3


def test_saving_the_default_text_removes_the_override(owner, org):
    services.save_template(owner, "announcement", catalog.SMS, catalog.EN, body="{text}")
    default = catalog.get("announcement").bodies[(catalog.SMS, catalog.EN)]
    assert services.save_template(owner, "announcement", catalog.SMS, catalog.EN, body=default) is None
    assert services.live_template(org, "announcement", catalog.SMS, catalog.EN) is None


@pytest.mark.parametrize("body", ["", "{secret}", "{text.__class__}", "x" * 500])
def test_save_template_rejects(owner, body):
    with pytest.raises(ValidationError) as e:
        services.save_template(owner, "announcement", catalog.SMS, catalog.EN, body=body)
    assert "body" in e.value.message_dict


def test_staff_templates_are_english_only(owner):
    with pytest.raises(ValidationError):
        services.save_template(owner, "payment_pending_review", catalog.IN_APP, catalog.SW, body="{amount}")
    with pytest.raises(ValidationError):
        services.save_template(owner, "announcement", catalog.WHATSAPP, catalog.EN, body="{text}")


def test_templates_need_templates_manage(org):
    accountant = add_member(org, "accountant", all_properties=True)
    with pytest.raises(PermissionDenied):
        services.save_template(accountant, "announcement", catalog.SMS, catalog.EN, body="{text}")
    assert not MessageTemplate.objects.exists()


# ---------------------------------------------------------------------------
# Tenant opt-out
# ---------------------------------------------------------------------------


def test_stop_and_resume_sms(owner, org, tenant):
    assert services.channel_allowed(tenant, catalog.SMS)
    record = services.set_tenant_channel(owner, tenant, catalog.SMS, allowed=False, note="Asked on the phone")
    assert record.source == ConsentRecord.Source.STAFF and not record.granted
    assert not services.channel_allowed(tenant, catalog.SMS)
    assert services.set_tenant_channel(owner, tenant, catalog.SMS, allowed=False) is None

    m = delivery.notify(org, "announcement", tenant=tenant, context={"text": "Hi"})
    assert m.status == Status.SKIPPED

    services.set_tenant_channel(owner, tenant, catalog.SMS, allowed=True)
    assert services.channel_allowed(tenant, catalog.SMS)
    assert AuditEvent.objects.filter(action="tenant.consent").count() == 2


def test_whatsapp_needs_a_grant(tenant):
    assert not services.channel_allowed(tenant, catalog.WHATSAPP)


def test_opt_out_needs_tenant_manage_and_visibility(org, tenant):
    accountant = add_member(org, "accountant", all_properties=True)
    with pytest.raises(PermissionDenied):
        services.set_tenant_channel(accountant, tenant, catalog.SMS, allowed=False)
    scoped = add_member(org, "manager")
    with pytest.raises(PermissionDenied):
        services.set_tenant_channel(scoped, tenant, catalog.SMS, allowed=False)
    other = make_org()
    with pytest.raises(PermissionDenied):
        services.set_tenant_channel(other, tenant, catalog.SMS, allowed=False)


# ---------------------------------------------------------------------------
# Retry and the bell
# ---------------------------------------------------------------------------


def test_retry_failed_message(owner, org, tenant, outbox, django_capture_on_commit_callbacks):
    m = delivery.notify(org, "announcement", tenant=tenant, context={"text": "Hi"}, send_now=False)
    Message.objects.filter(pk=m.pk).update(status=Status.FAILED, attempts=3, error="boom")
    m.refresh_from_db()
    with django_capture_on_commit_callbacks(execute=True):
        services.retry_message(owner, m)
    m.refresh_from_db()
    assert m.status == Status.SENT and m.attempts == 1 and m.error == ""
    assert AuditEvent.objects.filter(action="message.retry").exists()


def test_only_failed_messages_are_retried(owner, org, tenant):
    m = delivery.notify(org, "announcement", tenant=tenant, context={"text": "Hi"}, send_now=False)
    with pytest.raises(ValidationError):
        services.retry_message(owner, m)
    accountant = add_member(org, "accountant", all_properties=True)
    Message.objects.filter(pk=m.pk).update(status=Status.FAILED)
    with pytest.raises(PermissionDenied):
        services.retry_message(accountant, m)


def test_inbox_and_unread(owner, org):
    manager = add_member(org, "manager", all_properties=True)
    for n in range(2):
        delivery.notify(org, "payment_pending_review", user=owner.user,
                        context={"amount": n, "tenant_name": "W", "unit": "A1", "recorded_by": "X"},
                        dedupe_key=f"p:{n}")
    assert services.unread_count(owner.user, org) == 2
    assert services.unread_count(manager.user, org) == 0
    first = services.inbox(owner.user, org).first()
    assert services.mark_read(owner.user, org, [first.pk]) == 1
    assert services.unread_count(owner.user, org) == 1
    assert services.mark_read(owner.user, org) == 1
    assert services.unread_count(owner.user, org) == 0
