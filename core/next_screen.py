"""Pages that now live in the Next.js app (D-168): the Django address stays, so links and redirects keep working."""

from django.conf import settings
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpResponseRedirect
from django.views import View


class NextScreenView(LoginRequiredMixin, View):
    """Send the browser to the same path on the Next.js app. In production Next.js answers this path itself (it sits
    in front of Django), so this runs only in development, where WEB_APP_URL is the Next.js dev server. A visitor is sent to sign in first; what the
    member may see is decided by the web-api view the screen reads."""

    def go(self, request, *args, **kwargs):
        base = getattr(settings, "WEB_APP_URL", "")
        return HttpResponseRedirect(f"{base}{request.get_full_path()}")

    get = post = go
