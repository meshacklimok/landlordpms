"""What a new organization's SMS starts with, the free welcome credit, and who may use their own SMS account or
sender name (D-200)."""

from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse

from accounts import sender_ids
from accounts.models import Organization
from accounts.tests.factories import make_org, make_user
from accounts.tests.test_isolation import login
from notifications import own_accounts
from notifications.providers.sms import MemorySmsSender, send_sms
from subscriptions import services
from subscriptions.models import SmsPolicy, SmsWallet, SmsWalletEntry

pytestmark = pytest.mark.django_db


@pytest.fixture
def owner():
    return make_org("Kamau Homes")


@pytest.fixture
def admin():
    return make_user(is_staff=True, is_superuser=True)


def policy(**kw):
    p = SmsPolicy.get()
    for k, v in kw.items():
        setattr(p, k, v)
    p.save()
    return p


def test_default_policy_keeps_todays_behaviour(owner):
    wallet = services.ensure_wallet(owner.organization)
    assert wallet.mode == SmsWallet.Mode.SPONSORED and wallet.monthly_cap == 300 and wallet.balance == 0
    assert not SmsWalletEntry.objects.filter(organization=owner.organization).exists()
    assert services.own_account_allowed(owner.organization) and services.own_sender_allowed(owner.organization)


def test_prepaid_with_free_welcome_sms(owner, settings):
    settings.SMS_WALLET_ENFORCED, settings.SMS_PRICE = True, "1.00"
    policy(new_mode=SmsWallet.Mode.PREPAID, welcome_parts=100)
    org = owner.organization
    # No wallet yet, but the 100 free SMS are there when the first one is checked.
    assert services.sms_available(org)
    wallet = SmsWallet.objects.get(organization=org)
    entry = SmsWalletEntry.objects.get(organization=org)
    assert wallet.mode == SmsWallet.Mode.PREPAID and wallet.balance == Decimal("100.00")
    assert entry.kind == SmsWalletEntry.Kind.WELCOME and entry.amount == Decimal("100.00")
    assert services.sms_summary(org)["balance"] == Decimal("100.00")
    # Asking again never gives it twice.
    services.ensure_wallet(org)
    assert SmsWalletEntry.objects.filter(organization=org).count() == 1


def test_welcome_credit_is_worth_parts_at_the_sms_price(owner, settings):
    settings.SMS_PRICE = "1.50"
    policy(new_mode=SmsWallet.Mode.BUNDLED, new_monthly_parts=0, welcome_parts=10)
    assert services.ensure_wallet(owner.organization).balance == Decimal("15.00")


def test_a_sponsored_start_gets_no_welcome_credit(owner):
    policy(welcome_parts=100)
    assert services.ensure_wallet(owner.organization).balance == 0


def test_existing_wallets_keep_their_terms(owner):
    org = owner.organization
    services.ensure_wallet(org)
    policy(new_mode=SmsWallet.Mode.PREPAID, welcome_parts=100)
    wallet = services.ensure_wallet(org)
    assert wallet.mode == SmsWallet.Mode.SPONSORED and wallet.balance == 0


def test_a_new_prepaid_organization_without_welcome_must_top_up(owner, settings):
    settings.SMS_WALLET_ENFORCED = True
    policy(new_mode=SmsWallet.Mode.PREPAID, welcome_parts=0)
    assert not services.sms_available(owner.organization)


# ---------------------------------------------------------------------------
# Own account and own sender name: on or off for everyone, or for one organization
# ---------------------------------------------------------------------------


def test_policy_turns_the_own_account_off_for_everyone(owner):
    policy(own_account_default=False)
    with pytest.raises(ValidationError):
        own_accounts.save_sms(owner, username="acme", api_key="key-123456")


def test_one_organization_can_be_allowed_when_the_policy_says_no(owner):
    policy(own_account_default=False, own_sender_default=False)
    wallet = services.ensure_wallet(owner.organization)
    wallet.own_account, wallet.own_sender = SmsWallet.Access.ALLOWED, SmsWallet.Access.ALLOWED
    wallet.save()
    own_accounts.save_sms(owner, username="acme", api_key="key-123456")
    sender_ids.request_name(owner, "KAMAUHOMES")


def test_one_organization_can_be_blocked_when_the_policy_says_yes(owner):
    wallet = services.ensure_wallet(owner.organization)
    wallet.own_account, wallet.own_sender = SmsWallet.Access.BLOCKED, SmsWallet.Access.BLOCKED
    wallet.save()
    with pytest.raises(ValidationError):
        own_accounts.save_sms(owner, username="acme", api_key="key-123456")
    with pytest.raises(ValidationError):
        sender_ids.request_name(owner, "KAMAUHOMES")


def test_switching_the_own_account_off_stops_using_it_but_keeps_the_details(owner):
    account = own_accounts.save_sms(owner, username="acme", api_key="key-123456")
    assert own_accounts.usable_sms(owner.organization) is not None
    wallet = services.ensure_wallet(owner.organization)
    wallet.own_account = SmsWallet.Access.BLOCKED
    wallet.save()
    assert own_accounts.usable_sms(owner.organization) is None
    assert own_accounts.sms_account(owner.organization).pk == account.pk
    wallet.own_account = SmsWallet.Access.DEFAULT
    wallet.save()
    assert own_accounts.usable_sms(owner.organization) is not None


def test_an_approved_sender_name_is_not_used_once_switched_off(owner, admin):
    sender_ids.request_name(owner, "KAMAUHOMES")
    org = Organization.objects.get(pk=owner.organization.pk)
    sender_ids.decide(admin, org, True)
    org.refresh_from_db()
    send_sms("+254712345678", "Rent is due", org=org)
    assert MemorySmsSender.senders[-1] == "KAMAUHOMES"
    policy(own_sender_default=False)
    send_sms("+254712345678", "Rent is due", org=org)
    assert MemorySmsSender.senders[-1] == ""


def test_the_pages_say_so_when_it_is_switched_off(client, owner):
    policy(own_account_default=False, own_sender_default=False)
    login(client, owner)
    page = client.get(reverse("notifications:sms"))
    assert page.status_code == 200 and b"not switched on for your organization" in page.content
    assert b'name="api_key"' not in page.content
    sub = client.get(reverse("subscriptions:page"))
    assert b'id="sender-name"' not in sub.content


def test_the_policy_and_the_wallet_permissions_are_editable_in_admin(client, admin, owner, monkeypatch):
    from accounts import mfa
    from accounts.tests.factories import PASSWORD

    monkeypatch.setattr(mfa, "is_enabled", lambda user: True)
    monkeypatch.setattr(mfa, "session_verified", lambda request: True)
    client.login(username=admin.phone, password=PASSWORD)
    assert client.get(reverse("admin:subscriptions_smspolicy_changelist")).status_code == 200
    services.ensure_wallet(owner.organization)
    page = client.get(reverse("admin:subscriptions_smswallet_changelist"))
    assert page.status_code == 200 and b"own_account" in page.content


# ---------------------------------------------------------------------------
# Loopholes closed
# ---------------------------------------------------------------------------


def test_the_welcome_credit_is_once_per_person_not_per_organization(owner, settings):
    settings.SMS_PRICE = "1.00"
    policy(new_mode=SmsWallet.Mode.PREPAID, welcome_parts=100)
    assert services.ensure_wallet(owner.organization).balance == Decimal("100.00")
    # The same user starts a second organization: no second gift.
    second = make_org("Second Homes", owner=owner.user)
    assert services.ensure_wallet(second.organization).balance == 0
    assert SmsWalletEntry.objects.filter(kind=SmsWalletEntry.Kind.WELCOME).count() == 1
    # Someone else gets theirs.
    other = make_org("Other Homes")
    assert services.ensure_wallet(other.organization).balance == Decimal("100.00")


def test_invitations_and_signing_links_use_the_wallet(owner, settings):
    from notifications import delivery

    settings.SMS_WALLET_ENFORCED, settings.SMS_PRICE = True, "1.00"
    org = owner.organization
    SmsWallet.objects.update_or_create(organization=org, defaults={"mode": SmsWallet.Mode.PREPAID})
    # No credit: refused, so the caller can use email.
    result = delivery.send_sms_for(org, "+254712345678", "Open this link", what="Portal invitation")
    assert not result.ok and "credit" in result.error
    services.apply_sms_top_up(org, amount=Decimal("100"), method="MPESA", reference="TOPX1",
                              paid_on=__import__("datetime").date.today())
    assert delivery.send_sms_for(org, "+254712345678", "x" * 200, what="Portal invitation").ok
    entry = SmsWalletEntry.objects.filter(kind=SmsWalletEntry.Kind.MESSAGE).get()
    assert entry.amount == Decimal("-2.00") and entry.segments == 2 and entry.note == "Portal invitation"
    assert services.sms_balance(org) == Decimal("98.00")


def test_a_prepaid_organization_needs_a_whole_part_to_start_a_message(owner, settings):
    settings.SMS_WALLET_ENFORCED, settings.SMS_PRICE = True, "1.00"
    wallet = services.ensure_wallet(owner.organization)
    wallet.mode, wallet.balance = SmsWallet.Mode.PREPAID, Decimal("0.40")
    wallet.save()
    assert not services.sms_available(owner.organization)


def test_a_signing_code_is_charged_but_never_blocked(owner, settings):
    from notifications import delivery

    settings.SMS_WALLET_ENFORCED, settings.SMS_PRICE = True, "1.00"
    org = owner.organization
    SmsWallet.objects.update_or_create(organization=org, defaults={"mode": SmsWallet.Mode.PREPAID})
    assert delivery.send_sms_for(org, "+254712345678", "Your code is 123456", what="Signing code",
                                 block_when_empty=False).ok
    entry = SmsWalletEntry.objects.filter(kind=SmsWalletEntry.Kind.MESSAGE).get()
    assert entry.amount == Decimal("-1.00") and services.sms_balance(org) == Decimal("-1.00")


def test_welcome_credit_can_wait_for_the_first_top_up(owner, settings):
    import datetime

    settings.SMS_PRICE = "1.00"
    policy(new_mode=SmsWallet.Mode.PREPAID, welcome_parts=100, welcome_when=SmsPolicy.WelcomeWhen.FIRST_TOP_UP)
    org = owner.organization
    assert services.ensure_wallet(org).balance == 0  # a made-up account gets nothing
    services.apply_sms_top_up(org, amount=Decimal("200"), method="MPESA", reference="FIRST1",
                              paid_on=datetime.date.today())
    assert services.sms_balance(org) == Decimal("300.00")
    services.apply_sms_top_up(org, amount=Decimal("200"), method="MPESA", reference="SECOND1",
                              paid_on=datetime.date.today())
    assert services.sms_balance(org) == Decimal("500.00")  # the bonus came once
    # The same person's second organization gets no bonus on its first top-up.
    second = make_org("Second Homes", owner=owner.user)
    services.apply_sms_top_up(second.organization, amount=Decimal("200"), method="MPESA", reference="THIRD1",
                              paid_on=datetime.date.today())
    assert services.sms_balance(second.organization) == Decimal("200.00")
