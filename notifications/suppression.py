"""The list of addresses we no longer mail (EmailSuppression), and the check every send path makes."""

from .models import EmailSuppression


def _clean(address: str) -> str:
    return (address or "").strip().lower()


def suppressed(addresses) -> set[str]:
    """The lowercase addresses among `addresses` that are on the list."""
    wanted = {_clean(a) for a in addresses if a}
    if not wanted:
        return set()
    return set(EmailSuppression.objects.filter(email__in=wanted).values_list("email", flat=True))


def is_suppressed(address: str) -> bool:
    return bool(suppressed([address]))


def suppress(address: str, reason: str, *, provider: str = "", detail: str = "") -> bool:
    """Adds the address, and un-verifies any sign-in that uses it, so nothing sensitive is sent there until its
    owner confirms again. False when the address was already listed."""
    from accounts.models import User

    address = _clean(address)
    if not address:
        return False
    _, created = EmailSuppression.objects.get_or_create(
        email=address, defaults={"reason": reason, "provider": provider[:20], "detail": detail[:200]})
    User.objects.filter(email__iexact=address, email_verified_at__isnull=False).update(email_verified_at=None)
    return created
