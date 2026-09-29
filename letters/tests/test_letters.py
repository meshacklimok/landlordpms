"""Tenancy and payment record letters (D-048): the facts, the settings, issuing, scope and the check page."""

import datetime

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.urls import reverse

from accounts.capabilities import ROLE_TEMPLATES
from accounts.tests.factories import add_member, make_org, make_property
from accounts.tests.test_isolation import login
from audit.models import AuditEvent
from billing import deposits
from billing.tests.test_invoicing import APR, FEB, JAN, MAR, bill, make_lease
from leases import services as lease_services
from letters import services
from letters.models import TenancyLetter
from payments import services as payment_services
from payments.models import Payment

pytestmark = pytest.mark.django_db

D = datetime.date


@pytest.fixture(autouse=True)
def _media(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path


@pytest.fixture
def owner():
    return make_org()


@pytest.fixture
def prop(owner):
    return make_property(owner.organization)


def pay(actor, lease, amount, paid_at):
    return payment_services.record_payment(actor, lease, amount=amount, method=Payment.Method.CASH, paid_at=paid_at)


def four_months(owner, prop):
    """Jan paid early, Feb late, Mar on the last day of grace, Apr not paid. Due on the 1st, 3 days' grace."""
    lease = make_lease(owner, prop)
    for month in (JAN, FEB, MAR, APR):
        bill(lease, month)
    pay(owner, lease, 15000, D(2025, 12, 30))
    pay(owner, lease, 15000, D(2026, 2, 10))
    march = pay(owner, lease, 15000, D(2026, 3, 4))
    return lease, march


# ---------------------------------------------------------------------------
# Facts
# ---------------------------------------------------------------------------


def test_payment_record_counts_on_time_late_and_unpaid_using_grace_days(owner, prop):
    lease, _ = four_months(owner, prop)
    record = services.facts(lease, today=D(2026, 5, 1))["payment_record"]
    assert (record["due"], record["on_time"], record["late"], record["unpaid"]) == (4, 2, 1, 1)
    assert (record["first"], record["last"]) == ("2026-01-01", "2026-04-01")


def test_invoices_still_within_grace_are_not_counted(owner, prop):
    lease, _ = four_months(owner, prop)
    assert services.facts(lease, today=D(2026, 4, 4))["payment_record"]["due"] == 3


def test_a_reversed_payment_does_not_count(owner, prop):
    lease, march = four_months(owner, prop)
    payment_services.reverse_payment(owner, march, reason="Cheque bounced")
    record = services.facts(lease, today=D(2026, 5, 1))["payment_record"]
    assert (record["on_time"], record["late"], record["unpaid"]) == (1, 1, 2)


def test_balance_and_rent(owner, prop):
    lease, _ = four_months(owner, prop)
    data = services.facts(lease, today=D(2026, 5, 1))
    assert data["balance"] == "15000.00" and data["rent"] == "15000.00" and data["current"]
    assert data["periods"][0]["end"] is None


def test_a_renewal_is_one_tenancy_filed_on_the_latest_lease(owner, prop):
    lease, _ = four_months(owner, prop)
    deposits.record_received(owner, lease, amount=30000, entry_date=JAN)
    draft = lease_services.renew_lease(owner, lease, start_date=D(2026, 5, 1), rent=16000)
    new = lease_services.activate_lease(owner, draft)
    lease.refresh_from_db()

    assert services.latest_lease(lease) == new
    assert [ls.pk for ls in services.tenancy(lease)] == [lease.pk, new.pk]
    data = services.facts(lease, today=D(2026, 5, 10))
    assert [p["start"] for p in data["periods"]] == ["2026-01-01", "2026-05-01"]
    assert data["periods"][0]["end"] == "2026-04-30" and data["periods"][1]["end"] is None
    assert data["balance"] == "15000.00"  # April, left on the old lease
    assert data["rent"] == "16000.00"
    assert data["deposit"] == {"received": "30000.00", "deducted": "0.00", "refunded": "0.00", "held": "30000.00"}

    letter = services.issue(owner, lease)
    assert letter.lease == new


def test_deposit_wording_while_held_and_once_cleared(owner, prop):
    lease = make_lease(owner, prop)
    deposits.record_received(owner, lease, amount=30000, entry_date=JAN)
    rows = dict(services.statements(services.facts(lease)))
    assert rows["Deposit"] == ["KES 30,000.00 held."]

    lease_services.end_lease(owner, lease, ended_on=D(2026, 6, 30))
    deposits.deduct(owner, lease, amount=5000, reason="Broken window")
    deposits.refund(owner, lease, amount=25000)
    lease.refresh_from_db()
    rows = dict(services.statements(services.facts(lease)))
    assert rows["Deposit"] == ["Cleared: KES 25,000.00 refunded, KES 5,000.00 deducted."]
    assert rows["Status"] == ["Tenancy ended on 30 Jun 2026"]
    assert "Broken window" not in str(rows)


def test_a_corrected_deduction_counts_net(owner, prop):
    lease = make_lease(owner, prop)
    deposits.record_received(owner, lease, amount=30000, entry_date=JAN)
    wrong = deposits.deduct(owner, lease, amount=5000, reason="Typo")
    deposits.reverse(owner, wrong, reason="Entered by mistake")
    assert services.facts(lease)["deposit"]["deducted"] == "0.00"


def test_no_deposit_and_no_invoices_due(owner, prop):
    lease = make_lease(owner, prop)
    rows = dict(services.statements(services.facts(lease)))
    assert rows["Deposit"] == ["No deposit recorded."]
    assert rows["Payment record"] == ["No rent had fallen due yet."]
    assert rows["Balance"][0].startswith("Nothing owed as at ")


def test_concerns_flag_arrears_and_late_payments(owner, prop):
    lease, _ = four_months(owner, prop)
    assert len(services.concerns(services.facts(lease, today=D(2026, 5, 1)))) == 2


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


def test_the_organization_chooses_what_letters_state(owner, prop):
    lease = make_lease(owner, prop)
    services.save_settings(owner, letter_show_payment_record=True, letter_show_balance=False,
                           letter_show_deposit=False, letter_show_rent=False)
    owner.organization.refresh_from_db()
    data = services.facts(lease)
    assert "payment_record" in data and not {"balance", "deposit", "rent"} & set(data)
    labels = [label for label, _ in services.statements(data)]
    assert labels == ["Tenant", "Tenancy", "Status", "Payment record"]
    event = AuditEvent.objects.get(action="letters.settings")
    assert event.changes["letter_show_balance"] == [True, False]


def test_only_organization_managers_change_the_settings(owner):
    manager = add_member(owner.organization, "manager", all_properties=True)
    with pytest.raises(PermissionDenied):
        services.save_settings(manager, letter_show_balance=False)


# ---------------------------------------------------------------------------
# Issue and withdraw
# ---------------------------------------------------------------------------


def test_issue_numbers_stores_freezes_and_audits(owner, prop):
    lease, _ = four_months(owner, prop)
    letter = services.issue(owner, lease)
    year = datetime.date.today().year
    assert letter.number == f"LTR-{year}-000001"
    assert letter.pdf.read().startswith(b"%PDF")
    assert letter.facts["balance"] == "15000.00"
    pay(owner, lease, 15000, D(2026, 5, 2))
    letter.refresh_from_db()
    assert letter.facts["balance"] == "15000.00"  # frozen
    assert services.issue(owner, lease).number == f"LTR-{year}-000002"
    assert AuditEvent.objects.filter(action="letter.issue").count() == 2


def test_who_may_issue(owner, prop):
    lease = make_lease(owner, prop)
    manager = add_member(owner.organization, "manager", properties=[prop])
    services.issue(manager, lease)
    for role in ("caretaker", "accountant", "viewer", "leasing_agent"):
        with pytest.raises(PermissionDenied):
            services.issue(add_member(owner.organization, role, all_properties=True), lease)
    elsewhere = add_member(owner.organization, "manager", properties=[make_property(owner.organization)])
    with pytest.raises(PermissionDenied):
        services.issue(elsewhere, lease)
    with pytest.raises(PermissionDenied):
        services.issue(make_org(), lease)


def test_manager_template_has_the_capability():
    templates = {t.key: t.capabilities for t in ROLE_TEMPLATES}
    assert "tenants.issue_letter" in templates["manager"] and "tenants.issue_letter" in templates["owner"]
    assert "tenants.issue_letter" not in templates["caretaker"]


def test_a_draft_lease_gets_no_letter(owner, prop):
    lease = make_lease(owner, prop)
    draft = lease_services.renew_lease(owner, lease, start_date=D(2026, 12, 1))
    assert services.latest_lease(lease) == lease  # a draft renewal is not the tenancy yet
    with pytest.raises(ValidationError):
        services.issue(owner, draft)


def test_withdraw_needs_a_reason_and_happens_once(owner, prop):
    letter = services.issue(owner, make_lease(owner, prop))
    with pytest.raises(ValidationError):
        services.withdraw(owner, letter, reason=" ")
    services.withdraw(owner, letter, reason="Wrong tenant")
    letter.refresh_from_db()
    assert letter.is_withdrawn and letter.withdraw_reason == "Wrong tenant"
    with pytest.raises(ValidationError):
        services.withdraw(owner, letter, reason="Again")


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------


def test_lease_page_links_to_letters_and_the_letter_is_issued(client, owner, prop):
    lease = make_lease(owner, prop)
    login(client, owner)
    assert b"Tenancy letter" in client.get(reverse("leases:detail", args=[lease.public_id])).content
    url = reverse("letters:lease", args=[lease.public_id])
    page = client.get(url)
    assert page.status_code == 200 and b"Issue letter" in page.content
    assert client.post(url, {"action": "issue"}).status_code == 302
    letter = TenancyLetter.objects.get()
    pdf = client.get(reverse("letters:pdf", args=[letter.public_id]))
    assert pdf.status_code == 200 and pdf["Content-Type"] == "application/pdf"
    assert letter.number.encode() in client.get(url).content


def test_withdraw_through_the_page(client, owner, prop):
    lease = make_lease(owner, prop)
    letter = services.issue(owner, lease)
    login(client, owner)
    url = reverse("letters:lease", args=[lease.public_id])
    assert client.post(url, {"action": "withdraw", "letter": letter.public_id, "reason": ""}).status_code == 400
    assert client.post(url, {"action": "withdraw", "letter": letter.public_id, "reason": "Error"}).status_code == 302
    letter.refresh_from_db()
    assert letter.is_withdrawn


def test_pages_outside_scope_are_404_and_without_capability_403(client, owner, prop):
    lease = make_lease(owner, prop)
    letter = services.issue(owner, lease)
    other = make_org()
    login(client, other)
    assert client.get(reverse("letters:lease", args=[lease.public_id])).status_code == 404
    assert client.get(reverse("letters:pdf", args=[letter.public_id])).status_code == 404
    client.logout()
    login(client, add_member(owner.organization, "caretaker", all_properties=True))
    assert client.get(reverse("letters:lease", args=[lease.public_id])).status_code == 403
    assert b"Tenancy letter" not in client.get(reverse("leases:detail", args=[lease.public_id])).content


def test_settings_page_saves_and_returns_to_the_lease(client, owner, prop):
    lease = make_lease(owner, prop)
    login(client, owner)
    back = reverse("letters:lease", args=[lease.public_id])
    response = client.post(reverse("letters:settings"), {"letter_show_rent": "on", "next": back})
    assert response.status_code == 302 and response.url == back
    owner.organization.refresh_from_db()
    org = owner.organization
    assert org.letter_show_rent and not (org.letter_show_balance or org.letter_show_deposit
                                         or org.letter_show_payment_record)
    evil = client.post(reverse("letters:settings"), {"next": "//evil.example"})
    assert evil.url == reverse("letters:settings")


def test_the_check_page_shows_the_frozen_facts_and_withdrawal(client, owner, prop):
    letter = services.issue(owner, make_lease(owner, prop))
    assert services.verify_url(letter).endswith(f"/l/{letter.verify_code}/")
    url = reverse("letter_check", args=[letter.verify_code])
    page = client.get(url)
    assert page.status_code == 200 and letter.number.encode() in page.content
    assert b"Tenant A1" in page.content and page["X-Robots-Tag"] == "noindex, nofollow"
    services.withdraw(owner, letter, reason="Wrong tenant")
    assert b"withdrew this letter" in client.get(url).content
    assert b"Wrong tenant" not in client.get(url).content
    assert client.get(reverse("letter_check", args=["nope"])).status_code == 404
