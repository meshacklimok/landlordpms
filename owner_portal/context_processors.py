from . import selectors


def owner_portal(request):
    """`owner_portal_access`: whether the top bar shows the owner's own pages (D-128)."""
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return {"owner_portal_access": False}
    return {"owner_portal_access": selectors.has_access(user)}
