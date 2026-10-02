"""Status and security pages (D-061), help pages and support requests (D-063)."""

import datetime

import pytest
from django.core import mail
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone

from accounts.models import Organization
from accounts.tests.factories import make_org, make_user
from accounts.tests.test_isolation import login
from core.models import JobRun
from support import help as help_topics
from support import services, status
from support.models import Incident, IncidentUpdate, SupportRequest

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _media(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path


# --- status and security -----------------------------------------------------

def test_status_page_is_public_and_shows_incidents(client):
    make_org(name="Secret Estates Ltd")
    incident = Incident.objects.create(title="M-Pesa payments delayed", impact=Incident.Impact.MAJOR)
    IncidentUpdate.objects.create(incident=incident, status=Incident.Status.INVESTIGATING, text="Looking into it.")
    Incident.objects.create(title="Hidden draft", is_published=False)
    response = client.get(reverse("support:status"))
    assert response.status_code == 200
    body = response.content.decode()
    assert "M-Pesa payments delayed" in body and "Looking into it." in body
    assert "Hidden draft" not in body
    assert "Secret Estates" not in body
    assert response.context["overall"] == status.DEGRADED


def test_status_overall():
    now = timezone.now()
    for name in ("send_due_messages", "billing_daily", "mpesa_daily", "subscriptions_daily", "backup",
                 "purge_otp_codes", "purge_import_previews"):
        JobRun.objects.create(name=name, started_at=now, finished_at=now, ok=True)
    assert status.build(now)["overall"] == status.OK
    outage = Incident.objects.create(title="Down", impact=Incident.Impact.OUTAGE)
    assert status.build(now)["overall"] == status.DOWN
    outage.status = Incident.Status.RESOLVED
    outage.save()
    assert outage.resolved_at is not None
    data = status.build(now + datetime.timedelta(minutes=1))
    assert data["overall"] == status.OK and data["recent"] == [outage]
    assert status.build(now + datetime.timedelta(days=15))["recent"] == []


def test_a_late_job_degrades_without_details():
    data = status.build()
    assert data["overall"] == status.DEGRADED  # nothing has run in a fresh database
    assert dict(data["components"])["Database"] == status.OK


def test_status_is_cached():
    first = status.page()
    Incident.objects.create(title="New")
    assert status.page() is not None and status.page()["open"] == first["open"]
    cache.delete(status.CACHE_KEY)
    assert [i.title for i in status.page()["open"]] == ["New"]


def test_security_page_is_public(client, settings):
    settings.DATA_PROTECTION_EMAIL = "privacy@example.com"
    response = client.get(reverse("support:security"))
    assert response.status_code == 200
    assert b"privacy@example.com" in response.content


def test_login_page_links_status_and_security(client):
    body = client.get(reverse("accounts:login")).content.decode()
    assert reverse("support:status") in body and reverse("support:security") in body


# --- help pages --------------------------------------------------------------

def test_help_needs_login(client):
    assert client.get(reverse("support:help")).status_code == 302


def test_every_help_topic_renders(client):
    login(client, make_org())
    index = client.get(reverse("support:help"))
    assert index.status_code == 200
    for slug, title, _summary in help_topics.TOPICS:
        response = client.get(reverse("support:help_topic", args=[slug]))
        assert response.status_code == 200, slug
        assert str(title) in response.content.decode()
    assert client.get(reverse("support:help_topic", args=["no-such-topic"])).status_code == 404


# --- support requests --------------------------------------------------------

def test_a_member_sends_a_request(client, settings, django_capture_on_commit_callbacks):
    settings.SUPPORT_EMAIL = "help@example.com"
    owner = make_org()
    login(client, owner)
    upload = SimpleUploadedFile("My Units.xlsx", b"PK\x03\x04 spreadsheet", content_type="application/octet-stream")
    with_page = reverse("support:contact") + "?from=/tenants/"
    assert client.get(with_page).context["form"]["page"].value() == "/tenants/"
    with django_capture_on_commit_callbacks(execute=True):
        response = client.post(reverse("support:contact"), {
            "kind": SupportRequest.Kind.IMPORT_HELP, "subject": "Please import", "message": "Attached.",
            "attachment": upload, "page": "/tenants/"}, HTTP_USER_AGENT="TestBrowser/1.0")
    assert response.status_code == 302
    item = SupportRequest.objects.get()
    assert item.number == f"SUP-{timezone.localdate().year}-000001"
    assert item.organization == owner.organization and item.created_by == owner.user
    assert item.attachment_name == "My Units.xlsx" and item.attachment.name.startswith("support/")
    assert "My Units" not in item.attachment.name  # stored under a random name
    assert item.page == "/tenants/" and item.user_agent == "TestBrowser/1.0"
    assert len(mail.outbox) == 1 and item.number in mail.outbox[0].subject
    assert mail.outbox[0].to == ["help@example.com"]


def test_a_foreign_page_is_not_recorded(client):
    login(client, make_org())
    response = client.get(reverse("support:contact") + "?from=https://evil.example.com/x")
    assert response.context["form"]["page"].value() == ""


@pytest.mark.parametrize("name,size,ok", [("a.exe", 10, False), ("big.pdf", 10 * 1024 * 1024 + 1, False),
                                          ("photo.JPG", 10, True)])
def test_attachment_rules(client, name, size, ok):
    login(client, make_org())
    upload = SimpleUploadedFile(name, b"x" * size)
    response = client.post(reverse("support:contact"), {
        "kind": "QUESTION", "subject": "Hi", "message": "Hello", "attachment": upload})
    assert (response.status_code == 302) is ok
    assert SupportRequest.objects.exists() is ok


def test_a_frozen_organization_can_still_ask_for_help(client):
    owner = make_org()
    Organization.objects.filter(pk=owner.organization_id).update(status=Organization.Status.FROZEN)
    login(client, owner)
    response = client.post(reverse("support:contact"), {"kind": "QUESTION", "subject": "Frozen?", "message": "Why"})
    assert response.status_code == 302
    assert SupportRequest.objects.filter(organization=owner.organization).exists()


def test_someone_without_an_organization_is_sent_on(client):
    user = make_user()
    client.force_login(user)
    response = client.get(reverse("support:contact"))
    assert response.status_code == 302 and SupportRequest.objects.count() == 0


def test_numbers_follow_on():
    owner = make_org()
    first = services.create_request(owner.user, owner.organization, kind="QUESTION", subject="a", message="b")
    second = services.create_request(owner.user, owner.organization, kind="QUESTION", subject="c", message="d")
    assert first.number.endswith("000001") and second.number.endswith("000002")


def test_admin_downloads_the_attachment_and_closes(client, monkeypatch):
    from accounts import mfa

    owner = make_org()
    item = services.create_request(owner.user, owner.organization, kind="PROBLEM", subject="Broken", message="x",
                                   attachment=SimpleUploadedFile("log.txt", b"hello"))
    admin = make_user(is_staff=True, is_superuser=True)
    monkeypatch.setattr(mfa, "is_enabled", lambda user: True)
    monkeypatch.setattr(mfa, "session_verified", lambda request: True)
    client.force_login(admin)
    response = client.get(reverse("admin:support_request_attachment", args=[item.pk]))
    assert response.status_code == 200
    assert b"".join(response.streaming_content) == b"hello"
    assert 'filename="log.txt"' in response["Content-Disposition"]
    change = reverse("admin:support_supportrequest_change", args=[item.pk])
    assert client.get(change).status_code == 200
    response = client.post(change, {"status": "CLOSED", "internal_note": "Done"})
    assert response.status_code == 302
    item.refresh_from_db()
    assert item.closed_at is not None and item.internal_note == "Done"
