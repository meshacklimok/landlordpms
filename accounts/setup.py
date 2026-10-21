"""The home page setup checklist (D-063 item 1). Worked out from data each time; no table (doc 11 §16)."""

import dataclasses
from dataclasses import dataclass

from django.urls import reverse
from django.utils.translation import gettext as _

from billing.models import Invoice
from leases.models import Lease
from payments.models import PaymentAccount
from properties.models import Property, Unit
from tenants.models import Tenant

from .models import Invitation, Membership
from .vocabulary import RENTAL


@dataclass(frozen=True)
class Step:
    key: str
    label: str
    url: str
    done: bool
    optional: bool = False
    hint: str = ""
    icon: str = "circle"
    minutes: int = 0

    def __str__(self):
        return self.label


def checklist(org) -> list[Step]:
    members = Membership.objects.filter(organization=org).count()
    steps = _steps(org, members)
    v = org.vocab
    if v is RENTAL:
        return steps
    # Water vendors, garbage collectors and estates see their own words (D-130).
    w = {"property": str(v["property"]).lower(), "unit": str(v["unit"]).lower(), "units": str(v["units"]).lower(),
         "tenant": str(v["tenant"]).lower(), "tenants": str(v["tenants"]).lower(), "lease": str(v["lease"]).lower()}
    words = {
        "property": (_("Add a %(property)s") % w, _("Give it a name and where it is.")),
        "units": (_("Add %(units)s") % w, _("Open the %(property)s to add them.") % w),
        "tenant": (_("Add a %(tenant)s") % w, _("A name and phone number is enough to start.")),
        "lease": (_("Start a %(lease)s and set the charge") % w,
                  _("Joins a %(tenant)s to a %(unit)s, with the amount and the day it is due.") % w),
        "payment": (_("Set up how %(tenants)s pay") % w,
                    _("Your Paybill, till or bank details, so payments match to the right %(tenant)s.") % w),
        "invoice": (_("Issue your first invoice"),
                    _("%(tenants)s get the bill by SMS or email, and you see who has paid.") % {"tenants": str(v["tenants"])}),
    }
    return [dataclasses.replace(s, label=words[s.key][0], hint=words[s.key][1]) if s.key in words else s
            for s in steps]


def _steps(org, members) -> list[Step]:
    return [
        Step("property", _("Add a property"), reverse("properties:create"),
             Property.objects.for_org(org).exists(),
             hint=_("A building or estate. Give it a name and where it is."), icon="building", minutes=1),
        Step("units", _("Add units"), reverse("properties:list"), Unit.objects.for_org(org).exists(),
             hint=_("The flats, rooms or shops you let out. Open the property to add them."), icon="door-open",
             minutes=2),
        Step("tenant", _("Add a tenant"), reverse("tenants:create"), Tenant.objects.for_org(org).exists(),
             hint=_("A name and phone number is enough to start."), icon="person-plus", minutes=1),
        Step("lease", _("Start a lease and set the rent"), reverse("leases:list"),
             Lease.all_objects.for_org(org).exists(),
             hint=_("Joins a tenant to a unit, with the rent and the day it is due."), icon="file-earmark-text",
             minutes=2),
        Step("payment", _("Set up how tenants pay"), reverse("mpesa:settings"),
             PaymentAccount.objects.filter(organization=org).exists(),
             hint=_("Your Paybill, till or bank details, so payments match to the right tenant."), icon="phone",
             minutes=3),
        Step("invoice", _("Issue your first invoice"), reverse("billing:invoices"),
             Invoice.objects.for_org(org).exclude(status=Invoice.Status.DRAFT).exists(),
             hint=_("Tenants get the bill by SMS or email, and you see who has paid."), icon="receipt",
             minutes=1),
        Step("invite", _("Invite a team member"), reverse("accounts:invite"),
             members > 1 or Invitation.objects.filter(organization=org).exists(), optional=True,
             hint=_("A caretaker or accountant, with only the access they need."), icon="people"),
    ]
