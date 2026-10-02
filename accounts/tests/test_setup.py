"""The home setup checklist and time to first invoice in admin (D-063 items 1 and 5)."""

import datetime
from types import SimpleNamespace

import pytest
from django.urls import reverse

from accounts import mfa, setup
from accounts.admin import OrganizationAdmin
from accounts.models import Organization
from payments.models import PaymentAccount

from .factories import add_member, make_org, make_user
from .test_isolation import login

pytestmark = pytest.mark.django_db


def test_a_new_workspace_sees_six_steps_with_links(client):
    owner = make_org()
    login(client, owner)
    response = client.get(reverse("accounts:home"))
    ctx = response.context
    assert ctx["show_checklist"] and (ctx["checklist_done"], ctx["checklist_total"]) == (0, 6)
    body = response.content.decode()
    assert "0 of 6 done" in body
    for step in ctx["checklist"]:
        assert step.url in body
    assert [s.key for s in ctx["checklist"] if s.optional] == ["invite"]


def test_a_payment_account_ticks_the_payment_step(client):
    owner = make_org()
    login(client, owner)
    PaymentAccount.objects.create(organization=owner.organization, type=PaymentAccount.Type.PAYBILL,
                                  number="123456", display_name="Paybill")
    steps = {s.key: s.done for s in client.get(reverse("accounts:home")).context["checklist"]}
    assert steps["payment"] and not steps["invoice"]


def test_inviting_is_optional_and_ticks_with_a_second_member(client):
    owner = make_org()
    assert not {s.key: s.done for s in setup.checklist(owner.organization)}["invite"]
    add_member(owner.organization, "caretaker")
    assert {s.key: s.done for s in setup.checklist(owner.organization)}["invite"]


def test_the_checklist_hides_once_the_required_steps_are_done(client, monkeypatch):
    owner = make_org()
    login(client, owner)
    steps = [setup.Step(k, k, "/", True) for k in ("a", "b", "c", "d", "e", "f")]
    monkeypatch.setattr(setup, "checklist", lambda org: [*steps, setup.Step("invite", "Invite", "/", False, True)])
    response = client.get(reverse("accounts:home"))
    assert not response.context["show_checklist"]
    assert "Get set up" not in response.content.decode()


def test_time_to_first_invoice_display():
    admin = OrganizationAdmin(Organization, None)
    created = datetime.datetime(2026, 9, 1, 9, tzinfo=datetime.UTC)
    assert admin.time_to_first_invoice(SimpleNamespace(created_at=created, _first_invoice=None)) == "—"
    same = SimpleNamespace(created_at=created, _first_invoice=created + datetime.timedelta(hours=3))
    assert admin.time_to_first_invoice(same) == "same day"
    later = SimpleNamespace(created_at=created, _first_invoice=created + datetime.timedelta(days=4, hours=1))
    assert admin.time_to_first_invoice(later) == "4 days"


@pytest.mark.parametrize("invoiced", ["", "yes", "no"])
def test_organization_admin_lists_with_the_invoiced_filter(client, monkeypatch, invoiced):
    make_org(name="Alpha Homes")
    monkeypatch.setattr(mfa, "is_enabled", lambda user: True)
    monkeypatch.setattr(mfa, "session_verified", lambda request: True)
    client.force_login(make_user(is_staff=True, is_superuser=True))
    url = reverse("admin:accounts_organization_changelist") + (f"?invoiced={invoiced}" if invoiced else "")
    response = client.get(url)
    assert response.status_code == 200
    assert ("Alpha Homes" in response.content.decode()) is (invoiced != "yes")
