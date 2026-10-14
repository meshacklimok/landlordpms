from django.conf import settings

SYSTEM = "default"
MESSAGES = "messages"

# The two mail accounts the server can hold: the EMAIL_* settings and the optional EMAIL_MESSAGES_* ones.
PROVIDERS = {SYSTEM: "Resend", MESSAGES: "Brevo"}


def mailer_for(purpose: str = SYSTEM) -> str:
    """The MAILERS alias to pass as `using=` when sending.

    `purpose` is SYSTEM (sign-in codes, alerts, support) or MESSAGES (tenant and owner mail). Which account
    handles each is chosen in Django admin (Core > Email routing); with no choice, or a choice whose account
    is not set up on this server, SYSTEM goes to `default` and MESSAGES to `messages`, else `default`."""
    choice = purpose
    try:
        from .models import EmailRouting

        choice = EmailRouting.get().alias_for(purpose)
    except Exception:  # noqa: BLE001 - mail must still go out if the table is missing or the database is down
        pass
    return choice if choice in settings.MAILERS else SYSTEM
