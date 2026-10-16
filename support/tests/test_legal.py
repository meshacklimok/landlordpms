"""The Terms of Service and Privacy Policy, and who accepted which version (D-095)."""

import pytest
from django.urls import reverse

from accounts.models import User
from accounts.tests.factories import PASSWORD, make_user
from audit.models import AuditEvent
from support import legal

pytestmark = pytest.mark.django_db


def test_both_pages_are_public_and_say_they_are_drafts(client, settings):
    settings.LEGAL_NAME = "Nyumba Systems Ltd"
    settings.DATA_PROTECTION_EMAIL = "privacy@example.com"
    for name, title in (("support:terms", "Terms of Service"), ("support:privacy", "Privacy Policy")):
        page = client.get(reverse(name)).content.decode()
        assert title in page and f"Effective {legal.VERSION}" in page and "Nyumba Systems Ltd" in page
        assert "data-legal-draft" in page
    assert "privacy@example.com" in client.get(reverse("support:privacy")).content.decode()
    assert "We never hold your money" in client.get(reverse("support:terms")).content.decode()
    settings.LEGAL_REGISTRATION = "PVT-ABC123"
    settings.LEGAL_ADDRESS = "P.O. Box 1, Nairobi"
    settings.ODPC_REGISTRATION = "ODPC/123"
    terms = client.get(reverse("support:terms")).content.decode()
    assert "PVT-ABC123" in terms and "P.O. Box 1, Nairobi" in terms and "Arbitration Act, 1995" in terms
    privacy = client.get(reverse("support:privacy")).content.decode()
    assert "ODPC/123" in privacy and "within 48 hours" in privacy and "730" in privacy
    settings.LEGAL_REVIEWED = True
    assert "data-legal-draft" not in client.get(reverse("support:terms")).content.decode()


def test_the_footer_and_the_sign_up_form_link_to_them(client):
    page = client.get(reverse("accounts:register")).content.decode()
    assert reverse("support:terms") in page and reverse("support:privacy") in page
    assert 'href="/terms/"' in client.get(reverse("support:status")).content.decode()


def test_sign_up_records_the_version(client):
    client.post(reverse("accounts:register"), {"full_name": "Wanjiku", "phone": "0712345678",
                                               "email": "w@example.com", "password": PASSWORD, "accept_terms": "on"})
    user = User.objects.get()
    assert user.terms_version == legal.VERSION and user.terms_accepted_at is not None
    event = AuditEvent.objects.get(action="user.register")
    assert event.changes["terms_version"] == [None, legal.VERSION]
    client.force_login(user)
    assert "data-terms-notice" not in client.get(reverse("support:terms")).content.decode()


def test_sign_up_needs_the_box_ticked(client):
    client.post(reverse("accounts:register"), {"full_name": "Wanjiku", "phone": "0712345678",
                                               "email": "w@example.com", "password": PASSWORD})
    assert not User.objects.exists()


def test_a_new_version_is_shown_until_accepted(client):
    user = make_user()
    user.terms_version = "2020-01-01"
    user.save()
    client.force_login(user)
    page = client.get(reverse("support:status")).content.decode()
    assert "data-terms-notice" in page and "We have updated our Terms of Service and Privacy Policy" in page
    response = client.post(reverse("support:accept_terms"), {"next": "/status/"})
    assert response["Location"] == "/status/"
    user.refresh_from_db()
    assert user.terms_version == legal.VERSION
    assert AuditEvent.objects.get(action="user.accept_terms").changes["terms_version"] == ["2020-01-01",
                                                                                         legal.VERSION]
    assert "data-terms-notice" not in client.get(reverse("support:status")).content.decode()
    # Accepting again changes nothing.
    client.post(reverse("support:accept_terms"))
    assert AuditEvent.objects.filter(action="user.accept_terms").count() == 1


def test_accepting_needs_a_login_and_only_goes_back_to_this_site(client):
    response = client.post(reverse("support:accept_terms"))
    assert response.status_code == 302 and "/login/" in response["Location"]
    client.force_login(make_user())
    assert client.post(reverse("support:accept_terms"), {"next": "https://evil.example.com/"})["Location"] == "/"


def test_no_notice_for_visitors(client):
    assert "data-terms-notice" not in client.get(reverse("support:terms")).content.decode()
