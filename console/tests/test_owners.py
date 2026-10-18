"""The owners page, its CSV and the email to owners (D-179). Superusers only."""

import pytest
from django.core import mail
from django.urls import reverse

from accounts.tests.factories import add_member, make_org, make_property
from audit.models import AuditEvent
from console.models import OwnerEmail
from properties.models import Unit

from .test_console import owner, seat_for, signed_in

pytestmark = pytest.mark.django_db


@pytest.fixture
def two_owners(settings):
    settings.MAILERS = {"default": {"BACKEND": "django.core.mail.backends.locmem.EmailBackend"}}
    big, small = make_org("Big Lets"), make_org("Small Lets")
    big.user.email, big.user.full_name = "big@example.com", "Wanjiku Kamau"
    big.user.save(update_fields=["email", "full_name"])
    small.user.full_name = "Otieno Ouma"
    small.user.save(update_fields=["full_name"])
    prop = make_property(big.organization)
    for n in range(3):
        Unit.objects.create(organization=big.organization, property=prop, code=f"A{n}", payment_reference=f"REF{n}")
    add_member(big.organization, "caretaker")
    return big, small


def test_lists_owners_with_email_team_and_units(client, two_owners):
    owner(client)
    html = client.get(reverse("console:owners")).content.decode()
    assert "big@example.com" in html and "Wanjiku Kamau" in html and "Otieno Ouma" in html
    rows = client.get(reverse("console:owners")).context["page"]
    big = next(r for r in rows if r["org"].name == "Big Lets")
    assert (big["units"], big["team"]) == (3, 1)


def test_filters_narrow_the_list(client, two_owners):
    owner(client)
    page = client.get(reverse("console:owners"), {"email": "has"}).context["page"]
    assert [r["org"].name for r in page] == ["Big Lets"]
    page = client.get(reverse("console:owners"), {"q": "otieno"}).context["page"]
    assert [r["org"].name for r in page] == ["Small Lets"]


def test_csv_download_is_audited(client, two_owners):
    owner(client)
    r = client.get(reverse("console:owners_export"))
    assert r["Content-Type"].startswith("text/csv") and b"big@example.com" in r.content
    assert AuditEvent.objects.filter(action="console.export").exists()


@pytest.mark.parametrize("name", ["owners", "owner_email", "owner_emails", "owners_export"])
def test_console_team_without_superuser_cannot_open(client, name):
    seat = seat_for()
    signed_in(client, seat.user)
    assert client.get(reverse(f"console:{name}")).status_code == 403


def test_a_landlord_gets_404(client, two_owners):
    signed_in(client, two_owners[0].user)
    assert client.get(reverse("console:owners")).status_code == 404


def test_sends_one_email_each_and_keeps_the_record(client, two_owners):
    me = owner(client)
    big, small = two_owners
    small.user.email = "small@example.com"
    small.user.save(update_fields=["email"])
    r = client.post(reverse("console:owner_email"), {"subject": "Hello {name}", "body": "About {organization}.",
                                                       "checked": "on"})
    log = OwnerEmail.objects.get()
    assert r.status_code == 302 and (log.sent, log.failed, log.recipients) == (2, 0, 2)
    assert sorted(m.to[0] for m in mail.outbox) == ["big@example.com", "small@example.com"]
    big_mail = next(m for m in mail.outbox if m.to == ["big@example.com"])
    assert big_mail.subject == "Hello Wanjiku" and "About Big Lets." in big_mail.body
    assert AuditEvent.objects.filter(action="console.owner_email").exists()
    assert me.get(reverse("console:owner_email_detail", args=[log.pk])).status_code == 200


def test_sending_needs_the_tick_and_skips_owners_without_email(client, two_owners):
    owner(client)
    r = client.post(reverse("console:owner_email"), {"subject": "x", "body": "y"})
    assert r.status_code == 200 and not mail.outbox
    client.post(reverse("console:owner_email"), {"subject": "x", "body": "y", "checked": "on"})
    assert [m.to for m in mail.outbox] == [["big@example.com"]]


def test_only_the_ticked_owners_get_it(client, two_owners):
    owner(client)
    big, small = two_owners
    small.user.email = "small@example.com"
    small.user.save(update_fields=["email"])
    client.post(reverse("console:owner_email"), {"subject": "x", "body": "y", "checked": "on",
                                                   "m": [str(small.public_id)]})
    assert [m.to for m in mail.outbox] == [["small@example.com"]]


def test_test_goes_only_to_the_sender(client, two_owners):
    me = make_org().user
    me.is_superuser, me.is_staff, me.email = True, True, "me@example.com"
    me.save(update_fields=["is_superuser", "is_staff", "email"])
    signed_in(client, me)
    client.post(reverse("console:owner_email"), {"subject": "x", "body": "y", "test": "1"})
    assert [m.to for m in mail.outbox] == [["me@example.com"]] and not OwnerEmail.objects.exists()
