def organization(request):
    return {
        "membership": getattr(request, "membership", None),
        "organization": getattr(request, "organization", None),
        "user_memberships": getattr(request, "user_memberships", []),
    }
