"""Where people reach us, available to every template (error pages, sign-in, public footer, Help)."""
from django.conf import settings

from core.contacts import support_whatsapp_numbers


def contacts(request):
    numbers = support_whatsapp_numbers()
    return {"support_email": getattr(settings, "SUPPORT_EMAIL", ""), "hello_email": getattr(settings, "HELLO_EMAIL", ""),
            "support_whatsapp": numbers[0] if numbers else "",
            "support_whatsapp_2": numbers[1] if len(numbers) > 1 else "",
            "support_phone": getattr(settings, "SUPPORT_PHONE", "")}
