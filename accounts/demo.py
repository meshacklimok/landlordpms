"""The public "Try the demo" organization (D-210).

A visitor is signed in as one shared sample landlord with no sign-up. The organization is marked `is_demo`, so:
every change is refused (`DemoReadOnlyMiddleware`), the pages that hold secrets or cost money are closed, nothing is
ever sent from it (`notifications.delivery`), and it never lapses. The data comes from `manage.py seed_public_demo`,
which makes a fresh organization every week. A visitor can look around as the owner or as any of the sample staff.
"""

from urllib.parse import urlsplit

from django.contrib import messages
from django.contrib.auth import login, logout
from django.http import JsonResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext as _

from core import sessions
from portal.models import TenantAccount

from .middleware import SESSION_KEY as ACTIVE_ORG
from .models import Membership, Organization, User

# Not a real subscriber's number. The account has no usable password, so this is only a name for the row.
DEMO_PHONE = "+254700000000"
DEMO_USER_NAME = "Demo Landlord"
DEMO_ORG_NAME = "Demo Properties Ltd"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
# What a demo visitor may still post: signing out.
ALLOWED_POST_NAMES = ("accounts:logout",)
# Pages a demo visitor may not even open: secrets, money we are paid, outside services that cost us, account removal.
BLOCKED_PREFIXES = (
    "/account/", "/delete-account/", "/security/", "/console/", "/ok-ml-staff/", "/subscription/", "/ask/",
    "/staff/api-keys/", "/staff/webhooks/", "/staff/two-step/", "/staff/invite/", "/organization/export/",
    "/mpesa/settings/", "/messages/whatsapp/", "/messages/sms/", "/messages/email/", "/my/ask/",
)
# (switch key, name, phone, role template, property codes the person works at; None means every property)
STAFF = [
    ("owner", DEMO_USER_NAME, DEMO_PHONE, "owner", None),
    ("manager", "Grace Wanjiru", "+254700000001", "manager", None),
    ("accountant", "Peter Otieno", "+254700000002", "accountant", None),
    ("leasing_agent", "Faith Achieng", "+254700000003", "leasing_agent", ("KHW", "GRN")),
    ("caretaker", "Samuel Kiprono", "+254700000004", "caretaker", ("RVS",)),
]
STAFF_PHONES = {phone: key for key, _name, phone, _role, _codes in STAFF}
# The sample tenant: a portal login linked to one tenant record of the demo (named after that tenant by the seed).
TENANT_KEY = "tenant"
TENANT_PHONE = "+254700000005"


def demo_org() -> Organization | None:
    return Organization.objects.filter(is_demo=True, archived_at__isnull=True).order_by("-created_at").first()


def is_demo(request) -> bool:
    """True when this request is inside the demo organization, or is the sample tenant's (who has no membership)."""
    if getattr(getattr(request, "organization", None), "is_demo", False):
        return True
    user = getattr(request, "user", None)
    return bool(user is not None and user.is_authenticated and user.phone == TENANT_PHONE)


def demo_user(phone: str = DEMO_PHONE, name: str = DEMO_USER_NAME) -> User:
    """A sample person: phone and terms confirmed, no password, no two-step login, no platform rights."""
    from support.legal import VERSION

    user, created = User.objects.get_or_create(phone=phone, defaults={"full_name": name})
    now = timezone.now()
    if created or user.phone_verified_at is None or user.terms_version != VERSION or user.has_usable_password():
        user.full_name = name
        user.phone_verified_at = user.phone_verified_at or now
        user.email_verified_at = user.email_verified_at or now
        user.terms_version, user.terms_accepted_at = VERSION, user.terms_accepted_at or now
        user.is_active, user.is_staff, user.is_superuser = True, False, False
        user.set_unusable_password()
        user.save()
    return user


def _sign_in(request, user: User, org: Organization) -> None:
    login(request, user, backend="accounts.backends.PhoneOrEmailBackend")
    request.session[ACTIVE_ORG] = str(org.public_id)
    request.session[sessions.REMEMBER] = False  # the short staff limits apply (D-107)


def enter(request) -> bool:
    """Signs the visitor in as the demo landlord. False when there is no demo organization yet."""
    org = demo_org()
    if org is None:
        return False
    _sign_in(request, demo_user(), org)
    return True


def switch(request, key: str) -> bool:
    """Signs the visitor in as another sample person of the demo organization. False when there is no such person."""
    org = demo_org()
    row = next((s for s in STAFF if s[0] == key), None)
    if org is None or (row is None and key != TENANT_KEY):
        return False
    user = User.objects.filter(phone=row[2] if row else TENANT_PHONE).first()
    if user is None:
        return False
    if row is None:
        if not TenantAccount.objects.filter(organization=org, user=user, revoked_at__isnull=True).exists():
            return False
    elif not Membership.objects.filter(organization=org, user=user, is_active=True).exists():
        return False
    _sign_in(request, user, org)
    return True


def leave(request) -> None:
    """Ends a demo session; a real landlord's session is never touched."""
    if is_demo(request):
        logout(request)


def people(request) -> list[dict]:
    """The sample people a visitor can switch to, for the banner."""
    org = getattr(request, "organization", None)
    if org is None and is_demo(request):  # the sample tenant is in no organization
        org = demo_org()
    if org is None or not org.is_demo:
        return []
    rows = Membership.objects.filter(organization=org, is_active=True, user__phone__in=STAFF_PHONES).select_related(
        "user", "role")
    by_phone = {m.user.phone: m for m in rows}
    out = []
    for key, name, phone, _role, _codes in STAFF:
        m = by_phone.get(phone)
        if m is not None:
            out.append({"key": key, "name": name, "role": m.role.name, "current": m.user_id == request.user.pk,
                        "url": reverse("growth:demo_as", args=[key])})
    account = TenantAccount.objects.filter(organization=org, user__phone=TENANT_PHONE, revoked_at__isnull=True).first()
    if account is not None:
        out.append({"key": TENANT_KEY, "name": account.user.full_name, "role": _("Tenant"),
                    "current": account.user_id == request.user.pk, "url": reverse("growth:demo_as", args=[TENANT_KEY])})
    return out


def trial_url() -> str:
    return reverse("growth:demo_leave")


def is_blocked(path: str) -> bool:
    return path.startswith(BLOCKED_PREFIXES)


class DemoReadOnlyMiddleware:
    """Refuses every change made from the demo organization. Sits after `ActiveOrganizationMiddleware`."""

    def __init__(self, get_response):
        self.get_response = get_response
        self._allowed = None

    def _allowed_paths(self) -> frozenset:
        if self._allowed is None:
            self._allowed = frozenset(reverse(name) for name in ALLOWED_POST_NAMES)
        return self._allowed

    def __call__(self, request):
        if is_demo(request):
            if request.method not in SAFE_METHODS and request.path not in self._allowed_paths():
                return self._refuse(request, _("This is a demo with sample data, so nothing can be changed. "
                                               "Start your free trial to use your own."))
            if is_blocked(request.path):
                return self._refuse(request, _("That page is not part of the demo. It holds settings and secrets "
                                               "that only your own account has."))
        return self.get_response(request)

    @staticmethod
    def _refuse(request, text):
        if "text/html" in request.headers.get("Accept", ""):
            messages.info(request, text)
            back = request.headers.get("Referer", "")
            ok = url_has_allowed_host_and_scheme(back, {request.get_host()}) and not is_blocked(urlsplit(back).path)
            return redirect(back if ok else "/")
        return JsonResponse({"error": "demo_read_only", "detail": text}, status=403)
