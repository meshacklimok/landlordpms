"""Responses shared by the provider callbacks."""

import logging

from django.http import HttpResponse

logger = logging.getLogger(__name__)


class ReplyThen(HttpResponse):
    """Runs `then` once the reply has been sent: WSGI and ASGI servers close the response after the body.

    Used where a provider needs a quick answer and the slow part can wait: the USSD M-Pesa prompt (D-084)
    and tenants' WhatsApp questions (D-093).
    """

    def __init__(self, *args, then=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._then = then

    def close(self):
        then, self._then = self._then, None
        try:
            if then is not None:
                then()
        except Exception:
            logger.exception("Follow-up after the reply failed")
        finally:
            super().close()
