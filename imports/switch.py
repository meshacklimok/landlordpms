"""Moving to NyumbaPMS from cash, a bank account, another Paybill or Till, a notebook or another system.

The guide page works out which steps are already done from the data (no table, like accounts/setup.py), and the
"how to pay" notice is written from the organization's payment accounts so the landlord only has to check it.
"""
from dataclasses import dataclass

from django.urls import reverse
from django.utils.translation import gettext as _

from billing.models import LedgerEntry
from leases.models import Lease
from payments.models import Payment, PaymentAccount
from properties.models import Unit
from tenants.models import Tenant


@dataclass(frozen=True)
class Step:
    title: str
    text: str
    url: str
    action: str
    done: bool


def pay_change_text(org) -> str:
    """The notice that tells tenants how to pay from now on. {tenant_name}, {unit} and {pay_ref} are filled in per
    tenant when it is sent."""
    acct = (PaymentAccount.objects.filter(organization=org).exclude(type=PaymentAccount.Type.CASH)
            .order_by("type", "pk").first())
    if acct is None:
        return _("Hello {tenant_name}, from now please pay rent for {unit} as we will tell you. Thank you.")
    T = PaymentAccount.Type
    if acct.type == T.PAYBILL:
        how = _("M-Pesa Paybill %(n)s, account number {pay_ref}") % {"n": acct.number}
    elif acct.type == T.TILL:
        how = _("M-Pesa Till %(n)s") % {"n": acct.number}
    elif acct.type == T.BANK:
        how = _("bank account %(n)s, reference {pay_ref}") % {"n": acct.number}
    else:
        how = _("M-Pesa to %(n)s") % {"n": acct.number}
    return _("Hello {tenant_name}, from now please pay rent for {unit} by %(how)s. "
             "Please stop using the old way. Thank you.") % {"how": how}


def steps(org) -> list[Step]:
    units = Unit.objects.for_org(org).exists()
    tenants = Tenant.objects.for_org(org).exists()
    leases = Lease.all_objects.for_org(org).exists()
    pay = PaymentAccount.objects.filter(organization=org).exists()
    balances = LedgerEntry.objects.filter(organization=org, kind=LedgerEntry.Kind.OPENING_BALANCE).exists()
    return [
        Step(_("Bring in your units and tenants"),
             _("Upload your spreadsheet or notebook list. 'Tenants moving in' adds the tenant and the lease together."),
             reverse("imports:list"), _("Import"), units and tenants and leases),
        Step(_("Say what each tenant owes today"),
             _("Add arrears (or money paid ahead) as opening balances, so nobody is billed twice or loses credit."),
             reverse("imports:list"), _("Opening balances"), balances),
        Step(_("Bring in past payments (optional)"),
             _("Upload what tenants have already paid, so their statements show the full history. Nobody is "
               "messaged. From now on, rent paid by cash, bank or cheque is entered with Record a payment."),
             reverse("imports:list"), _("Past payments"), Payment.objects.filter(organization=org).exists()),
        Step(_("Set up where rent will be paid"),
             _("Add your Paybill, Till or bank account. Keep the old way working until tenants have moved."),
             reverse("mpesa:settings"), _("Payment setup"), pay),
        Step(_("Tell tenants the new way to pay"),
             _("One message to every tenant, with their own account number filled in. Check it, then send."),
             reverse("notifications:announcement_new") + "?switch=1", _("Write the notice"), False),
        Step(_("Match the first payments"),
             _("Payments that arrive without a clear account number wait in the inbox for you to match with one tap."),
             reverse("mpesa:inbox"), _("Open payments"), False),
    ]
