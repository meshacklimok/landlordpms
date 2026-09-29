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
