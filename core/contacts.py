"""The support WhatsApp numbers: edited in Django admin, falling back to settings.SUPPORT_WHATSAPP."""
from django.conf import settings
from django.db import DatabaseError

from .models import SupportContact


def _digits(value: str) -> str:
    return "".join(ch for ch in (value or "") if ch.isdigit())


def support_whatsapp_numbers() -> list[str]:
    """Up to two numbers, digits only. The first is the main one."""
    try:
        row = SupportContact.objects.filter(pk=1).first()
    except DatabaseError:
        row = None
    numbers = [_digits(row.whatsapp), _digits(row.whatsapp_2)] if row else []
    numbers = [n for n in numbers if n]
    return numbers or [n for n in [_digits(getattr(settings, "SUPPORT_WHATSAPP", ""))] if n]


def support_whatsapp() -> str:
    numbers = support_whatsapp_numbers()
    return numbers[0] if numbers else ""
