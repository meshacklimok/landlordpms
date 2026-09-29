"""The single permission check (doc 13): ``can(membership, capability, property=None)``.

Every view, service, API and AI tool calls this function. Views never ask
"is this a manager?"; they ask for a capability.

Effective capabilities are computed per request and cached on the membership
instance, so role edits take effect on the next request (no stale sessions).
"""

from django.core.exceptions import PermissionDenied

from .capabilities import CAPABILITY_MAP
from .models import Capability, Membership, Organization, PropertyAccess


def effective_capabilities(membership: Membership) -> frozenset[str]:
    """Role capabilities + granted overrides − withheld overrides."""
    cached = getattr(membership, "_effective_caps", None)
    if cached is not None:
        return cached
    caps = set(
        Capability.objects.filter(
            is_active=True, rolecapability__role_id=membership.role_id
        ).values_list("codename", flat=True)
    )
    for codename, granted in membership.overrides.filter(capability__is_active=True).values_list(
        "capability__codename", "granted"
    ):
        if granted:
            caps.add(codename)
        else:
            caps.discard(codename)
    result = frozenset(caps)
    membership._effective_caps = result
    return result


def clear_cache(membership: Membership) -> None:
    for attr in ("_effective_caps", "_property_ids"):
        membership.__dict__.pop(attr, None)


def is_membership_usable(membership: Membership | None) -> bool:
    if membership is None:
        return False
    org = membership.organization
    return (
        membership.is_active
        and membership.archived_at is None
        and membership.user.is_active
        and org.archived_at is None
        and org.status != Organization.Status.FROZEN
        and membership.role.archived_at is None
    )


def accessible_property_ids(membership: Membership) -> frozenset[int] | None:
    """None means every property in the organization."""
    if membership.all_properties:
        return None
    cached = getattr(membership, "_property_ids", None)
    if cached is None:
        cached = frozenset(
            PropertyAccess.objects.filter(
                membership=membership, property__organization_id=membership.organization_id
            ).values_list("property_id", flat=True)
        )
        membership._property_ids = cached
    return cached


def can(membership: Membership | None, capability: str, property=None) -> bool:
    if not is_membership_usable(membership):
        return False
    meta = CAPABILITY_MAP.get(capability)
    if meta is None:
        raise ValueError(f"Unknown capability {capability!r}; add it to accounts/capabilities.py")
    if membership.organization.status == Organization.Status.READ_ONLY and not meta.read_only_safe:
        return False
    if capability not in effective_capabilities(membership):
        return False
    if meta.org_wide and not membership.all_properties:
        return False
    if property is not None:
        if property.organization_id != membership.organization_id:
            return False
        ids = accessible_property_ids(membership)
        if ids is not None and property.pk not in ids:
            return False
    return True


def require(membership: Membership | None, capability: str, property=None) -> None:
    if not can(membership, capability, property):
        raise PermissionDenied(capability)


def visible_properties(membership: Membership, queryset=None):
    """Properties this membership may see, already scoped to its organization."""
    from properties.models import Property

    qs = (queryset if queryset is not None else Property.objects.all()).for_org(membership.organization)
    ids = accessible_property_ids(membership)
    return qs if ids is None else qs.filter(pk__in=ids)
