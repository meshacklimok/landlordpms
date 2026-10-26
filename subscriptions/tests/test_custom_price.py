"""One landlord's own subscription price over the plan's (owner only, audited)."""

import datetime
from decimal import Decimal

import pytest
from django.core.exceptions import PermissionDenied, ValidationError

from accounts.tests.factories import make_org, make_user
from audit.models import AuditEvent
from subscriptions import services
from subscriptions.models import Plan, Subscription

pytestmark = pytest.mark.django_db

TODAY = datetime.date(2026, 3, 10)


def paid_sub(org):
    plan = Plan.objects.filter(monthly_price__gt=0, yearly_price__gt=0, is_active=True).first()
    Subscription.objects.filter(organization=org).update(plan=plan)
    return Subscription.objects.get(organization=org), plan


def test_an_invoice_uses_the_landlords_own_price_and_other_landlords_keep_the_plan_price():
    owner = make_user(is_staff=True, is_superuser=True)
    a, b = make_org().organization, make_org().organization
    sub_a, plan = paid_sub(a)
    sub_b, _plan = paid_sub(b)
    services.set_custom_prices(owner, a, Decimal("1234"), None, "Agreed on a call")
    sub_a.refresh_from_db()
    invoice = services.issue_invoice(sub_a, plan, "MONTHLY", reason="test", today=TODAY)
    assert invoice.price == Decimal("1234.00")
    yearly = services.issue_invoice(sub_a, plan, "YEARLY", reason="test", today=TODAY)
    assert yearly.price == plan.yearly_price  # no own yearly price set
    other = services.issue_invoice(sub_b, plan, "MONTHLY", reason="test", today=TODAY)
    assert other.price == plan.monthly_price
    assert AuditEvent.objects.filter(action="console.custom_price", organization=a).count() == 1


def test_the_own_price_follows_the_plan_it_was_set_for_and_can_be_cleared():
    owner = make_user(is_staff=True, is_superuser=True)
    org = make_org().organization
    sub, plan = paid_sub(org)
    services.set_custom_prices(owner, org, Decimal("500"), Decimal("5000"), "Pilot landlord")
    sub.refresh_from_db()
    other = Plan.objects.exclude(pk=plan.pk).filter(monthly_price__gt=0).first()
    assert sub.price_for(plan, "MONTHLY") == Decimal("500.00")
    assert sub.price_for(other, "MONTHLY") == other.monthly_price
    services.set_custom_prices(owner, org, None, None, "Back to the plan price")
    sub.refresh_from_db()
    assert sub.price_for(plan, "MONTHLY") == plan.monthly_price


def test_only_an_owner_with_a_reason_and_a_sensible_price_can_set_it():
    org = make_org().organization
    owner = make_user(is_staff=True, is_superuser=True)
    with pytest.raises(PermissionDenied):
        services.set_custom_prices(make_user(is_staff=True), org, Decimal("100"), None, "a good reason")
    with pytest.raises(ValidationError):
        services.set_custom_prices(owner, org, Decimal("100"), None, "no")
    with pytest.raises(ValidationError):
        services.set_custom_prices(owner, org, Decimal("-1"), None, "a good reason")
    assert Subscription.objects.get(organization=org).custom_monthly_price is None


def test_the_owner_sets_a_landlords_price_in_the_console_and_the_landlord_sees_it(client):
    from django.urls import reverse

    from console.tests.test_console import signed_in

    org = make_org().organization
    paid_sub(org)
    signed_in(client, make_user(is_staff=True, is_superuser=True))
    url = reverse("console:organization", args=[org.public_id])
    assert b"This landlord's price" in client.get(url).content
    post = reverse("console:organization_custom_price", args=[org.public_id])
    r = client.post(post, {"monthly": "2500", "yearly": "", "reason": "Agreed by phone"})
    assert r.status_code == 302
    sub = Subscription.objects.get(organization=org)
    assert (sub.custom_monthly_price, sub.custom_yearly_price) == (Decimal("2500.00"), None)
    assert b'value="2500.00"' in client.get(url).content
    bad = client.post(post, {"monthly": "-5", "reason": "Agreed by phone"})
    assert bad.status_code == 302 and Subscription.objects.get(organization=org).custom_monthly_price == Decimal("2500.00")
    cleared = client.post(post, {"monthly": "", "yearly": "", "reason": "Back to normal"})
    assert cleared.status_code == 302 and Subscription.objects.get(organization=org).custom_monthly_price is None
