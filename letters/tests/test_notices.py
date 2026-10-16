"""Notices to vacate and demand letters (D-109): what applies, the facts, issuing, service, withdrawing and pages."""

import datetime

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone

from accounts.capabilities import ROLE_TEMPLATES
from accounts.tests.factories import add_member, make_org, make_property
from accounts.tests.test_isolation import login
from audit.models import AuditEvent
from billing.models import FollowUp
from billing.tests.test_invoicing import APR, FEB, JAN, MAR, bill, make_lease
from documents.models import Document
from leases import services as lease_services
from leases.models import LeaseNotice
from letters import notices
from letters.models import NoticeLetter
from payments import services as payment_services
from payments.models import Payment, PaymentAccount
from properties.models import Property

pytestmark = pytest.mark.django_db

D = datetime.date
Kind = NoticeLetter.Kind
PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"


@pytest.fixture(autouse=True)
def _media(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path


@pytest.fixture
def owner():
    return make_org()


@pytest.fixture
def prop(owner):
    return make_property(owner.organization)


def landlord_notice(owner, lease, **kw):
    today = timezone.localdate()
    return lease_services.give_notice(owner, lease, given_on=kw.pop("given_on", today),
                                      given_by=LeaseNotice.GivenBy.LANDLORD, **kw)


def in_arrears(owner, prop):
    """Jan to Apr billed, Jan paid: Feb, Mar and Apr are past their grace days."""
    lease = make_lease(owner, prop)
    for month in (JAN, FEB, MAR, APR):
        bill(lease, month)
    payment_services.record_payment(owner, lease, amount=15000, method=Payment.Method.CASH, paid_at=D(2026, 1, 2))
    return lease


# ---------------------------------------------------------------------------
# Notice to vacate
# ---------------------------------------------------------------------------


def test_a_vacate_letter_needs_an_open_notice_from_the_landlord(owner, prop):
    lease = make_lease(owner, prop)
    assert "Record the landlord's notice" in notices.vacate_problem(lease)
    lease_services.give_notice(owner, lease, given_on=timezone.localdate(), given_by=LeaseNotice.GivenBy.TENANT)
    assert "Record the landlord's notice" in notices.vacate_problem(lease)
    with pytest.raises(ValidationError):
        notices.issue_vacate(owner, lease)


def test_issuing_a_vacate_letter_freezes_the_notice(owner, prop):
    lease = make_lease(owner, prop)
    notice = landlord_notice(owner, lease, reason="Owner moving in")
    letter = notices.issue_vacate(owner, lease, note="Keys to the caretaker.")
    year = timezone.localdate().year
    assert letter.number == f"NTV-{year}-000001" and letter.kind == Kind.VACATE and letter.notice == notice
    assert letter.facts["move_out_on"] == notice.move_out_on.isoformat()
    assert letter.facts["notice_days"] == lease.notice_days and letter.facts["reason"] == "Owner moving in"
    assert letter.pdf.read().startswith(b"%PDF")
    text = " ".join(notices.paragraphs(Kind.VACATE, letter.facts))
    assert "hand over vacant possession" in text and "Keys to the caretaker." in text
    assert "does not take away any rights" in text
    assert AuditEvent.objects.filter(action="notice_letter.issue").exists()
    assert "already has a letter" in notices.vacate_problem(lease)
    with pytest.raises(ValidationError):
        notices.issue_vacate(owner, lease)


def test_the_reason_can_be_left_off(owner, prop):
    lease = make_lease(owner, prop)
    landlord_notice(owner, lease, reason="Private reason")
    letter = notices.issue_vacate(owner, lease, print_reason=False)
    assert letter.facts["reason"] == ""
    assert "Private reason" not in " ".join(notices.paragraphs(Kind.VACATE, letter.facts))


def test_withdrawing_or_replacing_the_notice_withdraws_its_letter(owner, prop):
    lease = make_lease(owner, prop)
    landlord_notice(owner, lease)
    first = notices.issue_vacate(owner, lease)
    landlord_notice(owner, lease)  # a later notice replaces the open one
    first.refresh_from_db()
    assert first.is_withdrawn and "Replaced by a later notice" in first.withdraw_reason
    second = notices.issue_vacate(owner, lease)
    lease_services.withdraw_notice(owner, lease, reason="Agreed to stay")
    second.refresh_from_db()
    assert second.is_withdrawn and "Agreed to stay" in second.withdraw_reason


def test_commercial_property_and_backdated_notice_are_warned(owner, prop):
    lease = make_lease(owner, prop)
    notice = landlord_notice(owner, lease, given_on=timezone.localdate() - datetime.timedelta(days=3))
    concerns = notices.vacate_concerns(lease, notice)
    assert len(concerns) == 1 and "before today" in concerns[0]
    Property.objects.filter(pk=prop.pk).update(category=Property.Category.COMMERCIAL)
    lease.unit.property.refresh_from_db()
    assert "Cap. 301" in notices.vacate_concerns(lease, notice)[0]


def test_late_service_of_a_notice_is_flagged(owner, prop):
    lease = make_lease(owner, prop)
    today = timezone.localdate()
    landlord_notice(owner, lease, given_on=today - datetime.timedelta(days=10))
    letter = notices.issue_vacate(owner, lease)
    NoticeLetter.objects.filter(pk=letter.pk).update(issued_at=timezone.now() - datetime.timedelta(days=10))
    letter.refresh_from_db()
    notices.record_service(owner, letter, served_on=today, served_via=NoticeLetter.ServedVia.HAND)
    letter.refresh_from_db()
    warning = notices.service_concerns(letter)
    assert warning and "may be entitled to stay" in warning[0]


# ---------------------------------------------------------------------------
# Demand
# ---------------------------------------------------------------------------


def test_arrears_leave_out_invoices_still_within_grace(owner, prop):
    lease = in_arrears(owner, prop)
    owed, late = notices.arrears([lease], D(2026, 4, 2))  # April is due on the 1st with 3 days' grace
    assert owed == 30000 and len(late) == 2
    owed, late = notices.arrears([lease], D(2026, 5, 1))
    assert owed == 45000 and len(late) == 3


def test_nothing_to_demand_when_paid_up(owner, prop):
    lease = make_lease(owner, prop)
    assert "Nothing past its grace days" in notices.demand_problem(lease)
    with pytest.raises(ValidationError):
        notices.issue_demand(owner, lease)


def test_issuing_a_demand(owner, prop):
    lease = in_arrears(owner, prop)
    PaymentAccount.objects.create(organization=owner.organization, type=PaymentAccount.Type.PAYBILL,
                                  number="4001234", display_name="Rent Paybill")
    letter = notices.issue_demand(owner, lease, pay_within_days=14, note="Call us to agree a plan.")
    data = letter.facts
    assert letter.number.startswith("DEM-") and letter.kind == Kind.DEMAND and letter.notice is None
    assert data["owed"] == "45000.00" and len(data["invoices"]) == 3 and data["pay_within_days"] == 14
    assert data["ways_to_pay"] == [f"M-Pesa Paybill 4001234, account {lease.unit.payment_reference}"]
    text = " ".join(notices.paragraphs(Kind.DEMAND, data))
    assert "within 14 days of the day you receive this letter" in text and "Call us to agree a plan." in text
    rows = notices.arrears_rows(data)
    assert rows[-1][0] == "Total owed" and len(rows) == 4
    assert letter.pdf.read().startswith(b"%PDF")
    with pytest.raises(ValidationError):
        notices.issue_demand(owner, lease, pay_within_days=0)


def test_demand_warnings_for_a_promise_a_dispute_and_a_running_demand(owner, prop):
    lease = in_arrears(owner, prop)
    today = timezone.localdate()
    FollowUp.objects.create(organization=owner.organization, lease=lease, outcome=FollowUp.Outcome.PROMISED,
                            promised_on=today + datetime.timedelta(days=2))
    assert "promised" in notices.demand_concerns(lease)[0]
    FollowUp.objects.create(organization=owner.organization, lease=lease, outcome=FollowUp.Outcome.DISPUTED)
    assert "disputes" in notices.demand_concerns(lease)[0]
    letter = notices.issue_demand(owner, lease, pay_within_days=7)
    notices.record_service(owner, letter, served_on=today, served_via=NoticeLetter.ServedVia.ADULT)
    letter.refresh_from_db()
    assert letter.pay_by == today + datetime.timedelta(days=7)
    assert any(letter.number in c for c in notices.demand_concerns(lease))


# ---------------------------------------------------------------------------
# Service and withdrawing
# ---------------------------------------------------------------------------


def test_recording_and_correcting_service_with_a_signed_copy(owner, prop):
    lease = in_arrears(owner, prop)
    letter = notices.issue_demand(owner, lease)
    today = timezone.localdate()
    with pytest.raises(ValidationError):
        notices.record_service(owner, letter, served_on=today + datetime.timedelta(days=1),
                               served_via=NoticeLetter.ServedVia.HAND)
    with pytest.raises(ValidationError):
        notices.record_service(owner, letter, served_on=today - datetime.timedelta(days=1),
                               served_via=NoticeLetter.ServedVia.HAND)
    with pytest.raises(ValidationError):
        notices.record_service(owner, letter, served_on=today, served_via="CARRIER_PIGEON")
    notices.record_service(owner, letter, served_on=today, served_via=NoticeLetter.ServedVia.HAND,
                           served_by_name="Caretaker", received_by="Tenant A1",
                           proof_file=SimpleUploadedFile("signed.pdf", PDF, content_type="application/pdf"))
    letter.refresh_from_db()
    assert letter.is_served and letter.proof.lease == lease and letter.proof.category == Document.Category.NOTICE
    notices.record_service(owner, letter, served_on=today, served_via=NoticeLetter.ServedVia.ADULT,
                           served_by_name="Caretaker", received_by="Spouse")
    event = AuditEvent.objects.filter(action="notice_letter.serve").order_by("-pk").first()
    assert event.changes["served_via"] == ["HAND", "ADULT"] and event.changes["received_by"] == ["Tenant A1", "Spouse"]


def test_withdrawn_letters_cannot_be_served_and_say_so_on_the_check_page(client, owner, prop):
    lease = in_arrears(owner, prop)
    letter = notices.issue_demand(owner, lease)
    page = client.get(reverse("letter_check", args=[letter.verify_code]))
    assert page.status_code == 200 and b"Demand for rent arrears" in page.content and b"45,000" in page.content
    with pytest.raises(ValidationError):
        notices.withdraw(owner, letter, reason="")
    notices.withdraw(owner, letter, reason="Wrong amount")
    letter.refresh_from_db()
    with pytest.raises(ValidationError):
        notices.record_service(owner, letter, served_on=timezone.localdate(), served_via=NoticeLetter.ServedVia.HAND)
    assert b"withdrew this letter" in client.get(reverse("letter_check", args=[letter.verify_code])).content


# ---------------------------------------------------------------------------
# Who may and pages
# ---------------------------------------------------------------------------


def test_capability_goes_to_owner_and_manager_only(owner, prop):
    holders = {t.key for t in ROLE_TEMPLATES if "leases.issue_notice" in t.capabilities}
    assert holders == {"owner", "manager"}
    lease = in_arrears(owner, prop)
    with pytest.raises(PermissionDenied):
        notices.issue_demand(add_member(owner.organization, "accountant", all_properties=True), lease)
    elsewhere = add_member(owner.organization, "manager", properties=[make_property(owner.organization)])
    with pytest.raises(PermissionDenied):
        notices.issue_demand(elsewhere, lease)
    assert notices.issue_demand(add_member(owner.organization, "manager", properties=[prop]), lease)


def test_pages(client, owner, prop):
    lease = in_arrears(owner, prop)
    landlord_notice(owner, lease)
    login(client, owner)
    assert b"Notice and demand letters" in client.get(reverse("leases:detail", args=[lease.public_id])).content
    url = reverse("letters:notices", args=[lease.public_id])
    page = client.get(url)
    assert page.status_code == 200 and b"hand over vacant possession" in page.content and b"Total owed" in page.content
    assert client.post(url, {"action": "demand", "pay_within_days": "0"}).status_code == 400
    assert client.post(url, {"action": "demand", "pay_within_days": "7"}).status_code == 302
    assert client.post(url, {"action": "vacate", "print_reason": "on"}).status_code == 302
    demand = NoticeLetter.objects.get(kind=Kind.DEMAND)
    pdf = client.get(reverse("letters:notice_pdf", args=[demand.public_id]))
    assert pdf.status_code == 200 and pdf["Content-Type"] == "application/pdf"
    service = reverse("letters:notice_service", args=[demand.public_id])
    assert client.get(service).status_code == 200
    assert client.post(service, {"served_on": "", "served_via": "HAND"}).status_code == 400
    assert client.post(service, {"served_on": timezone.localdate().isoformat(), "served_via": "HAND",
                                 "proof": SimpleUploadedFile("s.pdf", PDF, content_type="application/pdf")},
                       ).status_code == 302
    assert b"Pay by" in client.get(url).content
    assert client.post(url, {"action": "withdraw", "letter": demand.public_id, "reason": "Error"}).status_code == 302
    assert client.get(service).status_code == 404


def test_pages_outside_scope_are_404_and_without_capability_403(client, owner, prop):
    lease = in_arrears(owner, prop)
    letter = notices.issue_demand(owner, lease)
    login(client, make_org())
    assert client.get(reverse("letters:notices", args=[lease.public_id])).status_code == 404
    assert client.get(reverse("letters:notice_pdf", args=[letter.public_id])).status_code == 404
    assert client.get(reverse("letters:notice_service", args=[letter.public_id])).status_code == 404
    login(client, add_member(owner.organization, "accountant", all_properties=True))
    assert client.get(reverse("letters:notices", args=[lease.public_id])).status_code == 403
    assert b"Notice and demand letters" not in client.get(reverse("leases:detail", args=[lease.public_id])).content


def test_billers_that_are_not_landlords_have_no_notices_or_deposit_rows(client, owner, prop):
    lease = make_lease(owner, prop)
    login(client, owner)
    detail = reverse("leases:detail", args=[lease.public_id])
    page = client.get(detail).content
    assert b"Notice and demand letters" in page and b"Deposit" in page

    org = owner.organization
    org.org_type = org.Type.ESTATE
    org.save(update_fields=["org_type"])
    page = client.get(detail).content
    assert b"Notice and demand letters" not in page and b"Record notice" not in page
    assert b"Deposit" not in page
    assert client.get(reverse("letters:notices", args=[lease.public_id])).status_code == 404
    assert client.get(reverse("letters:handover", args=[lease.public_id])).status_code == 404
    assert client.post(detail, {"action": "notice", "given_on": "2030-01-01"}).status_code == 404
