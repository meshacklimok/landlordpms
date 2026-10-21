"""Per-user request limits for signed-in people (doc 14 D14).

nginx limits by IP and the views limit login, OTP and public forms. This covers the rest: one signed-in
account, or one stolen session, cannot flood the app or run up the bills that cost us money (OpenAI, SMS,
WhatsApp, email), whatever the IP. Limits are counted per user, so an office behind one shared IP is not
punished for it.
"""

import re

from django.http import HttpResponse
from django.utils.translation import gettext as _

from core import ratelimit

PER_MINUTE = 300         # every request a signed-in user makes
WRITES_PER_MINUTE = 120  # POST, PUT, PATCH, DELETE; offline sync sends bursts, so this is generous

# (method, path pattern, scope, limit per minute): the calls that cost money or send messages.
COSTLY = [
    ("POST", re.compile(r"^/ask/$"), "assistant", 10),
    ("POST", re.compile(r"^/my/ask/"), "tenant_assistant", 10),
    ("POST", re.compile(r"^/meters/readings/[^/]+/scan-bill/$"), "scan", 6),
    ("POST", re.compile(r"^/messages/send/"), "compose", 20),
    ("POST", re.compile(r"^/my/leases/[^/]+/messages/$"), "tenant_message", 10),
    ("POST", re.compile(r"^/reports/owner-statement/send/$"), "statement_send", 10),
    ("POST", re.compile(r"^/esign/lease/[^/]+/send/$"), "esign_send", 10),
    ("GET", re.compile(r"^/(payments/export\.csv|reports/.*(\.csv|\.xlsx|export/?))$"), "export", 12),
]


def too_many(request) -> HttpResponse:
    response = HttpResponse(_("Too many requests. Wait a minute and try again."), status=429,
                            content_type="text/plain; charset=utf-8")
    response["Retry-After"] = "60"
    return response


class UserThrottleMiddleware:
    """Place after AuthenticationMiddleware. Anonymous requests are left to nginx and the views."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated and self._limited(request, user.pk):
            return too_many(request)
        return self.get_response(request)

    def _limited(self, request, user_id) -> bool:
        if not ratelimit.hit(f"user:{user_id}", PER_MINUTE, 60):
            return True
        write = request.method not in ("GET", "HEAD", "OPTIONS")
        if write and not ratelimit.hit(f"user_write:{user_id}", WRITES_PER_MINUTE, 60):
            return True
        for method, pattern, scope, limit in COSTLY:
            if request.method == method and pattern.match(request.path_info):
                if not ratelimit.hit(f"costly:{scope}:{user_id}", limit, 60):
                    return True
        return False
