"""Who sees which payments (doc 13 scoping)."""

import pytest

from accounts.tests.factories import add_member, make_org, make_property
from billing.tests.test_invoicing import make_lease
from payments import selectors

from .test_services import pay

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _media(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path


def test_visibility_follows_capability_property_and_org():
    owner = make_org()
    org = owner.organization
    p1, p2 = make_property(org), make_property(org)
    here = pay(owner, make_lease(owner, p1, code="A1"), "100", reference="HERE")
    there = pay(owner, make_lease(owner, p2, code="B1"), "100")
    stranger = make_org()
    pay(stranger, make_lease(stranger, make_property(stranger.organization)), "100")

    assert set(selectors.visible_payments(owner)) == {here, there}
    scoped = add_member(org, "accountant", properties=[p1])
    assert list(selectors.visible_payments(scoped)) == [here]
    assert not selectors.visible_payments(add_member(org, "caretaker", all_properties=True)).exists()
    assert list(selectors.filter_payments(selectors.visible_payments(owner), q="here")) == [here]
    assert list(selectors.filter_payments(selectors.visible_payments(owner), q=here.receipt.number)) == [here]
    assert not selectors.filter_payments(selectors.visible_payments(owner), status="pending").exists()


def test_review_queue_is_for_checkers_only():
    owner = make_org()
    org = owner.organization
    lease = make_lease(owner, make_property(org))
    accountant = add_member(org, "accountant", all_properties=True)
    pending = pay(accountant, lease, "100")
    pay(owner, lease, "100")
    assert list(selectors.pending_review(owner)) == [pending]
    assert not selectors.pending_review(accountant).exists()
    manager = add_member(org, "manager", all_properties=True)
    assert list(selectors.pending_review(manager)) == [pending]
