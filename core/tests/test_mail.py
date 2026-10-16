import pytest
from django.test import override_settings

from accounts import mfa
from accounts.tests.factories import make_user
from core.mail import MESSAGES, SYSTEM, mailer_for
from core.models import EmailRouting

BOTH = {"default": {"BACKEND": "django.core.mail.backends.locmem.EmailBackend"},
        "messages": {"BACKEND": "django.core.mail.backends.locmem.EmailBackend"}}
ONE = {"default": BOTH["default"]}


@pytest.mark.django_db
def test_defaults_keep_resend_for_system_and_brevo_for_messages():
    with override_settings(MAILERS=BOTH):
        assert mailer_for(SYSTEM) == "default"
        assert mailer_for(MESSAGES) == "messages"


@pytest.mark.django_db
def test_admin_choice_swaps_the_roles():
    EmailRouting.objects.update_or_create(pk=1, defaults={"system_account": "messages", "messages_account": "default"})
    with override_settings(MAILERS=BOTH):
        assert mailer_for(SYSTEM) == "messages"
        assert mailer_for(MESSAGES) == "default"


@pytest.mark.django_db
def test_an_account_not_set_up_falls_back_to_default():
    EmailRouting.objects.update_or_create(pk=1, defaults={"system_account": "messages"})
    with override_settings(MAILERS=ONE):
        assert mailer_for(SYSTEM) == "default"
        assert mailer_for(MESSAGES) == "default"


@pytest.mark.django_db
def test_admin_page_saves_and_records(client, monkeypatch):
    monkeypatch.setattr(mfa, "is_enabled", lambda user: True)
    monkeypatch.setattr(mfa, "session_verified", lambda request: True)
    client.force_login(make_user(is_staff=True, is_superuser=True))
    admin_client = client
    admin_client.get("/ok-ml-staff/core/emailrouting/")
    r = admin_client.post("/ok-ml-staff/core/emailrouting/1/change/",
                          {"system_account": "messages", "messages_account": "default"})
    assert r.status_code == 302
    assert EmailRouting.get().system_account == "messages"
