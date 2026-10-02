"""Active organization in the session (doc 14 A10).

Sets ``request.membership`` and ``request.organization`` on every request. The
membership is re-read each time, so suspensions and role changes apply on the
next request.
"""

from .models import Membership

SESSION_KEY = "active_org"


def user_memberships(user):
    return (
        Membership.objects.filter(user=user, is_active=True, organization__archived_at__isnull=True)
        .select_related("organization", "role", "user")
        .order_by("created_at")
    )


class ActiveOrganizationMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.membership = None
        request.organization = None
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated:
            wanted = request.session.get(SESSION_KEY)
            memberships = list(user_memberships(user))
            chosen = next((m for m in memberships if str(m.organization.public_id) == wanted), None)
            if chosen is None and memberships:
                chosen = memberships[0]
            if chosen is not None:
                request.membership = chosen
                request.organization = chosen.organization
                if wanted != str(chosen.organization.public_id):
                    request.session[SESSION_KEY] = str(chosen.organization.public_id)
            request.user_memberships = memberships
        else:
            request.user_memberships = []
        return self.get_response(request)


class AdminMFAMiddleware:
    """Django admin opens only for a Platform Admin whose session passed a two-step code (D-059).

    Admin's own login page is never used: it would skip the second step.
    """

    def __init__(self, get_response):
        self.get_response = get_response
        self._prefix = None

    def __call__(self, request):
        if self._prefix is None:
            from django.urls import reverse

            self._prefix = reverse("admin:index")
        if request.path.startswith(self._prefix):
            response = self._gate(request)
            if response is not None:
                return response
        return self.get_response(request)

    def _gate(self, request):
        from django.contrib import messages
        from django.contrib.auth.views import redirect_to_login
        from django.http import Http404
        from django.shortcuts import redirect
        from django.urls import reverse
        from django.utils.http import urlencode
        from django.utils.translation import gettext as _

        from . import mfa

        user = request.user
        if not user.is_authenticated:
            return redirect_to_login(request.get_full_path())
        if not (user.is_active and user.is_staff):
            raise Http404
        if not mfa.is_enabled(user):
            messages.warning(request, _("Set up two-step login to open the admin."))
            return redirect("accounts:security")
        if not mfa.session_verified(request):
            return redirect(f"{reverse('accounts:login_mfa')}?{urlencode({'next': request.get_full_path()})}")
        return None
