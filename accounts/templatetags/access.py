from django import template

from accounts.permissions import can as _can

register = template.Library()


@register.filter
def can(membership, capability):
    """{% if request.membership|can:"roles.manage" %}"""
    return _can(membership, capability)


@register.filter
def word(organization, key):
    """{{ lease.organization|word:"lease" }}: what that organization calls it (D-130). For pages that belong to
    one organization but are not opened as staff of it, such as the tenant portal."""
    return organization.vocab[key]


@register.filter
def override_state(overrides, codename):
    """Maps {codename: granted} to the OverrideForm state value: "grant", "deny" or ""."""
    granted = overrides.get(codename)
    return "" if granted is None else ("grant" if granted else "deny")
