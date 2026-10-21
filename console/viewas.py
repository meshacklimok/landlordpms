"""The platform owner looking at a landlord's account as one of its people sees it, to understand a support request.

Any staff seat (owner, manager, accountant, caretaker ...) or any tenant with a portal login can be viewed.
Read only: every change is refused while it lasts. Starting and stopping are audited. Superusers only.
"""

from django.contrib import messages
from django.http import HttpResponse, HttpResponseRedirect
from django.utils.translation import gettext as _

SESSION_KEY = "console_view_as"  # the organization's public id
MEMBER_KEY = "console_view_as_member"  # a Membership public id; empty means the organization's owner
TENANT_KEY = "console_view_as_tenant"  # a TenantAccount public id: the tenant portal
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def clear(session) -> None:
    for key in (SESSION_KEY, MEMBER_KEY, TENANT_KEY):
        session.pop(key, None)


def membership_for(request):
    """The seat to show, when this superuser is viewing an organization as staff (the owner unless one is chosen)."""
    from accounts.models import Membership

    public_id = request.session.get(SESSION_KEY)
    if not public_id or not request.user.is_superuser or request.session.get(TENANT_KEY):
        return None
    seats = Membership.objects.filter(organization__public_id=public_id, organization__archived_at__isnull=True,
                                      is_active=True)
    if request.session.get(MEMBER_KEY):
        seats = seats.filter(public_id=request.session[MEMBER_KEY])
    else:
        seats = seats.filter(role__is_owner_role=True)
    return seats.select_related("organization", "role", "user").order_by("created_at").first()


def tenant_account_for(request):
    from portal.models import TenantAccount

    public_id = request.session.get(TENANT_KEY)
    if not public_id or not request.user.is_superuser:
        return None
    return (TenantAccount.objects.filter(public_id=public_id, revoked_at__isnull=True,
                                         tenant__archived_at__isnull=True, organization__archived_at__isnull=True)
            .select_related("user", "tenant", "organization").first())


def label(request) -> str:
    account = getattr(request, "viewing_tenant", None)
    if account:
        return _("%(name)s, a tenant of %(org)s") % {"name": str(account.tenant), "org": account.organization.name}
    m = getattr(request, "membership", None)
    if not m:
        return ""
    return _("%(name)s, %(role)s at %(org)s") % {"name": m.user.full_name, "role": m.role.name,
                                                 "org": m.organization.name}


class ViewAsTenantMiddleware:
    """Right after login: while a superuser views a tenant, the request is the tenant's own login (portal only)."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.viewing_tenant = None
        user = getattr(request, "user", None)
        if (user is not None and user.is_authenticated and user.is_superuser and request.session.get(TENANT_KEY)
                and not request.path.startswith("/console/")):
            account = tenant_account_for(request)
            if account is not None:
                request.viewing_tenant = account
                request.user = account.user
        return self.get_response(request)


class ViewAsReadOnlyMiddleware:
    """Refuses every change outside the console while a superuser is viewing an account. Sits after messages."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        viewing = getattr(request, "viewing_as", False) or getattr(request, "viewing_tenant", None)
        if viewing and request.method not in SAFE_METHODS and not request.path.startswith("/console/"):
            text = _("You are viewing this account as one of its people, so nothing can be changed. Stop viewing first.")
            if "text/html" in request.headers.get("Accept", ""):
                messages.info(request, text)
                return HttpResponseRedirect(request.headers.get("Referer") or "/")
            return HttpResponse(text, status=403)
        return self.get_response(request)
