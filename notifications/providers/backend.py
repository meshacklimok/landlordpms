from django.core.mail.backends.smtp import EmailBackend


class SuppressingSMTPBackend(EmailBackend):
    """SMTP that leaves out addresses on the suppression list (notifications/suppression.py). A message whose
    every recipient is listed is not sent. Callers that must tell the person (a sign-in code, a tenant message)
    check the list first and say so; this is the net under every other send."""

    def send_messages(self, email_messages):
        from notifications.suppression import suppressed

        allowed = []
        for message in email_messages:
            blocked = suppressed([*message.to, *message.cc, *message.bcc])
            if blocked:
                message.to = [a for a in message.to if a.strip().lower() not in blocked]
                message.cc = [a for a in message.cc if a.strip().lower() not in blocked]
                message.bcc = [a for a in message.bcc if a.strip().lower() not in blocked]
            if message.recipients():
                allowed.append(message)
        return super().send_messages(allowed) if allowed else 0
