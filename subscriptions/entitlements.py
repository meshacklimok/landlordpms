"""What a plan allows (D-060 item 9). Other apps ask here; only creating is ever blocked.

Units are live (not archived) units. Seats are usable members plus pending invitations, not
counting Viewers, who are free.
"""

from django.core.exceptions import ValidationError
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext as _

VIEWER = "viewer"
LABELS = {"units": _("units"), "seats": _("team members")}


def _not_viewer(prefix: str) -> Q:
    return ~Q(**{f"{prefix}based_on_template__key": VIEWER}) | Q(**{f"{prefix}based_on_template__isnull": True})


def usage(org) -> dict[str, int]:
    from accounts.models import Invitation, Membership
    from properties.models import Unit

    members = Membership.objects.filter(organization=org, is_active=True).filter(_not_viewer("role__")).count()
    pending = (Invitation.objects.filter(organization=org, accepted_at__isnull=True, revoked_at__isnull=True,
                                         expires_at__gt=timezone.now())
               .filter(_not_viewer("role__")).count())
    return {"units": Unit.objects.filter(organization=org).count(), "seats": members + pending}


def limits(plan) -> dict[str, int | None]:
    return {"units": plan.unit_limit, "seats": plan.seat_limit}


def over_limits(org, plan) -> list[str]:
    """What the organization uses beyond `plan`, as readable lines; empty when it fits."""
    used, allowed = usage(org), limits(plan)
    return [_("%(used)s %(what)s (the plan allows %(limit)s)") % {"used": used[k], "what": LABELS[k],
                                                                 "limit": allowed[k]}
            for k in ("units", "seats") if allowed[k] is not None and used[k] > allowed[k]]


def check(org, what: str, adding: int = 1) -> None:
    """Raises ValidationError when adding `adding` more would go beyond the plan."""
    from .services import subscription_for

    sub = subscription_for(org)
    limit = limits(sub.plan)[what]
    if limit is None:
        return
    used = usage(org)[what]
    if used + adding > limit:
        raise ValidationError(
            _("Your %(plan)s plan allows %(limit)s %(what)s and you have %(used)s. Choose a bigger plan on the "
              "Subscription page.") % {"plan": sub.plan.name, "limit": limit, "what": LABELS[what], "used": used})


def is_viewer_role(role) -> bool:
    return bool(role.based_on_template_id) and role.based_on_template.key == VIEWER
