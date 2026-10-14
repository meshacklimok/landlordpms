"""How rent gets paid, for the charts (D-114), checked against the hand-worked block in test_units."""

from decimal import Decimal

import pytest
from django.template import Context, Template

from core import charts
from payments import selectors as payment_selectors
from payments.models import Payment
from reports import metrics, patterns, units
from reports.tests.test_units import FEB, JAN, MAR, TODAY, block, owner, prop  # noqa: F401 (fixtures)

pytestmark = pytest.mark.django_db


def money(value):
    return Decimal(value).quantize(Decimal("0.01"))


def test_each_month_counts_rents_by_how_they_were_paid(owner, block):  # noqa: F811
    rows = patterns.timeliness(metrics.scope(owner), MAR, TODAY)
    assert len(rows) == 12 and rows[-1].month == MAR
    jan, feb, mar = rows[-3:]
    assert (jan.counts[units.PAID], jan.counts[units.PART], jan.billed) == (1, 1, 2)
    assert jan.in_time_share == Decimal("0.5")
    assert (feb.counts[units.LATE], feb.billed) == (1, 1)
    assert (mar.counts[units.UNPAID], mar.billed) == (1, 1)
    assert all(r.billed == 0 and r.in_time_share is None for r in rows[:-3])
    spec = patterns.timeliness_chart(rows)
    assert spec["share"] and [s["color"] for s in spec["series"]] == [patterns.COLORS[s] for s in patterns.STATES]


def test_the_unit_page_counts_the_same_as_the_dashboard(owner, block):  # noqa: F811
    report = units.unit_report(owner, today=TODAY)
    from_cells = patterns.timeliness_from_cells(report.months, (r.cells for r in report.rows))
    assert [r.counts for r in from_cells] == [r.counts for r in patterns.timeliness(metrics.scope(owner), MAR, TODAY)]


def test_cash_by_method_and_month(owner, block):  # noqa: F811
    mm = patterns.methods_by_month(metrics.scope(owner), MAR)
    assert [m for m, _label in mm.methods] == ["CASH"]
    assert mm.row("CASH")[-3:] == [money(30000), money(20000), 0]
    assert mm.table[-3][1] == [money(30000)]


def test_one_lease_month_by_month(block):  # noqa: F811
    good, _part, _empty = block
    lm = patterns.lease_months(good, TODAY)
    assert [c.state for c in lm.cells[-3:]] == [units.PAID, units.LATE, units.UNPAID]
    assert (lm.in_time, len(lm.due), lm.expected, lm.collected) == (1, 3, money(60000), money(40000))
    assert lm.billed and lm.rate == money(40000) / money(60000)
    spec = patterns.lease_chart(lm, "KES")
    assert spec["series"][1]["colors"][-3:] == ["1", "late-1", "late-4"]


def test_payments_received_by_month_method_and_property(owner, block):  # noqa: F811
    payments = Payment.objects.filter(organization=owner.organization)
    got = payment_selectors.received(payments, owner.organization.currency, TODAY)
    assert not got.daily and [p[0] for p in got.periods] == [JAN, FEB, MAR]
    assert [(p[1], p[2]) for p in got.periods] == [(money(30000), 2), (money(20000), 1), (0, 0)]
    assert got.total == money(50000) and got.places == [("Acacia Court", money(50000))]
    assert got.methods == [(str(dict(Payment.Method.choices)["CASH"]), money(50000), 3)]

    feb = payments.filter(paid_at__gte=FEB, paid_at__lt=MAR)
    got = payment_selectors.received(feb, owner.organization.currency, TODAY, date_from=FEB, date_to=MAR)
    assert got.daily and len(got.periods) == 29 and got.total == money(20000)
    assert payment_selectors.received(payments.none(), owner.organization.currency, TODAY) is None


def test_fold_keeps_the_largest_and_sums_the_rest():
    rows = [("a", 1), ("b", 6), ("c", 5), ("d", 4), ("e", 3), ("f", 2), ("g", 0)]
    assert charts.fold(rows, "Other") == [("b", 6), ("c", 5), ("d", 4), ("e", 3), ("Other", 3)]
    assert charts.fold(rows[:3], "Other") == [("b", 6), ("c", 5), ("a", 1)]


def test_the_ring_draws_the_percent_and_a_dash_without_a_rate():
    template = Template('{% load reports %}{% ring value "Paid" %}|{% ring none "Paid" size="sm" %}')
    html = template.render(Context({"value": Decimal("0.75"), "none": None}))
    drawn, empty = html.split("|")
    assert 'stroke-dasharray="75 100"' in drawn and "75%" in drawn and 'aria-label="Paid: 75%"' in drawn
    assert "ring-arc" not in empty and "ring-sm" in empty and "—" in empty


def test_the_lease_account_page_draws_the_months(client, owner, block, monkeypatch):  # noqa: F811
    from django.urls import reverse
    from django.utils import timezone

    from accounts.tests.test_isolation import login

    good, _part, _empty = block
    monkeypatch.setattr(timezone, "localdate", lambda *a, **k: TODAY)
    login(client, owner)
    page = client.get(reverse("billing:lease_account", args=[good.public_id])).content.decode()
    assert 'id="chart-lease-months"' in page and 'aria-label="Paid in time: 33%"' in page
    assert "js/charts.js" in page
