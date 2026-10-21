"""The lease agreement PDF and the move-out statement (D-073)."""

import datetime
from decimal import Decimal

import pytest
from django.urls import reverse

from accounts.tests.factories import add_member, make_org, make_property
from accounts.tests.test_isolation import login
from billing import deposits
from documents.models import Document
from inspections import services as inspections
from inspections.models import ConditionReport, ConditionReportLine
from leases import paperwork, services
from leases.models import Lease
from leases.pdf import render_agreement, render_move_out
from reports.tests.test_income import bill, make_lease, pay

pytestmark = pytest.mark.django_db

D = datetime.date


@pytest.fixture
def owner():
    return make_org()


@pytest.fixture
def org(owner):
    return owner.organization


@pytest.fixture
def prop(org):
    return make_property(org, name="Acacia Court")


@pytest.fixture
def lease(owner, prop):
    """Rent 20,000 from Jan 2025, deposit 20,000 received, Jan and Feb billed, Jan paid."""
    lease = make_lease(owner, prop, deposit_amount=20000)
    tenant = lease.lease_tenants.get().tenant
    tenant.id_type, tenant.id_number = "NATIONAL_ID", "12345678"
    tenant.save()
    deposits.record_received(owner, lease, amount=20000, entry_date=D(2025, 1, 2))
    bill(lease, D(2025, 1, 1), D(2025, 2, 1))
    pay(owner, lease, 20000, D(2025, 1, 5))
    return lease


def ended(owner, lease):
    return services.end_lease(owner, lease, ended_on=D(2025, 2, 28), reason="Moved away")


# ---------------------------------------------------------------------------
# Agreement
# ---------------------------------------------------------------------------


def test_agreement_facts(owner, org, prop, lease):
    data = paperwork.agreement(lease, owner)
    assert data["tenants"][0]["id"].endswith("12345678")
    assert data["rent"] == Decimal("20000.00") and data["deposit"] == Decimal("20000.00")
    assert data["property"] == "Acacia Court" and not data["draft"]
    assert render_agreement(data).startswith(b"%PDF")


def test_agreement_hides_ids_without_view_sensitive(org, prop, lease):
    agent = add_member(org, "leasing_agent", properties=[prop])
    data = paperwork.agreement(lease, agent)
    assert data["tenants"][0]["id"] is None and data["tenants"][0]["kra_pin"] is None


def test_agreement_for_a_draft(owner, prop, lease):
    draft = services.renew_lease(owner, lease, start_date=D(2026, 1, 1), end_date=None, rent=22000)
    data = paperwork.agreement(draft, owner)
    assert data["draft"] and render_agreement(data).startswith(b"%PDF")


def test_agreement_pdf_view(client, owner, lease):
    login(client, owner)
    url = reverse("leases:agreement", args=[lease.public_id])
    r = client.get(url)
    assert r.status_code == 200 and r["Content-Type"] == "application/pdf"
    assert r.content.startswith(b"%PDF") and r["Content-Disposition"].startswith("inline")
    assert client.get(url + "?download=1")["Content-Disposition"].startswith("attachment")
    assert url in client.get(reverse("leases:detail", args=[lease.public_id])).text


def test_agreement_saved_to_documents(client, owner, lease):
    login(client, owner)
    r = client.post(reverse("leases:agreement", args=[lease.public_id]), {"share": "on"})
    assert r.status_code == 302
    doc = Document.objects.get()
    assert doc.lease == lease and doc.category == Document.Category.LEASE and doc.kind == Document.Kind.PDF
    assert doc.shared_with_tenant and doc.file.read().startswith(b"%PDF")


def test_agreement_scope(client, org, lease):
    elsewhere = add_member(org, "manager", properties=[make_property(org)])
    login(client, elsewhere)
    assert client.get(reverse("leases:agreement", args=[lease.public_id])).status_code == 404
    client.logout()
    login(client, make_org())
    assert client.get(reverse("leases:agreement", args=[lease.public_id])).status_code == 404


def test_agreement_save_needs_upload(client, org, prop, lease):
    accountant = add_member(org, "accountant", properties=[prop])
    login(client, accountant)
    assert client.get(reverse("leases:agreement", args=[lease.public_id])).status_code == 200
    assert client.post(reverse("leases:agreement", args=[lease.public_id])).status_code == 404
    assert not Document.objects.exists()


# ---------------------------------------------------------------------------
# Move-out statement
# ---------------------------------------------------------------------------


def test_move_out_applies(owner, lease):
    assert not paperwork.move_out_applies(lease)
    services.give_notice(owner, lease, given_on=D(2025, 2, 1))
    lease.refresh_from_db()
    assert paperwork.move_out_applies(lease)
    s = paperwork.move_out(lease, D(2025, 2, 10))
    assert s["expected"] and s["moved_out"] == D(2025, 3, 3)


def test_move_out_figures(owner, lease):
    lease = ended(owner, lease)
    deposits.deduct(owner, lease, amount=3000, reason="Broken window", entry_date=D(2025, 3, 1))
    s = paperwork.move_out(lease, D(2025, 3, 2))
    rows = {r["key"]: r["amount"] for r in s["account"]}
    assert rows["charges"] == Decimal("40000.00") and rows["payments"] == Decimal("-20000.00")
    assert s["balance"] == Decimal("20000.00") and len(s["unpaid"]) == 1
    assert s["held"] == Decimal("17000.00")
    assert s["due"] == Decimal("-3000.00")  # 17,000 held less 20,000 owed
    assert not s["expected"] and s["moved_out"] == D(2025, 2, 28)
    assert s["condition"] is None
    assert render_move_out(s).startswith(b"%PDF")


def test_move_out_deposit_applied_to_balance(owner, lease):
    lease = ended(owner, lease)
    deposits.deduct(owner, lease, amount=20000, reason="February rent", apply_to_balance=True,
                    entry_date=D(2025, 3, 1))
    s = paperwork.move_out(lease)
    rows = {r["key"]: r["amount"] for r in s["account"]}
    assert rows["deposit_applied"] == Decimal("-20000.00")
    assert s["balance"] == 0 and s["held"] == 0 and s["due"] == 0 and not s["unpaid"]


def test_move_out_refund_due_with_credit(owner, lease):
    pay(owner, lease, 25000, D(2025, 2, 5))  # Feb plus 5,000 ahead
    lease = ended(owner, lease)
    s = paperwork.move_out(lease)
    assert s["balance"] == Decimal("-5000.00") and s["due"] == Decimal("25000.00")


def test_move_out_condition(owner, lease):
    reports = {}
    for kind, condition in (("MOVE_IN", "GOOD"), ("MOVE_OUT", "DAMAGED")):
        if kind == "MOVE_OUT":
            lease = ended(owner, lease)
        r = inspections.start(owner, lease, kind, inspected_on=D(2025, 2, 28))
        ConditionReportLine.objects.filter(report=r).update(condition="GOOD")
        ConditionReportLine.objects.filter(report=r).filter(
            pk=ConditionReportLine.objects.filter(report=r).order_by("pk").values("pk")[:1]).update(
            condition=condition)
        reports[kind] = inspections.complete(owner, r)
    c = paperwork.move_out(lease)["condition"]
    assert c["report"] == reports["MOVE_OUT"] and c["compared"]
    assert c["worse"] == 1 and c["items"] == ConditionReportLine.objects.filter(report=reports["MOVE_OUT"]).count()
    assert reports["MOVE_OUT"].kind == ConditionReport.Kind.MOVE_OUT


def test_move_out_page(client, owner, lease):
    login(client, owner)
    url = reverse("leases:move_out", args=[lease.public_id])
    assert client.get(url).status_code == 404  # no notice yet
    assert url not in client.get(reverse("leases:detail", args=[lease.public_id])).text
    lease = ended(owner, lease)
    r = client.get(url)
    assert r.status_code == 200 and "Nothing due either way" in r.text and "Save a copy to documents" in r.text
    assert url in client.get(reverse("leases:detail", args=[lease.public_id])).text
    assert url in client.get(reverse("billing:lease_account", args=[lease.public_id])).text
    pdf = client.get(reverse("leases:move_out_pdf", args=[lease.public_id]))
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")


def test_move_out_saved_to_documents(client, owner, lease):
    lease = ended(owner, lease)
    login(client, owner)
    r = client.post(reverse("leases:move_out", args=[lease.public_id]))
    assert r.status_code == 302
    doc = Document.objects.get()
    assert doc.category == Document.Category.NOTICE and not doc.shared_with_tenant
    assert doc.title.startswith(f"Move-out statement {lease.number}")


def test_move_out_needs_invoices_view(client, owner, org, prop, lease):
    ended(owner, lease)
    agent = add_member(org, "leasing_agent", properties=[prop])
    login(client, agent)
    assert client.get(reverse("leases:move_out", args=[lease.public_id])).status_code == 403
    assert client.get(reverse("leases:move_out_pdf", args=[lease.public_id])).status_code == 403
    assert "Move-out statement" not in client.get(reverse("leases:detail", args=[lease.public_id])).text


def test_move_out_viewer_cannot_save(client, owner, org, prop, lease):
    ended(owner, lease)
    viewer = add_member(org, "viewer", properties=[prop])
    login(client, viewer)
    r = client.get(reverse("leases:move_out", args=[lease.public_id]))
    assert r.status_code == 200 and "Save a copy to documents" not in r.text
    assert client.post(reverse("leases:move_out", args=[lease.public_id])).status_code == 404


def test_move_out_out_of_scope(client, owner, org, lease):
    ended(owner, lease)
    login(client, add_member(org, "manager", properties=[make_property(org)]))
    assert client.get(reverse("leases:move_out", args=[lease.public_id])).status_code == 404


def test_renewed_lease_has_no_move_out(owner, lease):
    services.renew_lease(owner, lease, start_date=D(2026, 1, 1), end_date=None, rent=22000)
    lease.status = Lease.Status.RENEWED
    assert not paperwork.move_out_applies(lease)


# Standard conditions and the blank template (D-187)

def test_agreement_prints_standard_conditions_with_lease_values(owner, lease):
    from leases import clauses

    filled = [clauses.fill(b, {"rent": "KES 20,000.00", "due_day": 5, "grace_days": 3, "deposit": "KES 20,000.00",
                               "notice_days": 30}) for _h, b in clauses.clauses_for(owner.organization)]
    assert any("KES 20,000.00" in b and "day 5" in b for b in filled)
    assert not any("{" in b for b in filled)
    assert render_agreement(paperwork.agreement(lease, owner)).startswith(b"%PDF")


def test_clause_parse_fill_and_check():
    from leases import clauses

    assert clauses.parse("A\nbody one\nmore\n\nB\nx") == [("A", "body one more"), ("B", "x")]
    assert clauses.fill("{rent} {other}", {"rent": "1"}) == "1 {other}"
    assert clauses.fill("{rent.__class__}", {"rent": "1"}) == "{rent.__class__}"
    clauses.check("A\nUnit {unit}")
    with pytest.raises(ValueError):
        clauses.check("A\n{nope}")


def test_blank_agreement_pdf(client, owner):
    login(client, owner)
    r = client.get(reverse("leases:agreement_blank"))
    assert r.status_code == 200 and r["Content-Type"] == "application/pdf" and r.content.startswith(b"%PDF")


def test_own_wording_is_saved_used_and_reset(client, owner, lease):
    login(client, owner)
    url = reverse("leases:wording")
    assert client.get(url).status_code == 200
    bad = client.post(url, {"clauses": "Pets\nNo {pets}"})
    assert bad.status_code == 400
    r = client.post(url, {"clauses": "Pets\nNo pets in {unit}."})
    assert r.status_code == 302
    org = owner.organization
    org.refresh_from_db()
    assert org.agreement_clauses == "Pets\nNo pets in {unit}."
    from leases import clauses
    assert clauses.clauses_for(org) == [("Pets", "No pets in {unit}.")]
    assert client.get(reverse("leases:agreement", args=[lease.public_id])).status_code == 200
    assert client.post(url, {"reset": "1"}).status_code == 302
    org.refresh_from_db()
    assert org.agreement_clauses == ""


def test_wording_needs_organization_manage(client, org, prop):
    agent = add_member(org, "leasing_agent", properties=[prop])
    login(client, agent)
    assert client.get(reverse("leases:wording")).status_code == 403
    assert client.get(reverse("leases:agreement_blank")).status_code == 200
