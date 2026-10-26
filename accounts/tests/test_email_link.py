"""Confirming an email from the link in the message (D-223)."""

import re

import pytest
from django.core import mail
from django.urls import reverse
from django.utils import timezone

from accounts import identity
from accounts.tests.factories import make_user

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _site(settings):
    settings.SITE_URL = "https://pms.example"


def test_the_verification_email_holds_the_code_and_the_link():
    user = make_user(email="a@example.com")
    identity.send_email_verification(user)
    body = mail.outbox[0].body
    assert re.search(r"\b\d{6}\b", body)
    assert f"https://pms.example/verify-email/{identity.email_link_token(user)}/" in body


def test_opening_the_link_confirms_nothing_until_the_button_is_pressed(client):
    user = make_user(email="a@example.com")
    url = reverse("accounts:email_link", args=[identity.email_link_token(user)])
    assert client.get(url).status_code == 200
    user.refresh_from_db()
    assert user.email_verified_at is None
    assert client.post(url).status_code == 200
    user.refresh_from_db()
    assert user.email_verified_at is not None


def test_a_link_dies_when_the_address_changes(client):
    user = make_user(email="a@example.com")
    url = reverse("accounts:email_link", args=[identity.email_link_token(user)])
    user.email = "b@example.com"
    user.save()
    assert client.post(url).status_code == 400
    user.refresh_from_db()
    assert user.email_verified_at is None


def test_an_expired_or_forged_link_is_refused(client, monkeypatch):
    user = make_user(email="a@example.com")
    assert client.post(reverse("accounts:email_link", args=["forged"])).status_code == 400
    token = identity.email_link_token(user)
    monkeypatch.setattr(identity, "EMAIL_LINK_MAX_AGE", -1)
    assert client.post(reverse("accounts:email_link", args=[token])).status_code == 400


def test_a_confirmed_address_shows_done_on_a_second_visit(client):
    user = make_user(email="a@example.com", email_verified_at=timezone.now())
    r = client.get(reverse("accounts:email_link", args=[identity.email_link_token(user)]))
    assert r.status_code == 200 and b"is confirmed" in r.content
