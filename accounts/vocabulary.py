"""The words an organization sees for its people and places (D-130).

Estates, water vendors and garbage collectors bill many households every month, like a landlord, but nobody there
is a "tenant" in a "unit". Only the wording changes; the data and billing are the same. Landlord, company and agency
organizations get the original words, so nothing changes for them.
"""

from django.utils.translation import gettext_lazy as _

RENTAL = {
    "tenant": _("Tenant"), "tenants": _("Tenants"),
    "unit": _("Unit"), "units": _("Units"),
    "property": _("Property"), "properties": _("Properties"),
    "lease": _("Lease"), "leases": _("Leases"),
    "rent": _("Rent"),
}

ESTATE = {
    "tenant": _("Member"), "tenants": _("Members"),
    "unit": _("House"), "units": _("Houses"),
    "property": _("Estate"), "properties": _("Estates"),
    "lease": _("Membership"), "leases": _("Memberships"),
    "rent": _("Service charge"),
}

WATER = {
    "tenant": _("Customer"), "tenants": _("Customers"),
    "unit": _("Connection"), "units": _("Connections"),
    "property": _("Zone"), "properties": _("Zones"),
    "lease": _("Account"), "leases": _("Accounts"),
    "rent": _("Water charge"),
    "service_days": _("Reading days"),
}

WASTE = {
    "tenant": _("Client"), "tenants": _("Clients"),
    "unit": _("Pickup point"), "units": _("Pickup points"),
    "property": _("Route"), "properties": _("Routes"),
    "lease": _("Contract"), "leases": _("Contracts"),
    "rent": _("Collection fee"),
    "service_days": _("Collection days"),
}


# A hostel still bills rent, so it keeps the rent words; only the people and places change (D-188).
HOSTEL = {
    "tenant": _("Student"), "tenants": _("Students"),
    "unit": _("Room or bed"), "units": _("Rooms and beds"),
    "property": _("Hostel"), "properties": _("Hostels"),
    "lease": _("Booking"), "leases": _("Bookings"),
    "rent": _("Hostel fees"),
}


def vocabulary_for(org_type: str) -> dict:
    """The words for an organization type; the rental words for anything else."""
    return {"ESTATE": ESTATE, "WATER": WATER, "WASTE": WASTE, "HOSTEL": HOSTEL}.get(org_type, RENTAL)
