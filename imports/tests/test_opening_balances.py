"""Opening balances by CSV: one row per lease, through set_opening_balance, undone by reversal (doc 14 A2)."""

import datetime
from decimal import Decimal

import pytest
from django.core.exceptions import PermissionDenied
from django.urls import reverse

from accounts.tests.factories import add_member, make_org, make_property
from accounts.tests.test_isolation import login
from billing import invoicing
from billing.models import LedgerEntry
from billing.tests.test_invoicing import make_lease
from imports import services
from imports.models import ImportBatch
from leases import services as lease_services

from .test_imports import csv_file

pytestmark = pytest.mark.django_db

BALANCES = ImportBatch.Kind.BALANCES


@pytest.fixture
def owner():
    return make_org()


@pytest.fixture
def prop(owner):
    return make_property(owner.organization)


@pytest.fixture
def leases(owner, prop):
    a1, a2, a3 = (make_lease(owner, prop, code=c) for c in ("A1", "A2", "A3"))
    lease_services.end_lease(owner, a3, ended_on=datetime.date(2026, 1, 20))
    return a1, a2, a3


def upload(owner, prop, rows):
    header = "Property,Unit,Lease,Balance,Date,Reason\n"
    text = header + "".join(f"{prop.code},{r}\n" for r in rows)
    return services.preview_import(owner, BALANCES, csv_file(text))


def errors(batch):
    return {r["line"]: r["errors"] for r in batch.rows if r["errors"]}


def test_preview_checks_every_row_and_changes_nothing(owner, prop, leases):
    a1, a2, a3 = leases
    batch = upload(owner, prop, [
        'A1,,"12,500",2026-01-31,Old arrears',
        "A2,,-3000,31/01/2026,Paid ahead",
        f"A3,{a3.number},800,2026-01-20,",
        "A1,,100,2026-01-31,",      # A1 twice in one file
        "A3,,800,2026-01-20,",      # ended, no lease number
        "B9,,800,2026-01-20,",      # no such unit
        "A2,,0,2026-01-31,",        # zero
        "A2,,lots,2026-01-31,",
        "A2,,100,31 Jan,",
    ])
    assert (batch.ok_count, batch.error_count) == (3, 6)
    assert set(errors(batch)) == {5, 6, 7, 8, 9, 10}
    assert "already has a balance" in errors(batch)[5][0]
    assert "no active lease" in errors(batch)[6][0]
    assert not LedgerEntry.objects.filter(kind=LedgerEntry.Kind.OPENING_BALANCE).exists()


def test_apply_sets_balances_and_undo_reverses_the_untouched_ones(owner, prop, leases):
    a1, a2, a3 = leases
    batch = upload(owner, prop, ["A1,,12500,2026-01-31,Old arrears", "A2,,-3000,2026-01-31,",
                                 f"A3,{a3.number},800,2026-01-20,"])
    batch = services.apply_import(owner, batch)
    assert batch.ok_count == 3
    assert invoicing.opening_balance(a1).amount == Decimal("12500.00")
    assert invoicing.opening_balance(a2).amount == Decimal("-3000.00")
    assert invoicing.opening_balance(a3).entry_date == datetime.date(2026, 1, 20)
    assert [r["label"] for r in batch.rows][0] == f"{a1.unit.payment_reference} · {a1.number}"

    invoicing.set_opening_balance(owner, a2, amount=-2500, as_of=datetime.date(2026, 1, 31))  # fixed by hand
    removed, kept = services.undo_import(owner, batch)
    assert len(removed) == 2 and kept == [f"{a2.unit.payment_reference} · {a2.number}"]
    assert invoicing.opening_balance(a1) is None and invoicing.opening_balance(a3) is None
    assert invoicing.opening_balance(a2).amount == Decimal("-2500.00")
    assert LedgerEntry.objects.filter(lease=a1, kind=LedgerEntry.Kind.REVERSAL).count() == 1  # nothing deleted


def test_needs_invoices_adjust_and_stays_in_scope(owner, prop, leases):
    viewer = add_member(owner.organization, "viewer", all_properties=True)
    with pytest.raises(PermissionDenied):
        upload(viewer, prop, ["A1,,100,2026-01-31,"])
    other = add_member(owner.organization, "manager", properties=[make_property(owner.organization)])
    batch = upload(other, prop, ["A1,,100,2026-01-31,"])
    assert batch.ok_count == 0 and "No unit" in errors(batch)[2][0]


def test_pages(client, owner, prop, leases):
    login(client, owner)
    r = client.get(reverse("imports:list"))
    assert ("balances", BALANCES) in r.context["cards"]
    r = client.get(reverse("imports:template", args=["balances"]))
    assert r.content.decode().splitlines()[0] == ",".join(services.COLUMNS[BALANCES])
    r = client.post(reverse("imports:upload", args=["balances"]),
                    {"file": csv_file(f"property_code,unit_code,amount,as_of\n{prop.code},A1,500,2026-01-31\n")})
    batch = ImportBatch.objects.get(kind=BALANCES)
    assert r.status_code == 302 and batch.ok_count == 1
    assert client.get(reverse("imports:detail", args=[batch.public_id])).status_code == 200
