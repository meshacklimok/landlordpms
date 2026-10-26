"""The heavy report figures are reused until the organization's data changes (D-135)."""


import pytest

from accounts.tests.factories import make_org, make_property
from core import reportcache
from ops import scale
from payments.models import Payment

pytestmark = pytest.mark.django_db

PAGES = ["/reports/", "/reports/profit-loss/", "/reports/cash-flow/", "/reports/units/"]


def _setup(client):
    owner = make_org()
    client.force_login(owner.user)
    prop = make_property(owner.organization, "Mvule Court")
    for n in (1, 2, 3):
        scale.add_lease(owner, prop, n)
    return owner, prop


def test_second_load_asks_the_database_less(client):
    _setup(client)
    first = {r["page"]: r for r in scale.time_pages(client, PAGES)}
    second = {r["page"]: r for r in scale.time_pages(client, PAGES)}
    assert [p for p, r in second.items() if r["status"] != 200] == []
    # Cash flow shares profit and loss's figures, so its first load may already be cheap.
    assert [p for p in PAGES if second[p]["queries"] > first[p]["queries"]] == []
    assert [p for p in PAGES if p != "/reports/cash-flow/" and second[p]["queries"] >= first[p]["queries"]] == []


def test_saving_a_payment_makes_the_next_load_fresh(client, django_capture_on_commit_callbacks, monkeypatch):
    owner, _prop = _setup(client)
    monkeypatch.setattr(reportcache, "BUMP_AT_COMMIT", True)
    before = reportcache.version(owner.organization_id)
    payment = Payment.objects.filter(organization=owner.organization).first()
    with django_capture_on_commit_callbacks(execute=True):
        payment.save()
    assert reportcache.version(owner.organization_id) == before + 1
    assert reportcache.version(owner.organization_id + 999) == 0  # other organizations are untouched


def test_figures_are_kept_per_member_and_per_data_version(client):
    owner, _prop = _setup(client)
    calls = []

    def build():
        calls.append(1)
        return {"n": len(calls)}

    m = owner
    assert reportcache.cached(m, "x", (1,), build) == {"n": 1}
    assert reportcache.cached(m, "x", (1,), build) == {"n": 1}  # reused
    assert reportcache.cached(m, "x", (2,), build) == {"n": 2}  # other filter
    reportcache.bump(m.organization_id)
    assert reportcache.cached(m, "x", (1,), build) == {"n": 3}  # data changed


def test_a_broken_cache_still_gives_the_report(client, monkeypatch):
    owner, _prop = _setup(client)

    def boom(*a, **k):
        raise RuntimeError("redis down")

    monkeypatch.setattr(reportcache.cache, "get", boom)
    monkeypatch.setattr(reportcache.cache, "set", boom)
    assert reportcache.cached(owner, "x", (), lambda: 5) == 5


def test_many_saves_in_one_transaction_bump_once(client, django_capture_on_commit_callbacks, monkeypatch):
    owner, _prop = _setup(client)
    monkeypatch.setattr(reportcache, "BUMP_AT_COMMIT", True)
    before = reportcache.version(owner.organization_id)
    payments = list(Payment.objects.filter(organization=owner.organization)[:3])
    with django_capture_on_commit_callbacks(execute=True) as callbacks:
        for payment in payments:
            payment.save()
    assert len(callbacks) == 1
    assert reportcache.version(owner.organization_id) == before + 1


def test_polled_rows_do_not_clear_the_figures(client, django_capture_on_commit_callbacks, monkeypatch):
    from mpesa.models import StkRequest

    owner, _prop = _setup(client)
    monkeypatch.setattr(reportcache, "BUMP_AT_COMMIT", True)
    with django_capture_on_commit_callbacks(execute=False) as callbacks:
        reportcache._changed(StkRequest, StkRequest(organization=owner.organization))
    assert callbacks == []


def test_while_redis_is_down_figures_are_kept_in_this_worker(client, monkeypatch):
    owner, _prop = _setup(client)

    def boom(*a, **k):
        raise RuntimeError("redis down")

    monkeypatch.setattr(reportcache.cache, "get", boom)
    monkeypatch.setattr(reportcache.cache, "set", boom)
    monkeypatch.setattr(reportcache.cache, "incr", boom)
    calls = []

    def build():
        calls.append(1)
        return len(calls)

    assert reportcache.cached(owner, "down", (), build) == 1
    assert reportcache.cached(owner, "down", (), build) == 1  # reused, not rebuilt
    reportcache.bump(owner.organization_id)
    assert reportcache.cached(owner, "down", (), build) == 2  # a save here still shows
