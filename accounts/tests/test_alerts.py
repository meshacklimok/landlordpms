"""Security alerts to account holders, organization managers and operators (D-108 items 2 and 3)."""

import datetime

import pytest
from django.contrib.auth import authenticate
from django.core import mail
from django.core.cache import cache
from django.utils import timezone

from accounts import alerts, mfa, services
from accounts.tests.factories import PASSWORD, add_member, make_org, role
from accounts.tests.test_mfa import code, enable
from api import services as api_services
from audit import services as audit
from core import alerts as ops_alerts
from core import watch
from notifications.models import Message

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _clear():
    cache.clear()


@pytest.fixture
def org(django_capture_on_commit_callbacks):
    owner = make_org("Kilimani Homes")
    partner = add_member(owner.organization, "owner", all_properties=True)
    manager = add_member(owner.organization, "manager", all_properties=True)
    return {"owner": owner, "partner": partner, "manager": manager}


def _texts(outbox, phone):
    return [body for to, body in outbox if to == phone]


# --- the account holder ------------------------------------------------------


def test_switching_two_step_off_texts_the_account_holder(outbox, django_capture_on_commit_callbacks):
    owner = make_org()
    secret, _codes = enable(owner.user)
    with django_capture_on_commit_callbacks(execute=True):
        mfa.disable(owner.user, password=PASSWORD, code=code(secret))
    [text] = _texts(outbox, owner.user.phone)
    assert "Two-step login was switched off" in text and "If this wasn't you" in text


def test_a_recovery_code_says_how_many_are_left(outbox, django_capture_on_commit_callbacks):
    owner = make_org()
    _secret, codes = enable(owner.user)
    with django_capture_on_commit_callbacks(execute=True):
        assert mfa.check(owner.user, codes[0]) == "recovery"
    [text] = _texts(outbox, owner.user.phone)
    assert "A recovery code was used" in text and f"{len(codes) - 1} recovery codes are left" in text


def test_a_failed_send_never_undoes_the_change(monkeypatch, django_capture_on_commit_callbacks):
    owner = make_org()
    secret, _codes = enable(owner.user)

    def broken(*args, **kwargs):
        raise ConnectionError("SMS provider down")

    monkeypatch.setattr(alerts, "send_sms", broken)
    with django_capture_on_commit_callbacks(execute=True):
        mfa.disable(owner.user, password=PASSWORD, code=code(secret))
    assert not mfa.is_enabled(owner.user)


# --- the organization's managers --------------------------------------------


def test_a_role_change_goes_to_the_other_owners_inbox_only(org, outbox, django_capture_on_commit_callbacks):
    owner, partner, manager = org["owner"], org["partner"], org["manager"]
    with django_capture_on_commit_callbacks(execute=True):
        services.change_member_role(owner, manager, role(owner.organization, "accountant"))
    inbox = Message.objects.filter(type="security_change")
    assert [m.user for m in inbox] == [partner.user]  # not the one who did it, not staff who cannot manage
    assert f"Security: {owner.user} changed the role of" in inbox[0].body
    assert outbox == []  # roles are inbox only


def test_where_money_goes_is_also_texted_to_every_manager(org, outbox, django_capture_on_commit_callbacks):
    owner, partner, manager = org["owner"], org["partner"], org["manager"]
    from mpesa import services as mpesa_services

    with django_capture_on_commit_callbacks(execute=True):
        mpesa_services.add_account(owner, type="PAYBILL", number="400200", display_name="Rent Paybill")
    [to_partner] = _texts(outbox, partner.user.phone)
    assert to_partner.startswith("Kilimani Homes on NyumbaPMS: ") and "added the payment account" in to_partner
    [to_owner] = _texts(outbox, owner.user.phone)  # the one who did it hears too, in case it was not them
    assert ": you added the payment account" in to_owner
    assert _texts(outbox, manager.user.phone) == []


def test_an_api_key_and_a_webhook_are_alerted(org, outbox, monkeypatch, django_capture_on_commit_callbacks):
    from api import webhooks

    monkeypatch.setattr(webhooks, "check_url", lambda url: None)  # no DNS in tests

    owner, partner = org["owner"], org["partner"]
    with django_capture_on_commit_callbacks(execute=True):
        api_services.create_key(owner, name="Accounting sync", scopes=["payments"])
        webhooks.create_endpoint(owner, url="https://hooks.example.com/rent", events=["payment.confirmed"])
    bodies = list(Message.objects.filter(type="security_change", user=partner.user).values_list("body", flat=True))
    assert any("created the API key" in b for b in bodies) and any("added a webhook" in b for b in bodies)
    assert len(_texts(outbox, partner.user.phone)) == 2


def test_the_alert_cannot_be_switched_off():
    from notifications import catalog

    ntype = catalog.get("security_change")
    assert ntype.mandatory and ntype.urgent


def test_an_event_is_alerted_once(org, django_capture_on_commit_callbacks):
    owner, manager = org["owner"], org["manager"]
    with django_capture_on_commit_callbacks(execute=True):
        event = audit.record("membership.override", actor=owner.user, organization=owner.organization, obj=manager)
    assert alerts.send(event.pk) == 0  # already sent after commit


# --- the operators -------------------------------------------------------------


def test_failed_sign_ins_are_counted_across_the_platform(settings):
    owner = make_org()
    for _ in range(3):
        authenticate(None, username=owner.user.phone, password="wrong")
    hour = timezone.localtime().replace(minute=0, second=0, microsecond=0)
    assert watch.seen("signin", hour) == 3


def test_a_wave_of_failures_alerts_the_operators_once_an_hour(settings, monkeypatch):
    settings.SECURITY_ALERT_SIGNIN_HOUR = 2
    settings.OPS_ALERT_EMAILS = "ops@example.com"
    monkeypatch.setattr(ops_alerts.jobs, "problems", lambda now=None: [])
    monkeypatch.setattr(ops_alerts.health, "checks", lambda: {})
    monkeypatch.setattr(ops_alerts.dbhealth, "problems", lambda: [])
    now = timezone.now()
    for _ in range(3):
        watch.count("signin", now)
    assert ops_alerts.check_and_alert(now) == "alert"
    assert "more than 2 failed sign-ins in the hour from" in mail.outbox[-1].subject
    watch.count("signin", now)  # still going: the line is the same, so no new email
    assert ops_alerts.check_and_alert(now + datetime.timedelta(minutes=10)) == "quiet"
    assert ops_alerts.check_and_alert(now + datetime.timedelta(hours=2, minutes=1)) == "all clear"


def test_wrong_two_step_codes_are_counted(django_capture_on_commit_callbacks):
    owner = make_org()
    enable(owner.user)
    mfa.check(owner.user, "000000")
    hour = timezone.localtime().replace(minute=0, second=0, microsecond=0)
    assert watch.seen("two_step", hour) == 1
