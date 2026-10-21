"""Extra response headers (D-139): what a browser may do with our pages, not keeping signed-in pages, and keeping
private pages out of search results (D-158)."""

# Features no page of ours uses. Photos are taken through file inputs, which this does not affect.
PERMISSIONS_POLICY = (
    "accelerometer=(), camera=(), geolocation=(), gyroscope=(), magnetometer=(), microphone=(), "
    "payment=(), usb=(), interest-cohort=()"
)


class ExtraSecurityHeadersMiddleware:
    """Switches off browser features we never use, keeps other sites from embedding our files, and stops a
    signed-in page being stored by the browser or a shared proxy (the Back button after logging out on a shared
    phone or café computer must not show a tenant's statement).

    Static files and responses that already chose their own Cache-Control are left alone.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        response.headers.setdefault("Permissions-Policy", PERMISSIONS_POLICY)
        response.headers.setdefault("Cross-Origin-Resource-Policy", "same-origin")
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated and "Cache-Control" not in response.headers:
            response.headers["Cache-Control"] = "private, no-store"
        if response.get("Content-Type", "").startswith("text/html") and "X-Robots-Tag" not in response.headers:
            from growth import seo

            # Only the pages in the SEO registry may be found (D-158); a new page is private until listed there.
            if seo.page_for(request) is None:
                response.headers["X-Robots-Tag"] = "noindex, nofollow"
        return response
