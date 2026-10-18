"""The guided Paybill setup: steps, key check, save and connect, properties, WhatsApp help (D-174)."""

import pytest
from django.urls import reverse

from accounts.tests.factories import make_org
from accounts.tests.test_isolation import login
from mpesa import services, setup
from mpesa.daraja import FakeDarajaClient
from mpesa.models import DarajaCredentials
from payments.models import PropertyPaymentAccount
from properties.models import Property

pytestmark = pytest.mark.django_db


@pytest.fixture
def owner():
    return make_org(name="Acme")


@pytest.fixture
def paybill(owner):
    return services.add_account(owner, type="PAYBILL", number="600123", display_name="Rent")


def test_friendly_errors():
    assert "consumer key" in setup.friendly_error("400 Invalid Access Token")
    assert "reach Safaricom" in setup.friendly_error("Could not reach Daraja: timed out")
    assert setup.friendly_error("something odd") == "something odd"


def test_help_link_is_prefilled(settings, owner, paybill):
    settings.SUPPORT_WHATSAPP = "+254 700 000 000"
    url = setup.help_link(organization=owner.organization, user=owner.user, account=paybill, step="3/7 Go-Live")
    assert url.startswith("https://wa.me/254700000000?text=") and "600123" in url and "Acme" in url
    settings.SUPPORT_WHATSAPP = ""
    assert setup.help_link(organization=owner.organization, user=owner.user) == ""


def test_steps_follow_the_data(owner, paybill):
    assert setup.progress(paybill, None)["current"]["key"] == "daraja"
    creds = services.save_credentials(owner, paybill, environment="PRODUCTION", consumer_key="k", consumer_secret="s")
    assert setup.progress(paybill, creds)["current"]["key"] == "connect"


def test_save_and_connect(client, owner, paybill):
    FakeDarajaClient.calls = []
    login(client, owner)
    url = reverse("mpesa:account", args=[paybill.public_id])
    r = client.post(url, {"action": "save_connect", "environment": "PRODUCTION", "consumer_key": "k",
                          "consumer_secret": "s"})
    assert r.status_code == 302
    assert DarajaCredentials.objects.get(payment_account=paybill).urls_registered_at
    page = client.get(url).content.decode()
    assert "Setup steps" in page and "Get help on WhatsApp" in page or "Message us" in page


def test_properties_are_chosen_on_the_account_page(client, owner, paybill):
    prop = Property.objects.create(organization=owner.organization, name="Riverside", code="RS")
    login(client, owner)
    url = reverse("mpesa:account", args=[paybill.public_id])
    client.post(url, {"action": "properties", "property": [str(prop.public_id)]})
    link = PropertyPaymentAccount.objects.get(property=prop, payment_account=paybill)
    assert link.is_default
    client.post(url, {"action": "properties"})
    assert not PropertyPaymentAccount.objects.filter(payment_account=paybill).exists()


def test_stalled_setup_reminder(owner, paybill):
    import datetime

    from django.utils import timezone

    from notifications.models import Message

    now = timezone.now()
    assert setup.send_reminders(now) == 0  # too new
    later = now + datetime.timedelta(days=4)
    assert setup.send_reminders(later) == 1
    assert setup.send_reminders(later) == 0  # same week: once
    msg = Message.objects.get(type="mpesa_setup_stalled")
    assert "600123" in msg.body and "Daraja" in msg.body
    assert setup.send_reminders(now + datetime.timedelta(days=8)) == 1  # next week
    services.save_credentials(owner, paybill, environment="PRODUCTION", consumer_key="k", consumer_secret="s")
    services.register_urls(owner, paybill.daraja)
    assert setup.send_reminders(now + datetime.timedelta(days=15)) == 0  # connected: no more


def test_waiting_for_safaricom(client, owner, paybill):
    login(client, owner)
    url = reverse("mpesa:account", args=[paybill.public_id])
    assert "I have applied, waiting for Safaricom" in client.get(url).content.decode()
    client.post(url, {"action": "applied"})
    paybill.refresh_from_db()
    assert paybill.safaricom_applied_at
    page = client.get(url).content.decode()
    assert "Waiting for Safaricom to approve your app" in page and "You told us you applied" in page
    assert "Waiting for Safaricom" in client.get(reverse("mpesa:settings")).content.decode()
    client.post(url, {"action": "not_applied"})
    paybill.refresh_from_db()
    assert paybill.safaricom_applied_at is None


def test_video_embed_only_for_youtube(settings):
    settings.MPESA_SETUP_VIDEO_URL = ""
    assert setup.video_url() == "" and setup.video_embed() == ""
    settings.MPESA_SETUP_VIDEO_URL = "https://youtu.be/dQw4w9WgXcQ"
    assert setup.video_embed() == "https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ"
    settings.MPESA_SETUP_VIDEO_URL = "https://vimeo.com/123"
    assert setup.video_url() and setup.video_embed() == ""
    settings.MPESA_SETUP_VIDEO_URL = "http://youtu.be/dQw4w9WgXcQ"
    assert setup.video_url() == ""


def test_video_hidden_when_not_set(client, owner, paybill, settings):
    settings.MPESA_SETUP_VIDEO_URL = ""
    login(client, owner)
    page = client.get(reverse("mpesa:settings")).content.decode()
    assert "data-setup-video" not in page and "Watch the video" not in page and "unavailable" not in page
    assert "I don&#x27;t have a Paybill yet" in page or "I don't have a Paybill yet" in page
    settings.MPESA_SETUP_VIDEO_URL = "https://youtu.be/dQw4w9WgXcQ"
    assert "data-setup-video" in client.get(reverse("mpesa:settings")).content.decode()


def test_test_payment_step(client, owner, paybill):
    import datetime

    from django.utils import timezone

    from mpesa.models import MpesaTransaction

    creds = services.save_credentials(owner, paybill, environment="PRODUCTION", consumer_key="k", consumer_secret="s")
    services.register_urls(owner, creds)
    old = MpesaTransaction.objects.create(organization=owner.organization, payment_account=paybill,
                                          trans_id="OLD1", amount=10, paid_at=timezone.now())
    MpesaTransaction.objects.filter(pk=old.pk).update(
        created_at=creds.urls_registered_at - datetime.timedelta(days=1))
    step = [s for s in setup.steps(paybill, paybill.daraja) if s["key"] == "payment"][0]
    assert not step["done"]  # a payment from before connecting is not the test
    MpesaTransaction.objects.create(organization=owner.organization, payment_account=paybill, trans_id="NEW1",
                                    amount=10, paid_at=timezone.now())
    step = [s for s in setup.steps(paybill, paybill.daraja) if s["key"] == "payment"][0]
    assert step["done"] and step["test"].trans_id == "NEW1"


def test_video_link_set_in_admin(client, owner, paybill, settings):
    from mpesa.models import SetupVideo

    settings.MPESA_SETUP_VIDEO_URL = ""
    login(client, owner)
    assert "data-setup-video" not in client.get(reverse("mpesa:settings")).content.decode()
    SetupVideo.objects.update_or_create(pk=1, defaults={"url": "https://youtu.be/dQw4w9WgXcQ"})
    assert "data-setup-video" in client.get(reverse("mpesa:settings")).content.decode()
    SetupVideo.objects.filter(pk=1).update(url="")
    assert "data-setup-video" not in client.get(reverse("mpesa:settings")).content.decode()
