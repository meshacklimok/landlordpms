from accounts.permissions import can


def banner(request):
    """The subscription warning in the top bar, for members who can see the subscription (D-060 item 12)."""
    membership = getattr(request, "membership", None)
    if membership is None or not can(membership, "subscription.view"):
        return {}
    from .services import banner as subscription_banner

    return {"subscription_banner": subscription_banner(membership.organization)}
