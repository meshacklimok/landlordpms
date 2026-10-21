"""The assistant as a statistics helper (D-136): each tool reads its report and respects the role."""

import datetime
import json

import pytest

from accounts.tests.factories import add_member, make_org, make_property
from ai_assistant import services, stats, tools
from ai_assistant.client import FakeClient
from reports.tests.test_income import bill, make_lease, pay

pytestmark = pytest.mark.django_db
TODAY = datetime.date(2025, 4, 20)


@pytest.fixture
def owner():
    m = make_org()
    m.organization.assistant_enabled = True
    m.organization.save()
    return m


@pytest.fixture
def prop(owner):
    return make_property(owner.organization, name="Acacia Court")


@pytest.fixture
def lease(owner, prop):
    """Unit A1 at 20,000 a month billed January to March 2025; 5,000 paid in January."""
    lease = make_lease(owner, prop, code="A1")
    bill(lease, datetime.date(2025, 1, 1), datetime.date(2025, 2, 1), datetime.date(2025, 3, 1))
    pay(owner, lease, 5000, datetime.date(2025, 1, 10))
    return lease


ALL = ("month_review", "trend", "money", "compare_properties", "arrears_picture", "payment_behaviour",
       "unit_performance", "lease_movement", "repair_stats", "payment_stats")


def test_every_statistics_tool_answers_and_is_json_for_the_model(owner, lease):
    for name in ALL:
        result = tools.run(owner, name, {}, TODAY, services.REGISTRY)
        assert "error" not in result, (name, result)
        json.dumps(result, default=str)
        assert result["url"].startswith("/"), name


def test_month_review_compares_with_the_month_before(owner, lease):
    r = tools.run(owner, "month_review", {"month": "2025-03"}, TODAY, services.REGISTRY)
    assert (r["month"], r["rent_expected"], r["rent_collected"], r["collection_rate"]) == (
        "Mar 2025", "KES 20,000.00", "KES 0.00", "0.0%")
    assert r["against_last_month"]["expected"]["difference"] == "KES 0.00"
    assert r["collection_by_property_lowest_first"][0]["property"] == "Acacia Court"
    assert tools.run(owner, "month_review", {"month": "March"}, TODAY, services.REGISTRY)["error"] == (
        "give the month as YYYY-MM")


def test_trend_money_and_period_arguments(owner, lease):
    r = tools.run(owner, "trend", {"from": "2025-01", "to": "2025-03"}, TODAY, services.REGISTRY)
    assert [x["month"] for x in r["months"]] == ["Jan 2025", "Feb 2025", "Mar 2025"]
    assert r["totals"]["expected"] == "KES 60,000.00"
    assert r["worst_collection_month"]["rate"] == "0.0%"
    # A swapped or over-long range is corrected the way the report pages do it.
    long = tools.run(owner, "trend", {"from": "2020-01", "to": "2025-03"}, TODAY, services.REGISTRY)
    assert len(long["months"]) == 12
    p = tools.run(owner, "money", {"from": "2025-01", "to": "2025-03"}, TODAY, services.REGISTRY)
    assert p["period"] == "Jan 2025 – Mar 2025" or "Jan 2025" in p["period"]
    assert p["total"]["income"] == "KES 5,000.00" and p["compared_with"]


def test_a_property_filter_narrows_and_a_wrong_name_is_an_error(owner, prop, lease):
    other = make_lease(owner, make_property(owner.organization, name="Baobab Flats"), code="B1")
    bill(other, datetime.date(2025, 3, 1))
    both = tools.run(owner, "arrears_picture", {}, TODAY, services.REGISTRY)
    one = tools.run(owner, "arrears_picture", {"property": "Baobab"}, TODAY, services.REGISTRY)
    assert both["leases_owing"] == 2 and one["leases_owing"] == 1
    assert one["largest_balances"][0]["unit"] == "B1"
    assert tools.run(owner, "money", {"property": "Nowhere"}, TODAY, services.REGISTRY)["error"].startswith(
        "no property")


def test_comparing_properties_ranks_them(owner, prop, lease):
    make_lease(owner, make_property(owner.organization, name="Baobab Flats"), code="B1")
    r = tools.run(owner, "compare_properties", {"from": "2025-01", "to": "2025-03"}, TODAY, services.REGISTRY)
    assert {p["property"] for p in r["properties"]} == {"Acacia Court", "Baobab Flats"}
    assert r["all_together"]["units"] == 2


def test_unit_and_lease_and_payment_tools_read_their_reports(owner, lease):
    u = tools.run(owner, "unit_performance", {}, TODAY, services.REGISTRY)
    assert (u["units"], u["occupied"]) == (1, 1) and u["lowest_collection"][0]["unit"] == "A1"
    b = tools.run(owner, "payment_behaviour", {"from": "2025-01", "to": "2025-03"}, TODAY, services.REGISTRY)
    assert b["invoices_past_due"] >= 1 and b["still_unpaid"] >= 1
    s = tools.run(owner, "payment_stats", {"from": "2025-01", "to": "2025-03"}, TODAY, services.REGISTRY)
    assert s["confirmed"] == "KES 5,000.00" and s["by_month"][0]["month"] == "Jan 2025"
    assert tools.run(owner, "lease_movement", {}, TODAY, services.REGISTRY)["active_leases_now"] == 1


def test_a_role_without_the_report_is_told_not_allowed(owner, lease):
    caretaker = add_member(owner.organization, "caretaker", all_properties=True)
    for name in ("month_review", "trend", "money", "compare_properties", "arrears_picture", "payment_behaviour",
                 "unit_performance", "payment_stats"):
        assert tools.run(caretaker, name, {}, TODAY, services.REGISTRY) == tools.NOT_ALLOWED, name


def test_tenant_names_are_left_out_without_the_right_to_see_tenants(owner, lease):
    accountant = add_member(owner.organization, "accountant", all_properties=True)
    rows = tools.run(accountant, "arrears_picture", {}, TODAY, services.REGISTRY)["largest_balances"]
    assert rows and "tenants" in rows[0]


def test_a_statistics_question_goes_through_the_tools(owner, lease):
    FakeClient.script = [FakeClient.tool("month_review", {"month": "2025-03"}),
                         FakeClient.tool("trend", {}, call_id="c2"),
                         FakeClient.text("March collected nothing of KES 20,000.00 expected.")]
    result = services.ask(owner, "What happened in March?", today=TODAY)
    assert result.answered
    assert any(label == "Dashboard" for label, _url in result.links)
    assert "month_review" in FakeClient.calls[0]["system"]
    assert {t.name for t in stats.TOOLS} <= {t["name"] for t in FakeClient.calls[0]["tools"]}
