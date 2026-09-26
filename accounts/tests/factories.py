"""Plain factory helpers. No factory library: the domain rules live in services, so tests use them."""

import itertools

from django.utils import timezone

from accounts import services
from accounts.models import Membership, Organization, Role, User
from properties.models import Property

PASSWORD = "Correct-Horse-9"
_seq = itertools.count(1)


def make_user(*, phone: str | None = None, verified: bool = True, **kw) -> User:
    n = next(_seq)
    phone = phone or f"+2547{n:08d}"
    kw.setdefault("full_name", f"User {n}")
    user = User.objects.create_user(phone=phone, password=PASSWORD, **kw)
    if verified:
        user.phone_verified_at = timezone.now()
        user.save(update_fields=["phone_verified_at"])
    return user


def make_org(name: str | None = None, *, owner: User | None = None) -> Membership:
    """Returns the Owner membership of a new organization."""
    owner = owner or make_user()
    return services.create_organization(user=owner, name=name or f"Org {next(_seq)}",
                                        org_type=Organization.Type.COMPANY)


def role(org: Organization, key_or_name: str) -> Role:
    return Role.objects.get(organization=org, based_on_template__key=key_or_name)


def add_member(org: Organization, role_key: str, *, user: User | None = None, all_properties: bool = False,
               properties=()) -> Membership:
    """Adds a member directly (bypassing invitations) for tests that need one."""
    m = Membership.objects.create(user=user or make_user(), organization=org, role=role(org, role_key),
                                  all_properties=all_properties)
    for p in properties:
        m.property_access.create(property=p)
    return m


def make_property(org: Organization, name: str | None = None) -> Property:
    n = next(_seq)
    return Property.objects.create(organization=org, name=name or f"Property {n}", code=f"P{n}")


def fresh(membership: Membership) -> Membership:
    """Re-read a membership as the middleware would on the next request (drops the per-request cache)."""
    return Membership.all_objects.select_related("user", "organization", "role").get(pk=membership.pk)
