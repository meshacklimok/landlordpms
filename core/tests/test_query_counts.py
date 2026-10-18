"""The main pages ask the database the same number of times however many rows they show (D-097 item 4, D-105)."""

import pytest

from accounts.tests.factories import make_org, make_property
from ops import scale

pytestmark = pytest.mark.django_db


def _queries(client, pages):
    rows = scale.time_pages(client, pages)
    assert [r["page"] for r in rows if r["status"] != 200] == []
    return {r["page"]: r["queries"] for r in rows}


def test_no_page_asks_once_per_row(client):
    owner = make_org()
    client.force_login(owner.user)
    prop = make_property(owner.organization, "Mvule Court")
    pages = scale.LISTS + [f"/properties/{prop.public_id}/"]
    for n in (1, 2):
        scale.add_lease(owner, prop, n)
    few = _queries(client, pages)
    for n in range(3, 9):
        scale.add_lease(owner, prop, n)
    many = _queries(client, pages)
    grows = {page: (few[page], many[page]) for page in pages if many[page] > few[page]}
    assert grows == {}


REPORTS = ["/reports/centre/", "/reports/maintenance/", "/reports/payments/", "/reports/arrears/",
           "/reports/utilities/", "/reports/leases/", "/reports/behaviour/", "/reports/scorecard/",
           "/reports/performance/", "/reports/deposits/", "/reports/delivery/", "/reports/income/", "/reports/profit-loss/",
           "/reports/cash-flow/", "/reports/fees/", "/reports/units/", "/reports/receivables/"]


def test_no_report_asks_once_per_row(client):
    owner = make_org()
    client.force_login(owner.user)
    prop = make_property(owner.organization, "Mvule Court")
    for n in (1, 2):
        scale.add_lease(owner, prop, n)
    few = _queries(client, REPORTS)
    for n in range(3, 9):
        scale.add_lease(owner, prop, n)
    many = _queries(client, REPORTS)
    grows = {page: (few[page], many[page]) for page in REPORTS if many[page] > few[page]}
    assert grows == {}


def test_scale_test_builds_times_and_undoes(settings):
    from io import StringIO

    from django.core.management import call_command

    from leases.models import Lease

    settings.DEBUG = True
    before = Lease.objects.count()
    out = StringIO()
    call_command("scale_test", leases=3, slow_ms=60_000, stdout=out)
    assert "rolled back" in out.getvalue() and "/billing/arrears/" in out.getvalue()
    assert Lease.objects.count() == before
