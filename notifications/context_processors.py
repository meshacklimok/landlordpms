def bell(request):
    """Unread in-app notifications for the nav bell. Skipped for anonymous and org-less requests."""
    membership = getattr(request, "membership", None)
    if membership is None or not request.user.is_authenticated:
        return {}
    from .services import unread_count

    return {"unread_count": unread_count(request.user, membership.organization)}
