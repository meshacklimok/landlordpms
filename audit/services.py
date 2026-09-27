"""The one way to write an audit event."""

from typing import Any

from django.db import models

from core.net import client_ip as client_ip_of

from .models import AuditEvent


def client_ip(request) -> str | None:
    if request is None:
        return None
    return client_ip_of(request) or None


def record(
    action: str,
    *,
    actor=None,
    organization=None,
    obj: models.Model | None = None,
    changes: dict[str, Any] | None = None,
    request=None,
) -> AuditEvent:
    if actor is None and request is not None and getattr(request, "user", None) is not None:
        actor = request.user if request.user.is_authenticated else None
    event = AuditEvent(
        organization=organization,
        actor=actor,
        action=action,
        changes=changes or {},
        ip=client_ip(request),
        user_agent=(request.META.get("HTTP_USER_AGENT", "")[:255] if request is not None else ""),
    )
    if obj is not None:
        event.object_type = obj._meta.label_lower
        event.object_id = str(getattr(obj, "public_id", None) or obj.pk)
        event.object_repr = str(obj)[:200]
    event.save()
    return event


def diff(before: dict[str, Any], after: dict[str, Any]) -> dict[str, list[Any]]:
    """{"field": [old, new]} for the fields that changed."""
    return {k: [before.get(k), after.get(k)] for k in after if before.get(k) != after.get(k)}
