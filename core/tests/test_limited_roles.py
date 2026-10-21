"""A member sees only what the owner gave them (D-108): the pages open to any staff member, with real data behind them."""

import datetime
import json

import pytest
from django.test import RequestFactory

from accounts import mfa
from accounts.tests.factories import make_org, make_property
from accounts.web_api import HomeDataView
from billing import invoicing
from core.tests.test_access_map import _member_with
from leases import services as lease_services
from properties import services as property_services
from reports import home as reports_home
from search import services as search_services
from tenants import services as tenant_services

pytestmark = pytest.mark.django_db

JAN, FEB = datetime.date(2026, 1, 1), datetime.date(2026, 2, 1)


@pytest.fixture
def owner():
    owner = make_org()
    prop = make_property(owner.organization)
    unit = property_services.create_unit(owner, prop, code="A1")
    tenant = tenant_services.create_tenant(owner, name="Wanjiku Kamau", phone="0712345601")
    lease = lease_services.create_lease(owner, unit=unit, tenants=[tenant], start_date=JAN, end_date=None, rent=15000)
    lease = lease_services.activate_lease(owner, lease)
    invoicing.generate_lease_period(lease, FEB, today=datetime.date(2026, 1, 27))
    return owner


def _home_json(member):
    request = RequestFactory().get("/web-api/home/")
    request.user, request.membership, request.organization = member.user, member, member.organization
    request.session = {mfa.VERIFIED: True}
    return json.loads(HomeDataView.as_view()(request).content)


def test_a_role_with_nothing_given_sees_no_figures_and_finds_nothing(owner):
    member = _member_with(owner, set(), "Nothing")
    data = _home_json(member)
    assert data["figures"] is None and data["debtors"] is None and data["payments"] is None
    assert data["summary"] is None and data["actions"] == [] and data["tasks"] == []
    for q in ("Wanjiku", "A1", "0712345601", "INV-2026"):
        assert search_services.search(member, q).count == 0


def test_a_role_given_only_tenants_finds_tenants_and_nothing_else(owner):
    member = _member_with(owner, {"tenants.view"}, "Tenants only")
    data = _home_json(member)
    assert data["figures"] is None and data["debtors"] is None and data["payments"] is None
    results = search_services.search(member, "Wanjiku")
    assert {g.key for g in results.groups if g.hits} == {"tenants"}
    assert search_services.search(member, "INV-2026").count == 0
    assert search_services.search(member, "A1").count == 0


def test_a_summary_role_sees_counts_but_never_amounts(owner):
    member = _member_with(owner, {"dashboard.view_summary"}, "Summary")
    data = _home_json(member)
    assert data["figures"] is None and data["debtors"] is None and data["payments"] is None
    assert data["summary"] is not None
    assert all("amount" not in key for key in data["summary"])


def test_a_financial_role_without_invoices_or_payments_sees_no_debtors_or_payments(owner):
    member = _member_with(owner, {"dashboard.view_financial"}, "Dashboard only")
    data = _home_json(member)
    assert data["figures"] is not None
    assert data["debtors"] is None and data["payments"] is None
    assert reports_home.home(member).debtors == []


def test_a_role_limited_to_another_property_sees_none_of_this_one(owner):
    other = make_property(owner.organization)
    member = _member_with(owner, {"tenants.view", "leases.view", "invoices.view", "dashboard.view_financial"}, "Other")
    member.all_properties = False
    member.save()
    from accounts.models import PropertyAccess
    PropertyAccess.objects.create(membership=member, property=other)
    assert search_services.search(member, "Wanjiku").count == 0
    assert search_services.search(member, "INV-2026").count == 0
    assert _home_json(member)["debtors"] in ([], None)


# --- the assistant and the Next.js endpoints --------------------------------------------------------------------------

def _call(view, member, path="/x/", **kwargs):
    request = RequestFactory().get(path)
    request.user, request.membership, request.organization = member.user, member, member.organization
    request.session = {mfa.VERIFIED: True}
    request.user_memberships = [member]
    return view.as_view()(request, **kwargs)


def test_every_assistant_tool_refuses_a_role_with_nothing_given(owner):
    from ai_assistant import tools

    member = _member_with(owner, set(), "Nothing")
    leaked = []
    for tool in tools.TOOLS:
        if tool.name == "how_to":  # help pages hold no organization data
            continue
        args = {"query": "Wanjiku", "q": "Wanjiku", "lease": "x", "month": "2026-02"}
        args["text"] = "Wanjiku"
        result = tools.run(member, tool.name, args)
        if tool.name == "find":  # a search: it must come back empty, not refused
            if result.get("groups"):
                leaked.append(tool.name)
        elif result != tools.NOT_ALLOWED:
            leaked.append(tool.name)
    assert leaked == []


def test_the_assistant_gives_a_tenants_only_role_tenants_and_no_money(owner):
    from ai_assistant import tools

    member = _member_with(owner, {"tenants.view"}, "Tenants only")
    assert tools.run(member, "who_owes", {}) == tools.NOT_ALLOWED
    assert tools.run(member, "summary", {}) == tools.NOT_ALLOWED
    assert tools.run(member, "vacant_units", {}) == tools.NOT_ALLOWED
    found = tools.run(member, "find", {"text": "Wanjiku"})
    assert [g["kind"] for g in found["groups"]] == ["Tenants"] or len(found["groups"]) == 1
    assert tools.run(member, "find", {"text": "INV-2026"})["groups"] == []


def test_the_next_js_endpoints_show_a_role_with_nothing_given_no_menu_and_no_settings(owner):
    from accounts.web_api import OrganizationSettingsDataView, SessionView
    from imports.web_api import SwitchGuideDataView

    member = _member_with(owner, set(), "Nothing")
    session = json.loads(_call(SessionView, member).content)
    assert [item for group in session["nav"] for item in group["items"]] == []
    settings_page = _call(OrganizationSettingsDataView, member)
    assert settings_page.status_code in (403, 404) or not json.loads(settings_page.content).get("pages")
    assert json.loads(_call(SwitchGuideDataView, member).content).get("can_announce") is False


def test_a_new_account_gets_a_guided_checklist_and_no_figures():
    fresh = make_org()
    data = _home_json(fresh)
    assert data["started"] is False and data["figures"] is None
    checklist = data["checklist"]
    assert checklist["done"] == 0 and checklist["next"] == "property"
    assert checklist["minutes"] == sum(s["minutes"] for s in checklist["steps"] if not s["optional"]) > 0
    first = checklist["steps"][0]
    assert first["hint"] and first["icon"] and first["url"]


def test_the_checklist_moves_on_when_a_step_is_done():
    fresh = make_org()
    make_property(fresh.organization)
    assert _home_json(fresh)["checklist"]["next"] == "units"


def test_the_home_shows_rent_falling_due_and_cash_by_method(owner):
    today = datetime.date(2026, 2, 1)
    request = RequestFactory().get("/web-api/home/")
    request.user, request.membership, request.organization = owner.user, owner, owner.organization
    request.session = {mfa.VERIFIED: True}
    home = reports_home.home(owner, today=today)
    assert home.due_soon is not None and home.vacancy is not None
    data = _home_json(owner)
    assert "due_soon" in data and "vacancy" in data and isinstance(data["methods"], list)


def test_the_home_compares_the_same_days_and_nets_off_expenses(owner):
    data = _home_json(owner)
    assert set(data["pace"]) == {"now", "before", "label", "change"}
    assert set(data["net"]) == {"cash", "expenses", "net", "negative", "url"}
    limited = _member_with(owner, {"dashboard.view_financial"}, "No expenses")
    assert _home_json(limited)["net"] is None


def test_nudges_flag_long_empty_units_and_rent_late_month_after_month(owner, monkeypatch):
    import datetime
    from types import SimpleNamespace as NS

    from core import reportcache
    from reports import home as reports_home
    from reports import units

    def row(code, status, empty, states):
        return NS(status=status, empty_days=empty, unit=NS(code=code, property=NS(name="Moi")),
                  cells=[NS(state=s) for s in states])

    rows = [row("A1", "vacant", 90, []), row("A2", "vacant", 10, []),
            row("B1", "occupied", 0, [units.PAID, units.LATE, units.PART, units.UNPAID, units.DUE]),
            row("B2", "occupied", 0, [units.LATE, units.PAID, units.LATE, units.UNPAID]),
            row("B3", "occupied", 0, [units.LATE, units.LATE])]
    monkeypatch.setattr(reportcache, "cached", lambda *a, **k: NS(rows=rows))
    found = {n["key"]: n for n in reports_home.nudges(owner, NS(day=datetime.date(2026, 10, 21)))}
    assert found["long_empty"]["units"] == ["A1, Moi"] and found["long_empty"]["count"] == 1
    assert found["repeat_late"]["units"] == ["B1, Moi"]  # B2 paid in time once; B3 has too few billed months
    monkeypatch.setattr(reportcache, "cached", lambda *a, **k: NS(rows=[row("C1", "occupied", 0, [units.PAID] * 4)]))
    assert reports_home.nudges(owner, NS(day=datetime.date(2026, 10, 21))) == []


def test_the_home_json_carries_nudges(owner):
    assert isinstance(_home_json(owner)["nudges"], list)
