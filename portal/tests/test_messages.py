"""Messages with the office, the tenant's lease page and renewal offers in the portal (D-131)."""

import datetime

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.utils import timezone

from accounts.tests.factories import add_member, make_org, make_property
from leases import services as lease_services
from leases.models import Lease
from notifications.models import Message
from portal import conversation, offers
from portal.models import PortalMessage, RenewalOffer
from portal.tests.test_portal import join
from reports.tests.test_income import make_lease

pytestmark = pytest.mark.django_db

D = datetime.date


@pytest.fixture
def owner():
    return make_org()


@pytest.fixture
def acacia(owner):
    return make_property(owner.organization, name="Acacia Court")


@pytest.fixture
def a1(owner, acacia):
    return make_lease(owner, acacia, code="A1")


@pytest.fixture
def b7(owner, acacia):
    return make_lease(owner, acacia, code="B7")


def sms_to_tenant(type_):
    return Message.objects.filter(type=type_, channel="SMS")


# ---------------------------------------------------------------------------
# The thread
# ---------------------------------------------------------------------------


def test_a_tenant_writes_and_the_office_hears_and_replies(client, owner, a1, django_capture_on_commit_callbacks):
    user = join(owner, a1)
    client.force_login(user)
    with django_capture_on_commit_callbacks(execute=True):
        response = client.post(f"/my/leases/{a1.public_id}/messages/", {"body": "The gate lock is stiff."})
    assert response.status_code == 302
    note = PortalMessage.objects.get()
    assert note.side == "TENANT" and note.tenant == a1.primary_tenant
    assert Message.objects.filter(type="portal_message_received", user=owner.user).exists()

    client.force_login(owner.user)
    page = client.get(f"/conversations/leases/{a1.public_id}/").content.decode()
    assert "The gate lock is stiff." in page
    note.refresh_from_db()
    assert note.read_at is not None

    with django_capture_on_commit_callbacks(execute=True):
        response = client.post(f"/conversations/leases/{a1.public_id}/", {"body": "We will send a locksmith."})
    assert response.status_code == 302
    assert sms_to_tenant("portal_message_reply").filter(tenant=a1.primary_tenant).exists()

    client.force_login(user)
    page = client.get(f"/my/leases/{a1.public_id}/messages/").content.decode()
    assert "We will send a locksmith." in page
    assert PortalMessage.objects.get(side="STAFF").read_at is not None


def test_the_inbox_lists_threads_with_the_unread_first(client, owner, a1, b7):
    for lease, text in ((a1, "first"), (b7, "second")):
        PortalMessage.objects.create(organization=owner.organization, lease=lease, tenant=lease.primary_tenant,
                                     side="TENANT", author=join(owner, lease), body=text)
    conversation.mark_read_by_staff(a1)
    client.force_login(owner.user)
    page = client.get("/conversations/").content.decode()
    assert page.index("· B7") < page.index("· A1")  # not bare codes: a random token in the page can contain "A1"
    assert conversation.staff_unread(owner) == 1


def test_a_tenant_cannot_read_or_write_on_a_neighbours_lease(client, owner, a1, b7):
    join(owner, b7)
    stranger = join(owner, a1)
    client.force_login(stranger)
    assert client.get(f"/my/leases/{b7.public_id}/messages/").status_code == 404
    assert client.post(f"/my/leases/{b7.public_id}/messages/", {"body": "hi"}).status_code == 404
    assert not PortalMessage.objects.exists()
    with pytest.raises(PermissionDenied):
        conversation.tenant_send(stranger, b7, "hi")


def test_empty_and_overlong_notes_are_refused(client, owner, a1):
    client.force_login(join(owner, a1))
    assert client.post(f"/my/leases/{a1.public_id}/messages/", {"body": "   "}).status_code == 400
    assert client.post(f"/my/leases/{a1.public_id}/messages/", {"body": "x" * 1001}).status_code == 400
    assert not PortalMessage.objects.exists()


def test_the_notes_are_limited_each_hour(owner, a1):
    user = join(owner, a1)
    for n in range(conversation.PER_HOUR):
        conversation.tenant_send(user, a1, f"note {n}")
    with pytest.raises(ValidationError):
        conversation.tenant_send(user, a1, "one too many")


def test_staff_without_the_right_cannot_reply_and_others_cannot_see_the_thread(client, owner, acacia, a1):
    PortalMessage.objects.create(organization=owner.organization, lease=a1, tenant=a1.primary_tenant,
                                 side="TENANT", author=join(owner, a1), body="hello")
    viewer = add_member(owner.organization, "viewer", all_properties=True)
    client.force_login(viewer.user)
    assert client.get(f"/conversations/leases/{a1.public_id}/").status_code == 403
    assert client.post(f"/conversations/leases/{a1.public_id}/", {"body": "no"}).status_code == 403
    with pytest.raises(PermissionDenied):
        conversation.staff_reply(viewer, a1, "no")
    assert PortalMessage.objects.filter(side="STAFF").count() == 0
    outsider = make_org()
    client.force_login(outsider.user)
    assert client.get(f"/conversations/leases/{a1.public_id}/").status_code == 404


def test_the_tenant_sees_announcements_the_office_sent(client, owner, a1):
    Message.objects.create(organization=owner.organization, type="tenant_message", channel="SMS",
                           tenant=a1.primary_tenant, body="Water off Tuesday 9 to 12.", status="SENT")
    Message.objects.create(organization=owner.organization, type="invoice_issued", channel="SMS",
                           tenant=a1.primary_tenant, body="Invoice INV-1 is due.", status="SENT")
    client.force_login(join(owner, a1))
    page = client.get(f"/my/leases/{a1.public_id}/messages/").content.decode()
    assert "Water off Tuesday" in page
    assert "Invoice INV-1" not in page


# ---------------------------------------------------------------------------
# The lease terms
# ---------------------------------------------------------------------------


def test_the_lease_page_shows_the_terms_and_the_agreement_pdf(client, owner, a1):
    client.force_login(join(owner, a1))
    page = client.get(f"/my/leases/{a1.public_id}/").content.decode()
    assert "Your lease" in page and "Agreement (PDF)" in page
    pdf = client.get(f"/my/leases/{a1.public_id}/agreement/")
    assert pdf.status_code == 200 and pdf["Content-Type"] == "application/pdf"
    assert pdf.content.startswith(b"%PDF")


def test_the_agreement_pdf_is_only_for_the_own_lease(client, owner, a1, b7):
    join(owner, b7)
    client.force_login(join(owner, a1))
    assert client.get(f"/my/leases/{b7.public_id}/agreement/").status_code == 404


# ---------------------------------------------------------------------------
# Renewal offers
# ---------------------------------------------------------------------------


def offer_for(owner, lease, **kw):
    kw.setdefault("start_date", D(2026, 1, 1))
    kw.setdefault("end_date", D(2026, 12, 31))
    kw.setdefault("rent", 22000)
    return offers.make_offer(owner, lease, **kw)


def test_the_office_offers_and_the_tenant_accepts_and_the_office_drafts(client, owner, a1,
                                                                         django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        offer = offer_for(owner, a1, note="Thanks for being a good tenant.")
    assert sms_to_tenant("renewal_offered").filter(tenant=a1.primary_tenant).exists()
    assert offer.expires_on == timezone.localdate() + datetime.timedelta(days=14)

    user = join(owner, a1)
    client.force_login(user)
    page = client.get(f"/my/leases/{a1.public_id}/").content.decode()
    assert "Renewal offer" in page and "Thanks for being a good tenant." in page
    with django_capture_on_commit_callbacks(execute=True):
        client.post(f"/my/offers/{offer.public_id}/answer/", {"answer": "accept"})
    offer.refresh_from_db()
    assert offer.status == "ACCEPTED" and offer.responded_by == user
    assert Message.objects.filter(type="renewal_answered", user=owner.user).exists()
    # Nothing changes on the lease until the office draft.
    assert Lease.all_objects.filter(previous_lease=a1).count() == 0

    draft = offers.start_draft(owner, offer)
    offer.refresh_from_db()
    assert draft.previous_lease == a1 and draft.start_date == D(2026, 1, 1) and offer.successor == draft
    assert draft.rent_on(D(2026, 1, 1)) == 22000
    with pytest.raises(ValidationError):
        offers.start_draft(owner, offer)


def test_declining_closes_the_offer(client, owner, a1):
    offer = offer_for(owner, a1)
    client.force_login(join(owner, a1))
    client.post(f"/my/offers/{offer.public_id}/answer/", {"answer": "decline"})
    offer.refresh_from_db()
    assert offer.status == "DECLINED"
    with pytest.raises(ValidationError):
        offers.start_draft(owner, offer)
    assert offers.open_offer(a1) is None


def test_only_one_open_offer_and_it_can_be_withdrawn(owner, a1):
    offer = offer_for(owner, a1)
    with pytest.raises(ValidationError):
        offer_for(owner, a1)
    offers.withdraw(owner, offer)
    assert offers.open_offer(a1) is None
    offer_for(owner, a1)


def test_an_expired_offer_cannot_be_answered(client, owner, a1):
    offer = offer_for(owner, a1)
    RenewalOffer.objects.filter(pk=offer.pk).update(expires_on=timezone.localdate() - datetime.timedelta(days=1))
    user = join(owner, a1)
    with pytest.raises(ValidationError):
        offers.answer(user, RenewalOffer.objects.get(pk=offer.pk), accept=True)
    client.force_login(user)
    assert "Renewal offer" not in client.get(f"/my/leases/{a1.public_id}/").content.decode()


def test_offer_checks(owner, a1):
    with pytest.raises(ValidationError):
        offer_for(owner, a1, rent=0)
    with pytest.raises(ValidationError):
        offer_for(owner, a1, start_date=D(2026, 6, 1), end_date=D(2026, 5, 1))
    with pytest.raises(ValidationError):
        offer_for(owner, a1, expires_on=timezone.localdate() - datetime.timedelta(days=1))
    # No end date on the lease and no start given: the office must say when it starts.
    with pytest.raises(ValidationError):
        offers.make_offer(owner, a1, start_date=None, end_date=None, rent=20000)


def test_no_offer_while_notice_is_open(owner, a1):
    lease_services.give_notice(owner, a1, given_on=timezone.localdate(), via="IN_PERSON")
    with pytest.raises(ValidationError):
        offer_for(owner, a1)


def test_a_neighbour_cannot_answer_my_offer(client, owner, a1, b7):
    offer = offer_for(owner, a1)
    client.force_login(join(owner, b7))
    assert client.post(f"/my/offers/{offer.public_id}/answer/", {"answer": "accept"}).status_code == 404
    offer.refresh_from_db()
    assert offer.status == "OFFERED"


def test_a_member_without_lease_rights_cannot_offer(client, owner, a1):
    viewer = add_member(owner.organization, "viewer", all_properties=True)
    with pytest.raises(PermissionDenied):
        offers.make_offer(viewer, a1, start_date=D(2026, 1, 1), end_date=None, rent=20000)
    client.force_login(viewer.user)
    response = client.post(f"/conversations/leases/{a1.public_id}/offer/", {"rent": "20000"})
    assert response.status_code == 403


def test_the_staff_lease_page_shows_the_offer_card(client, owner, a1):
    client.force_login(owner.user)
    page = client.get(f"/leases/{a1.public_id}/").content.decode()
    assert "Offer a renewal in the portal" in page
    offer_for(owner, a1)
    page = client.get(f"/leases/{a1.public_id}/").content.decode()
    assert "Withdraw offer" in page and "Offer a renewal in the portal" not in page
