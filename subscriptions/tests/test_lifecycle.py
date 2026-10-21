"""The whole unpaid-subscription path in one go: trial ends, past due, lapsed (read-only), paid again.

At every step it checks what the owner and staff see (banner), what is sent and to whom, what staff may still do,
and what the Platform Admin sees on the chase list. The pieces have their own tests in test_billing.py."""

import datetime

import pytest
from django.core import mail
from django.core.exceptions import PermissionDenied, ValidationError
from django.urls import reverse

from accounts.models import Organization
from accounts.tests.factories import add_member, fresh, make_org, make_property, make_user, role
from accounts.tests.test_isolation import login
from console import chase
from notifications.models import Message
from properties import services as property_services
from subscriptions import services
from subscriptions.models import BillingPolicy, Subscription

pytestmark = pytest.mark.django_db

Status = Subscription.Status
TODAY = datetime.date(2026, 3, 10)


def day(n):
    return TODAY + datetime.timedelta(days=n)


def told(org, *, kind=None):
    rows = Message.objects.filter(organization=org, type="subscription_access")
    if kind:
        rows = rows.filter(body__icontains=kind)
    return {m.user_id for m in rows if m.user_id}


def test_the_whole_lapse_and_recovery_sequence(client, django_capture_on_commit_callbacks):
    BillingPolicy.objects.update_or_create(pk=BillingPolicy.get().pk, defaults={f"stop_{k}_days": None for k in services.SECTIONS})  # this test is about lapsing, not paused sections (D-201)
    boss = make_org(owner=make_user(email="boss@example.co.ke", full_name="Wanjiru Boss"))
    org = boss.organization
    manager = add_member(org, "manager", all_properties=True)
    accountant = add_member(org, "accountant", all_properties=True)
    prop = make_property(org)
    admin = make_user(is_staff=True, is_superuser=True)
    for i in range(6):  # more than Free holds, so the trial must turn into a paid plan
        property_services.create_unit(boss, prop, code=f"U{i}")
    # The owner chooses to tell the manager as well, and not the accountant.
    assert services.set_lapse_notify_roles(boss, [role(org, "manager").public_id]) == ["Manager"]

    # 1. The trial ends: past due with an open invoice, a warning, the grace clock, and a place on the chase list.
    Subscription.objects.filter(organization=org).update(trial_ends_on=day(-1))
    assert services.daily(TODAY)["trial_ended"] == 1
    sub = Subscription.objects.get(organization=org)
    invoice = services.open_invoice(sub)
    assert sub.status == Status.PAST_DUE and sub.past_due_since == TODAY and invoice is not None
    banner = services.banner(org, TODAY)
    assert banner["level"] == "warning" and "14 days" in str(banner["text"])
    [row] = chase.rows(TODAY)
    assert (row["org"], row["lapsed"], row["left"], row["amount"]) == (org, False, 14, invoice.total)
    assert row["name"] == "Wanjiru Boss" and row["whatsapp"].startswith("https://wa.me/254")
    assert not Message.objects.filter(type="subscription_access").exists()  # nobody is told yet

    # 2. During the grace: the owner is emailed on the reminder days; everyone still works normally.
    reminder_day = services.policy().grace_reminder_list[0]
    mail.outbox.clear()
    assert services.send_reminders(day(reminder_day)) == 2  # the email and the SMS (D-198)
    assert mail.outbox[-1].to == ["boss@example.co.ke"] and "overdue" in mail.outbox[-1].subject
    assert services.daily(day(13))["lapsed"] == 0
    assert Organization.objects.get(pk=org.pk).status == Organization.Status.ACTIVE
    property_services.create_unit(fresh(manager), prop, code="STILL-OK")
    login(client, manager)
    assert client.get(reverse("accounts:home")).status_code == 200

    # 3. The grace runs out: read-only, banner, writes refused, and the right people are told in the app.
    with django_capture_on_commit_callbacks(execute=True):
        assert services.daily(day(14))["lapsed"] == 1
    org.refresh_from_db()
    assert org.status == Organization.Status.READ_ONLY
    assert services.banner(org, day(14))["level"] == "danger"
    assert "subscription has lapsed" in client.get(reverse("accounts:home")).content.decode()
    with pytest.raises(PermissionDenied):
        property_services.create_unit(fresh(manager), prop, code="BLOCKED")
    assert told(org, kind="lapsed") == {boss.user_id, manager.user_id}  # owner and the chosen role, not the accountant
    assert accountant.user_id not in told(org)
    assert not Message.objects.filter(type="subscription_access", tenant__isnull=False).exists()
    [row] = chase.rows(day(14))
    assert row["lapsed"] is True and row["left"] is None and row["overdue"] == 14
    # They can still read everything and see how to pay.
    login(client, boss)
    assert client.get(reverse("subscriptions:page")).status_code == 200
    assert client.get(reverse("properties:list")).status_code == 200

    # 4. Paid: open again at once, everyone who was told of the lapse hears it is over, the chase list empties.
    with django_capture_on_commit_callbacks(execute=True):
        services.record_payment(admin, invoice, amount=invoice.total, method="MPESA", reference="QLIFE1",
                                paid_on=day(15))
    org.refresh_from_db()
    sub.refresh_from_db()
    assert org.status == Organization.Status.ACTIVE and sub.status == Status.ACTIVE
    assert services.banner(org, day(15)) is None
    property_services.create_unit(fresh(manager), prop, code="BACK")
    assert told(org, kind="is paid") == {boss.user_id, manager.user_id}
    assert chase.rows(day(15)) == []


def test_only_owner_roles_are_told_when_no_role_is_chosen(django_capture_on_commit_callbacks):
    boss = make_org()
    manager = add_member(boss.organization, "manager", all_properties=True)
    with django_capture_on_commit_callbacks(execute=True):
        services._set_org_read_only(boss.organization)
    assert told(boss.organization) == {boss.user_id}
    assert manager.user_id not in told(boss.organization)


def test_a_role_can_be_chosen_only_from_the_organization_s_own_non_owner_roles():
    boss, other = make_org(), make_org()
    for bad in (role(boss.organization, "owner"), role(other.organization, "manager")):
        with pytest.raises(ValidationError):
            services.set_lapse_notify_roles(boss, [bad.public_id])
    with pytest.raises(PermissionDenied):
        services.set_lapse_notify_roles(add_member(boss.organization, "viewer"), [])


def test_the_owner_picks_roles_on_the_subscription_page(client):
    boss = make_org()
    manager_role = role(boss.organization, "manager")
    login(client, boss)
    page = client.get(reverse("subscriptions:page")).content.decode()
    assert "Who is told if the subscription lapses" in page and str(manager_role.public_id) in page
    r = client.post(reverse("subscriptions:lapse_notify"), {"roles": [str(manager_role.public_id)]})
    assert r.status_code == 302
    assert list(boss.organization.lapse_notify_roles.all()) == [manager_role]
    client.post(reverse("subscriptions:lapse_notify"), {})
    assert not boss.organization.lapse_notify_roles.exists()


def test_the_chase_list_puts_the_soonest_to_lapse_first_and_lapsed_last():
    soon, late, gone = (make_org(f"Org {n}") for n in "ABC")
    for membership, status, since in ((soon, Status.PAST_DUE, day(-12)), (late, Status.PAST_DUE, day(-2)),
                                      (gone, Status.LAPSED, day(-30))):
        Subscription.objects.filter(organization=membership.organization).update(status=status, past_due_since=since)
    rows = chase.rows(TODAY)
    assert [r["org"].name for r in rows] == ["Org A", "Org B", "Org C"]
    assert [r["left"] for r in rows] == [2, 12, None]


def test_an_extension_and_a_dispute_show_on_the_chase_list():
    boss = make_org()
    Subscription.objects.filter(organization=boss.organization).update(
        status=Status.PAST_DUE, past_due_since=day(-12), grace_until=day(20))
    [row] = chase.rows(TODAY)
    assert row["left"] == 20 and row["extended_to"] == day(20)


def test_whatsapp_links_take_kenyan_numbers_in_any_form():
    for number in ("0722000111", "+254722000111", "254722000111", "722000111", "0722 000 111"):
        assert chase.whatsapp_link(number, "Hi there").startswith("https://wa.me/254722000111?text=Hi%20there")
    assert chase.whatsapp_link("", "Hi") == "" and chase.whatsapp_link("12345", "Hi") == ""


def test_paying_online_unlocks_the_open_browser_session_at_once(client, django_capture_on_commit_callbacks):
    """The landlord pays through M-Pesa or Paystack (no admin), and the page they already have open works again."""
    boss = make_org()
    org = boss.organization
    prop = make_property(org)
    for i in range(6):
        property_services.create_unit(boss, prop, code=f"U{i}")
    Subscription.objects.filter(organization=org).update(trial_ends_on=day(-1))
    services.daily(TODAY)
    invoice = services.open_invoice(Subscription.objects.get(organization=org))
    with django_capture_on_commit_callbacks(execute=True):
        services.daily(day(14))
    login(client, boss)
    new_unit = reverse("properties:unit_create", args=[prop.public_id])
    assert client.get(new_unit).status_code == 403
    assert "subscription has lapsed" in client.get(reverse("accounts:home")).content.decode()

    with django_capture_on_commit_callbacks(execute=True):
        services.apply_payment(invoice, amount=invoice.total, method="MPESA", reference="QONLINE1", paid_on=day(15))

    # Same session, no new login.
    assert client.get(new_unit).status_code == 200
    home = client.get(reverse("accounts:home")).content.decode()
    assert "subscription has lapsed" not in home
    assert client.get(reverse("reports:dashboard")).status_code == 200
    org.refresh_from_db()
    assert org.status == Organization.Status.ACTIVE
    # The same invoice cannot be paid twice.
    with pytest.raises(ValidationError):
        services.apply_payment(invoice, amount=invoice.total, method="MPESA", reference="QONLINE2", paid_on=day(15))
