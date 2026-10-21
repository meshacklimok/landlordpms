"""Document numbers (doc 11 §24): LSE-2026-000042, per organization, gap-free per transaction."""

from django.db import transaction

from .models import NumberSequence

WIDTH = 6


def next_number(organization, key: str, *, prefix: str, period: str = "") -> str:
    """Takes the next number in the series. Call inside the transaction that issues the document."""
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError("next_number() must run inside the issuing transaction.")
    seq, _ = NumberSequence.objects.select_for_update().get_or_create(
        organization=organization, key=key, period=period, defaults={"prefix": prefix})
    value = seq.next_value
    NumberSequence.objects.filter(pk=seq.pk).update(next_value=value + 1)
    parts = [seq.prefix, period, f"{value:0{WIDTH}d}"] if period else [seq.prefix, f"{value:0{WIDTH}d}"]
    return "-".join(parts)
