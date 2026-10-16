"""Counts opens of the installed app (D-102): the manifest's start URL carries `?source=app` (D-080)."""

import logging

from django.db import DatabaseError, transaction
from django.db.models import F
from django.utils import timezone

from .models import AppOpen

log = logging.getLogger(__name__)


def platform(user_agent: str) -> str:
    if "Android" in user_agent:
        return AppOpen.Platform.ANDROID
    if "iPhone" in user_agent or "iPad" in user_agent:
        return AppOpen.Platform.IOS
    return AppOpen.Platform.OTHER


def record(request) -> None:
    user = getattr(request, "user", None)
    membership = getattr(request, "membership", None)
    if membership is not None:
        audience, org = AppOpen.Audience.STAFF, membership.organization
    elif user is not None and user.is_authenticated:
        audience, org = AppOpen.Audience.TENANT, None
    else:
        audience, org = AppOpen.Audience.SIGNED_OUT, None
    key = {"day": timezone.localdate(), "organization": org, "audience": audience,
           "platform": platform(request.headers.get("User-Agent", ""))}
    try:
        with transaction.atomic():
            row, _created = AppOpen.objects.get_or_create(**key)
            AppOpen.objects.filter(pk=row.pk).update(count=F("count") + 1)
    except DatabaseError:  # a count is never worth a failed page
        log.warning("Could not count an app open", exc_info=True)


class AppOpenMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.method == "GET" and request.path == "/" and request.GET.get("source") == "app":
            record(request)
        return self.get_response(request)
