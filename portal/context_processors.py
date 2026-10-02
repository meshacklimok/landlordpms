from . import selectors


def portal(request):
    """`portal_access`: whether the top bar shows the tenant's own pages."""
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return {"portal_access": False}
    return {"portal_access": selectors.has_access(user)}
