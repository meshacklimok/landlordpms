"""Where people reach us, available to every template (error pages, sign-in, public footer, Help)."""
from django.conf import settings


def contacts(request):
    digits = "".join(ch for ch in getattr(settings, "SUPPORT_WHATSAPP", "") if ch.isdigit())
    return {"support_email": getattr(settings, "SUPPORT_EMAIL", ""), "hello_email": getattr(settings, "HELLO_EMAIL", ""),
            "support_whatsapp": digits,
            "turnstile_site_key": getattr(settings, "TURNSTILE_SITE_KEY", "")}
