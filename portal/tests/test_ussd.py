"""The USSD menu for tenants on feature phones (D-081)."""

import datetime

import pytest
from django.core.cache import cache
from django.core.exceptions import PermissionDenied, ValidationError
from django.urls import reverse
from django.utils import timezone

from accounts.models import Organization
from accounts.tests.factories import make_org, make_property
from accounts.tests.test_isolation import login
from audit.models import AuditEvent
from billing.tests.test_invoicing import FEB, MAR, bill
from leases import services as lease_services
from maintenance import services as maintenance_services
from maintenance.models import MaintenanceRequest
from mpesa import services as mpesa_services
from mpesa.daraja import FakeDarajaClient
from mpesa.models import StkRequest
from notifications.models import Message
from payments import services as payment_services
from payments.models import Payment, PaymentAccount, PropertyPaymentAccount
from portal import ussd
from portal.models import UssdPin
from properties import services as property_services
from tenants import services as tenant_services

pytestmark = pytest.mark.django_db

D = datetime.date
TOKEN = "t" * 32
PHONE = "+254711000111"


@pytest.fixture(autouse=True)
def _setup(settings):
    settings.AT_CALLBACK_TOKEN = TOKEN
    cache.clear()


@pytest.fixture
def owner():
    return make_org(name="Acme")


def lease_for(owner, prop, code, phone=PHONE, *, activate=True, **tenant_fields):
    unit = property_services.create_unit(owner, prop, code=code)
    tenant = tenant_services.create_tenant(owner, name=f"Tenant {code}", phone=phone, **tenant_fields)
    lease = lease_services.create_lease(owner, unit=unit, tenants=[tenant], start_date=D(2026, 1, 1),
                                        end_date=None, rent=15000)
    return lease_services.activate_lease(owner, lease) if activate else lease


def post(client, text="", phone=PHONE, token=TOKEN):
    url = reverse("hook_ussd", args=[token])
    return client.post(url, {"sessionId": "ATUid_1", "serviceCode": "*384*123#", "phoneNumber": phone,
                             "text": text})


def say(client, text="", phone=PHONE) -> str:
    response = post(client, text, phone)
    assert response.status_code == 200 and response["Content-Type"].startswith("text/plain")
    body = response.content.decode()
    assert body[:4] in ("CON ", "END ") and len(body) <= ussd.MAX_CHARS
    return body


def test_a_wrong_or_missing_token_is_a_404(client, settings):
    assert post(client, token="wrong").status_code == 404
    settings.AT_CALLBACK_TOKEN = ""
    assert post(client, token="x").status_code == 404
    assert client.get(reverse("hook_ussd", args=[TOKEN])).status_code in (404, 405)


def test_an_unknown_number_is_told_to_ask_the_landlord(client, owner):
    prop = make_property(owner.organization, name="Acacia")
    lease_for(owner, prop, "A1", alt_phone="+254722000222")
    assert say(client, phone="+254700000000").startswith("END This number is not registered")
    # The other phone does not count; nor does a number that is not a phone number.
    assert say(client, phone="+254722000222").startswith("END This number is not registered")
    assert say(client, phone="hello").startswith("END This number is not registered")


def test_one_lease_goes_straight_to_the_menu_and_shows_the_balance(client, owner):
    prop = make_property(owner.organization, name="Acacia")
    lease = lease_for(owner, prop, "A1")
    bill(lease, FEB)
    bill(lease, MAR)
    assert say(client) == ("CON Acacia A1\n1. Balance\n2. How to pay\n3. Last payment\n4. Report a repair\n"
                           "0. Set a PIN")
    # AT sends the number as +254...; a local form is matched too.
    assert say(client, phone="0711000111").startswith("CON Acacia A1")
    balance = say(client, "1")
    assert balance.startswith("END Acacia A1\nOwed: KES ")
    assert "Overdue: KES " in balance
    assert say(client, "9") == "END Invalid choice."
    assert say(client, "1*1") == "END Invalid choice."


def test_how_to_pay_shows_the_propertys_accounts_with_the_unit_reference(client, owner):
    prop = make_property(owner.organization, name="Acacia")
    lease = lease_for(owner, prop, "A1")
    assert say(client, "2") == "END No payment account is set up. Ask your landlord."
    org = owner.organization
    paybill = PaymentAccount.objects.create(organization=org, type=PaymentAccount.Type.PAYBILL, number="522522",
                                            display_name="Rent paybill")
    PaymentAccount.objects.create(organization=org, type=PaymentAccount.Type.CASH, display_name="Cash office")
    PaymentAccount.objects.create(organization=org, type=PaymentAccount.Type.TILL, number="99887",
                                  display_name="Shop till")
    PropertyPaymentAccount.objects.create(organization=org, property=prop, payment_account=paybill)
    reply = say(client, "2")
    assert reply == f"END Acacia A1\nPaybill 522522, Account {lease.unit.payment_reference}"


def test_last_payment_shows_the_receipt_number(client, owner):
    prop = make_property(owner.organization, name="Acacia")
    lease = lease_for(owner, prop, "A1")
    assert say(client, "3") == "END No payment recorded yet."
    bill(lease, FEB)
    payment_services.record_payment(owner, lease, amount=5000, method=Payment.Method.CASH, paid_at=D(2026, 2, 3))
    latest = payment_services.record_payment(owner, lease, amount=7000, method=Payment.Method.CASH,
                                             paid_at=D(2026, 2, 9))
    reply = say(client, "3")
    assert reply.startswith("END Acacia A1\nLast payment: KES 7,000.00 on 9 Feb 2026")
    latest.refresh_from_db()
    assert reply.endswith(f"\nReceipt: {latest.receipt.number}")
    assert "Nothing owed." not in say(client, "1")


def test_several_leases_are_listed_first_and_other_organizations_stay_apart(client, owner):
    prop = make_property(owner.organization, name="Acacia")
    lease_for(owner, prop, "A1")
    lease_for(owner, prop, "B2")
    lease_for(owner, prop, "C3", activate=False)  # draft: not issued
    other = make_org(name="Other")
    lease_for(other, make_property(other.organization, name="Baobab"), "Z9")
    assert say(client) == "CON Choose your house:\n1. Acacia A1\n2. Acacia B2\n3. Baobab Z9"
    assert say(client, "2").startswith("CON Acacia B2\n1. Balance")
    assert say(client, "3*1").startswith("END Baobab Z9\nNothing owed.")
    assert say(client, "4") == "END Invalid choice."
    # A frozen organization's leases drop out, and so does an archived lease.
    Organization.objects.filter(pk=other.organization_id).update(status=Organization.Status.FROZEN)
    assert "Baobab" not in say(client)
    lease_b = ussd.caller_leases(PHONE)[1][0]
    lease_b.archive(owner.user)
    assert say(client).startswith("CON Acacia A1\n1. Balance")


def test_the_menu_speaks_the_tenants_language(client, owner):
    prop = make_property(owner.organization, name="Acacia")
    lease_for(owner, prop, "A1", language="sw")
    assert say(client) == ("CON Acacia A1\n1. Salio\n2. Jinsi ya kulipa\n3. Malipo ya mwisho\n"
                           "4. Ripoti tatizo la matengenezo\n0. Weka PIN")
    assert say(client, "3") == "END Hakuna malipo yaliyorekodiwa bado."


def test_a_number_is_limited_to_30_steps_an_hour(client, owner):
    prop = make_property(owner.organization, name="Acacia")
    lease_for(owner, prop, "A1")
    for _ in range(ussd.STEPS_PER_HOUR):
        assert say(client).startswith("CON ")
    assert say(client) == "END Too many requests. Try again later."
    # Unknown numbers are not counted: they never get past the first check.
    assert say(client, phone="+254700000000").startswith("END This number")


def test_every_reply_fits_one_screen(owner):
    prop = make_property(owner.organization, name="A very long property name that goes on and on " * 3)
    lease_for(owner, prop, "A1")
    assert len(ussd.respond(PHONE, "")) <= ussd.MAX_CHARS


# ---------------------------------------------------------------------------
# Paying from the menu (D-084)
# ---------------------------------------------------------------------------


def make_paybill(owner, number="600123", passkey="pk"):
    account = mpesa_services.add_account(owner, type="PAYBILL", number=number, display_name=f"Paybill {number}")
    return mpesa_services.save_credentials(owner, account, environment="SANDBOX", consumer_key="k",
                                           consumer_secret="s", passkey=passkey)


@pytest.fixture
def paybill(owner, settings):
    settings.SITE_URL = "https://pms.example.com"
    return make_paybill(owner)


def test_pay_asks_the_amount_confirms_and_sends_the_prompt_after_the_reply(client, owner, paybill):
    prop = make_property(owner.organization, name="Acacia")
    lease = lease_for(owner, prop, "A1")
    bill(lease, FEB)
    assert say(client).endswith("3. Last payment\n4. Pay with M-Pesa\n5. Report a repair\n0. Set a PIN")
    assert say(client, "4").startswith("CON Owed: KES ") and say(client, "4").endswith("Enter amount (KES):")
    for bad in ("abc", "0", "10.50", "250001"):
        assert say(client, f"4*{bad}") == "END Enter a whole amount from 1 to 250,000."
    assert say(client, "4*5000") == "CON Pay KES 5,000.00 for Acacia A1?\n1. Confirm\n2. Cancel"
    assert say(client, "4*5000*2") == "END Cancelled."
    assert say(client, "4*5000*3") == "END Invalid choice."
    # Working out the reply sends nothing; the prompt goes once the reply has been sent.
    assert ussd.respond(PHONE, "4*5000*1").startswith("END You will get an M-Pesa prompt")
    assert not StkRequest.objects.exists() and FakeDarajaClient.calls == []
    assert say(client, "4*5000*1") == ("END You will get an M-Pesa prompt on this phone. "
                                       "Enter your M-Pesa PIN to pay.")
    stk_request = StkRequest.objects.get()
    assert stk_request.via_ussd and stk_request.requested_by is None and stk_request.pay_link is None
    assert (stk_request.lease, stk_request.phone, stk_request.amount) == (lease, PHONE, 5000)
    assert [path for _code, path, _body in FakeDarajaClient.calls] == ["/mpesa/stkpush/v1/processrequest"]
    assert AuditEvent.objects.get(action="mpesa.stk_request").changes["via"] == [None, "ussd"]


def test_pay_limits_a_prompt_every_2_minutes_and_5_a_day(client, owner, paybill):
    prop = make_property(owner.organization, name="Acacia")
    lease_for(owner, prop, "A1")
    say(client, "4*100*1")
    assert say(client, "4*100*1").startswith("END A prompt was sent to this phone just now")
    for _ in range(ussd.PROMPTS_PER_PHONE_DAY - 1):
        StkRequest.objects.update(status=StkRequest.Status.FAILED)
        say(client, "4*100*1")
    StkRequest.objects.update(status=StkRequest.Status.FAILED)
    assert StkRequest.objects.count() == ussd.PROMPTS_PER_PHONE_DAY
    assert say(client, "4*100*1").startswith("END This phone has had as many M-Pesa prompts")
    assert StkRequest.objects.count() == ussd.PROMPTS_PER_PHONE_DAY


def test_a_refused_prompt_is_kept_as_failed_and_the_session_still_ends(client, owner, paybill):
    prop = make_property(owner.organization, name="Acacia")
    lease_for(owner, prop, "A1")
    FakeDarajaClient.fail = "Invalid Access Token"
    assert say(client, "4*100*1").startswith("END You will get an M-Pesa prompt")
    assert StkRequest.objects.get().status == StkRequest.Status.FAILED


def test_pay_is_offered_only_when_the_lease_can_take_a_prompt(client, owner, settings):
    settings.SITE_URL = "https://pms.example.com"
    prop = make_property(owner.organization, name="Acacia")
    lease = lease_for(owner, prop, "A1")
    make_paybill(owner, passkey="")  # no passkey: no prompts
    assert "4. Pay" not in say(client) and say(client, "4").startswith("CON What is wrong?")
    make_paybill(owner, number="600124")
    assert "4. Pay with M-Pesa" in say(client)
    # An ended lease that owes nothing takes no payments, and no repairs either.
    lease_services.end_lease(owner, lease, ended_on=D(2026, 1, 31))
    assert say(client).endswith("3. Last payment\n0. Set a PIN") and say(client, "4") == "END Invalid choice."


def test_with_several_leases_the_prompt_is_for_the_chosen_one(client, owner, paybill):
    prop = make_property(owner.organization, name="Acacia")
    lease_for(owner, prop, "A1")
    second = lease_for(owner, prop, "B2")
    assert say(client, "2*4*700") == "CON Pay KES 700.00 for Acacia B2?\n1. Confirm\n2. Cancel"
    say(client, "2*4*700*1")
    assert StkRequest.objects.get().lease == second


# ---------------------------------------------------------------------------
# Reporting a repair (D-085)
# ---------------------------------------------------------------------------


def test_a_repair_is_reported_in_three_steps_and_the_staff_are_told(client, owner):
    prop = make_property(owner.organization, name="Acacia")
    lease = lease_for(owner, prop, "A1")
    assert say(client, "4") == ("CON What is wrong?\n1. Water or plumbing\n2. Electricity\n3. Doors and locks\n"
                                "4. Roof and walls\n5. Security\n6. Other")
    assert say(client, "4*7") == "END Invalid choice."
    assert say(client, "4*1") == "CON Describe it in a few words, or 0 to skip:"
    assert say(client, "4*1*Kitchen tap leaks") == "CON Is it an emergency?\n1. Yes\n2. No"
    assert say(client, "4*1*Kitchen tap leaks*3") == "END Invalid choice."
    assert say(client, "4*1*" + "x" * 101) == "END Invalid choice."
    assert not MaintenanceRequest.objects.exists()
    reply = say(client, "4*1*Kitchen tap leaks*2")
    req = MaintenanceRequest.objects.get()
    assert reply == f"END Thank you. Your repair number is {req.number}. We will send you updates by SMS."
    assert (req.source, req.kind, req.priority, req.title) == ("USSD", "PLUMBING", "NORMAL", "Kitchen tap leaks")
    assert (req.lease, req.tenant, req.unit, req.reported_by) == (lease, lease.primary_tenant, lease.unit, None)
    assert req.updates.get().by is None
    # The owner assigns repairs: they are told; the tenant gets the "received" message.
    staff = Message.objects.filter(type="maintenance_reported")
    assert {m.user for m in staff} == {owner.user} and all("Tenant A1, from USSD" in m.body for m in staff)
    assert Message.objects.filter(type="maintenance_update", tenant=req.tenant).exists()
    assert AuditEvent.objects.get(action="maintenance.report").changes["source"] == [None, "USSD"]


def test_the_staff_page_shows_a_ussd_report(client, owner):
    prop = make_property(owner.organization, name="Acacia")
    lease_for(owner, prop, "A1")
    say(client, "4*2*0*1")
    req = MaintenanceRequest.objects.get()
    assert (req.title, req.kind, req.priority) == ("Electrical", "ELECTRICAL", "EMERGENCY")
    login(client, owner)
    page = client.get(reverse("maintenance:detail", args=[req.public_id])).content.decode()
    assert "from USSD" in page and "Tenant, from USSD" in page


def test_a_repeated_last_step_gives_the_same_request_and_5_a_day_is_the_limit(client, owner):
    prop = make_property(owner.organization, name="Acacia")
    lease_for(owner, prop, "A1")
    first = say(client, "4*1*Tap*2")
    assert say(client, "4*1*Tap*2") == first and MaintenanceRequest.objects.count() == 1
    for n in range(2, 6):
        say(client, f"4*1*Tap {n}*2")
    assert MaintenanceRequest.objects.count() == 5
    assert say(client, "4*1*Tap 6*2") == "END You have reported 5 problems today. Call the office if there are more."
    assert MaintenanceRequest.objects.count() == 5


def test_with_several_leases_the_repair_is_for_the_chosen_one(client, owner):
    prop = make_property(owner.organization, name="Acacia")
    lease_for(owner, prop, "A1")
    second = lease_for(owner, prop, "B2")
    say(client, "2*4*6*0*2")
    assert MaintenanceRequest.objects.get().lease == second


def test_only_a_tenant_of_an_active_lease_can_report(owner):
    prop = make_property(owner.organization, name="Acacia")
    lease = lease_for(owner, prop, "A1")
    stranger = tenant_services.create_tenant(owner, name="Stranger", phone="+254700000009")
    with pytest.raises(PermissionDenied):
        maintenance_services.ussd_report(lease, stranger, kind="OTHER")
    with pytest.raises(ValidationError):
        maintenance_services.ussd_report(lease, lease.primary_tenant, kind="NOPE")


# ---------------------------------------------------------------------------
# The optional PIN (D-086)
# ---------------------------------------------------------------------------


def test_setting_a_pin_refuses_weak_or_mismatched_ones(client, owner):
    prop = make_property(owner.organization, name="Acacia")
    lease_for(owner, prop, "A1")
    assert say(client, "0") == "CON Enter a new 4-digit PIN:"
    for weak in ("1111", "1234", "6789", "4321", "123", "12345", "12a4"):
        assert say(client, f"0*{weak}") == "END Choose 4 digits that are not all the same or in a row."
    assert say(client, "0*1357") == "CON Enter the PIN again:"
    assert say(client, "0*1357*1358") == "END The PINs did not match. Nothing changed."
    assert not UssdPin.objects.exists()
    assert say(client, "0*1357*1357") == "END PIN set. You will be asked for it every time."
    record = UssdPin.objects.get()
    assert record.phone == PHONE and "1357" not in record.pin_hash and record.pin_hash == ussd.pin_hash(PHONE, "1357")


def test_with_a_pin_every_step_needs_it(client, owner):
    prop = make_property(owner.organization, name="Acacia")
    lease_for(owner, prop, "A1")
    say(client, "0*1357*1357")
    assert say(client) == "CON Enter your PIN:"
    assert say(client, "2468") == "END Wrong PIN."
    assert say(client, "1357").startswith("CON Acacia A1\n1. Balance") and say(client, "1357").endswith("\n0. PIN")
    assert say(client, "1357*3") == "END No payment recorded yet."
    # Without the PIN in front the menu choice is read as a PIN.
    assert say(client, "3") == "END Wrong PIN."
    say(client, "1357*4*6*0*2")
    assert MaintenanceRequest.objects.get().source == "USSD"


def test_with_several_leases_the_pin_comes_before_the_house(client, owner):
    prop = make_property(owner.organization, name="Acacia")
    lease_for(owner, prop, "A1")
    lease_for(owner, prop, "B2")
    say(client, "1*0*1357*1357")
    assert say(client) == "CON Enter your PIN:"
    assert say(client, "1357") == "CON Choose your house:\n1. Acacia A1\n2. Acacia B2"
    assert say(client, "1357*2*1").startswith("END Acacia B2")


def test_five_wrong_pins_lock_the_phone_for_an_hour(client, owner):
    prop = make_property(owner.organization, name="Acacia")
    lease_for(owner, prop, "A1")
    say(client, "0*1357*1357")
    for _ in range(ussd.PIN_TRIES - 1):
        assert say(client, "0000") == "END Wrong PIN."
    assert say(client, "1357").startswith("CON Acacia A1")  # a right PIN resets the count
    assert UssdPin.objects.get().failures == 0
    for _ in range(ussd.PIN_TRIES - 1):
        say(client, "0000")
    locked = "END Too many wrong PINs. Try again in an hour, or ask your landlord to remove the PIN."
    assert say(client, "0000") == locked
    assert say(client, "1357") == locked and say(client) == locked
    UssdPin.objects.update(locked_until=timezone.now() - datetime.timedelta(seconds=1))
    assert say(client, "1357").startswith("CON Acacia A1")


def test_the_pin_can_be_changed_or_removed_from_the_menu(client, owner):
    prop = make_property(owner.organization, name="Acacia")
    lease_for(owner, prop, "A1")
    say(client, "0*1357*1357")
    assert say(client, "1357*0") == "CON PIN\n1. Change PIN\n2. Remove PIN"
    assert say(client, "1357*0*1") == "CON Enter a new 4-digit PIN:"
    assert say(client, "1357*0*1*2580*2580") == "END PIN changed."
    assert say(client, "1357") == "END Wrong PIN."
    assert say(client, "2580*0*3") == "END Invalid choice."
    assert say(client, "2580*0*2") == "END PIN removed. The menu will not ask for it."
    assert not UssdPin.objects.exists() and say(client).startswith("CON Acacia A1")


def test_staff_see_the_pin_and_can_remove_it(client, owner):
    prop = make_property(owner.organization, name="Acacia")
    lease = lease_for(owner, prop, "A1")
    tenant = lease.primary_tenant
    login(client, owner)
    page = reverse("tenants:detail", args=[tenant.public_id])
    assert "data-ussd-pin" not in client.get(page).content.decode()
    say(client, "0*1357*1357")
    assert "USSD PIN: on since" in client.get(page).content.decode()
    response = client.post(reverse("tenants:ussd_pin_remove", args=[tenant.public_id]))
    assert response.status_code == 302 and not UssdPin.objects.exists()
    assert AuditEvent.objects.filter(action="portal.ussd_pin_remove", organization=owner.organization).exists()


def test_only_staff_who_manage_portal_access_can_remove_a_pin(client, owner):
    prop = make_property(owner.organization, name="Acacia")
    lease = lease_for(owner, prop, "A1")
    say(client, "0*1357*1357")
    other = make_org(name="Other")
    login(client, other)
    response = client.post(reverse("tenants:ussd_pin_remove", args=[lease.primary_tenant.public_id]))
    assert response.status_code == 404 and UssdPin.objects.exists()
