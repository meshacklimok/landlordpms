"""What an organization's size decides it is shown (D-150).

A landlord with one flat does not manage other people's property, so the owner, management-fee and
portfolio-comparison tools stay out of the way until they apply. Nothing is switched off: the pages still open
by address, and the tools come back as soon as the organization adds a second property, records an owner or a fee,
or is not an individual landlord.
"""

from django.db.models import Q


def many_properties(org) -> bool:
    """More than one live property: only then is there anything to choose between."""
    if org is None:
        return False
    if not hasattr(org, "_many_properties"):
        from properties.models import Property

        org._many_properties = Property.objects.filter(organization=org).count() > 1
    return org._many_properties


def properties_so_far(org) -> int:
    """How many live properties it has now (the form for the next one asks)."""
    from properties.models import Property

    return Property.objects.filter(organization=org).count()


def agency_tools(org) -> bool:
    """Owners, management fees and owner statements: for anyone but a landlord who runs a single property alone."""
    if org is None:
        return True
    if not hasattr(org, "_agency_tools"):
        from accounts.models import Organization
        from properties.models import Property, PropertyOwner

        org._agency_tools = (
            org.org_type != Organization.Type.INDIVIDUAL
            or many_properties(org)
            or PropertyOwner.objects.filter(organization=org).exists()
            or Property.all_objects.filter(organization=org).filter(
                Q(owner__isnull=False) | Q(management_fee_percent__isnull=False)
                | Q(management_fee_fixed__isnull=False) | Q(shares__isnull=False)).exists())
    return org._agency_tools
