"""Owner-only email alerts for successful public registrations."""

import logging

from django.core.mail import send_mail

from notifications.providers.mail import SYSTEM, mailer_for

logger = logging.getLogger(__name__)


def send_new_registration(user) -> bool:
    """Notify platform operators without allowing mail failure to interrupt sign-up."""
    try:
        from ops.alerts import recipients

        to = recipients()
        if not to:
            return False

        joined = user.date_joined.isoformat()
        body = (
            "A new user registered on NyumbaPMS.\n\n"
            f"Name: {user.full_name}\n"
            f"Phone: {user.phone}\n"
            f"Email: {user.email or 'Not provided'}\n"
            f"Registered: {joined}\n"
            f"User reference: {user.public_id}\n\n"
            "This notification was sent to the platform operations recipients."
        )
        send_mail(
            subject="[NyumbaPMS] New user registration",
            message=body,
            from_email=None,
            recipient_list=to,
            fail_silently=False,
            using=mailer_for(SYSTEM),
        )
        return True
    except Exception:
        logger.exception(
            "New registration notification failed for user %s",
            getattr(user, "pk", "unknown"),
        )
        return False
