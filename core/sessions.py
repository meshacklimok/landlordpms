"""Ends a login that sat idle too long or reached its maximum age (D-107).

Staff sessions are short: they open owners' money and tenants' personal data, often on a shared office computer.
Tenant sessions in the portal are long, since tenants sign in with an SMS code on their own phones. The limits are
in settings (SESSION_IDLE_MINUTES and the rest). The last-seen time is saved at most once a minute, so a busy page
does not write the session on every request.
"""

import time

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import logout
from django.contrib.auth.signals import user_logged_in
from django.dispatch import receiver
from django.utils.translation import gettext as _

# Session keys: epoch seconds of the login and of the last request.
STARTED = "session_started"
SEEN = "session_seen"
TOUCH_EVERY = 60


def is_staff_session(request) -> bool:
    return request.user.is_staff or bool(getattr(request, "user_memberships", None))


def limits(request) -> tuple[int, int]:
    """(idle seconds, maximum age in seconds) for the logged-in user of this request."""
    if is_staff_session(request):
        return settings.SESSION_IDLE_MINUTES * 60, settings.SESSION_MAX_HOURS * 60 * 60
    day = 24 * 60 * 60
    return settings.PORTAL_SESSION_IDLE_DAYS * day, settings.PORTAL_SESSION_MAX_DAYS * day


@receiver(user_logged_in)
def _stamp_login(sender, request, user, **kwargs):
    if request is not None and hasattr(request, "session"):
        request.session[STARTED] = request.session[SEEN] = int(time.time())


class SessionTimeoutMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated:
            self._check(request)
        return self.get_response(request)

    def _check(self, request):
        now = int(time.time())
        session = request.session
        started, seen = session.get(STARTED), session.get(SEEN)
        if started is None or seen is None:
            # Logged in before the limits existed: they count from now.
            session[STARTED] = session[SEEN] = now
            return
        idle, max_age = limits(request)
        if now - seen > idle:
            self._end(request, _("You were logged out after a while without activity. Log in again to carry on."))
        elif now - started > max_age:
            self._end(request, _("For your security, logins last a limited time. Log in again to carry on."))
        elif now - seen >= TOUCH_EVERY:
            session[SEEN] = now

    @staticmethod
    def _end(request, text):
        logout(request)
        # ActiveOrganizationMiddleware already ran for the logged-in user.
        request.membership = request.organization = None
        request.user_memberships = []
        messages.info(request, text)
