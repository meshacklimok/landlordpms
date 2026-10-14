"""What a property owner may see in the owner portal (D-128).

Hard rule: every query starts from `live_accounts(user)`. Nothing here takes an organization or a
membership: an owner's reach is their own owner records and nothing else.
"""

from payments.models import Payment
from properties.models import Property, owned_by
from reports.models import OwnerStatementSend

from .models import OwnerAccount

SENDS_SHOWN = 24


def live_accounts(user):
    """The user's accounts that still work: not revoked, organization not archived."""
    if user is None or not user.is_authenticated:
        return OwnerAccount.objects.none()
    return (OwnerAccount.objects.filter(user=user, revoked_at__isnull=True, organization__archived_at__isnull=True)
            .select_related("owner", "organization").order_by("organization__name", "owner__name"))


def has_access(user) -> bool:
    return live_accounts(user).exists()


def own_account(user, public_id) -> OwnerAccount | None:
    return live_accounts(user).filter(public_id=public_id).first()


def properties(account: OwnerAccount):
    """The owner's properties; archived ones only for their past statements."""
    return (Property.all_objects.filter(organization=account.organization, archived_at__isnull=True)
            .filter(owned_by(account.owner)).select_related("branch").order_by("name"))


def sends(account: OwnerAccount):
    """Statements the office sent this owner, newest first."""
    return OwnerStatementSend.objects.filter(organization=account.organization, owner=account.owner)[:SENDS_SHOWN]


def own_send(user, public_id) -> OwnerStatementSend | None:
    owner_ids = live_accounts(user).values("owner_id")
    return (OwnerStatementSend.objects.filter(owner_id__in=owner_ids, public_id=public_id)
            .select_related("owner").first())


def payments(account: OwnerAccount, year: int | None = None, query: str = ""):
    """Confirmed payments on the owner's properties, newest first. Rows carry the unit, never the tenant."""
    found = (Payment.objects.filter(organization=account.organization, status=Payment.Status.CONFIRMED,
                                    lease__unit__property__in=properties(account))
             .select_related("lease__unit__property"))
    if year:
        found = found.filter(paid_at__year=year)
    if query:
        found = found.filter(reference__icontains=query)
    return found.order_by("-paid_at", "-pk")


def payment_years(account: OwnerAccount) -> list[int]:
    return [d.year for d in payments(account).dates("paid_at", "year", order="DESC")]
