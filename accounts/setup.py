"""The home page setup checklist (D-063 item 1). Worked out from data each time; no table (doc 11 §16)."""

from dataclasses import dataclass

from django.urls import reverse
from django.utils.translation import gettext as _

from billing.models import Invoice
from leases.models import Lease
from payments.models import PaymentAccount
from properties.models import Property, Unit
from tenants.models import Tenant

from .models import Invitation, Membership


@dataclass(frozen=True)
class Step:
    key: str
    label: str
    url: str
    done: bool
    optional: bool = False

    def __str__(self):
        return self.label


def checklist(org) -> list[Step]:
    members = Membership.objects.filter(organization=org).count()
    return [
        Step("property", _("Add a property"), reverse("properties:create"),
             Property.objects.for_org(org).exists()),
        Step("units", _("Add units"), reverse("properties:list"), Unit.objects.for_org(org).exists()),
        Step("tenant", _("Add a tenant"), reverse("tenants:create"), Tenant.objects.for_org(org).exists()),
        Step("lease", _("Start a lease and set the rent"), reverse("leases:list"),
             Lease.all_objects.for_org(org).exists()),
        Step("payment", _("Set up how tenants pay"), reverse("mpesa:settings"),
             PaymentAccount.objects.filter(organization=org).exists()),
        Step("invoice", _("Issue your first invoice"), reverse("billing:invoices"),
             Invoice.objects.for_org(org).exclude(status=Invoice.Status.DRAFT).exists()),
        Step("invite", _("Invite a team member"), reverse("accounts:invite"),
             members > 1 or Invitation.objects.filter(organization=org).exists(), optional=True),
    ]
