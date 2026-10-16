"""Reports read inside the report centre (D-148).

The report centre shows the chosen report beside the list of reports, in a frame. A report asked for with ?pane=1 is
drawn without the menus, top bar and footer, and only that response may be framed, and only by our own pages
(frame-ancestors 'self', X-Frame-Options SAMEORIGIN). Every other page keeps frame-ancestors 'none' and DENY.
"""

from django.conf import settings
from django.utils.csp import CSP

PANE_PARAM = "pane"
PREFIX = "/reports/"
NOT_IN_PANE = ("/reports/centre/",)  # the centre itself never goes inside its own frame


def is_pane(request) -> bool:
    path = request.path
    return (request.method == "GET" and request.GET.get(PANE_PARAM) == "1" and path.startswith(PREFIX)
            and not path.startswith(NOT_IN_PANE))


def report_pane(request):
    """Template context: report_pane is True when the page is drawn for the report centre's frame, and
    report_steps (on a report's own page) holds the reports before and after it, for Previous and Next."""
    pane = is_pane(request)
    return {"report_pane": pane, "report_steps": None if pane else _steps(request)}


def _steps(request):
    match = getattr(request, "resolver_match", None)
    membership = getattr(request, "membership", None)
    if (request.method != "GET" or not membership or not match or match.namespace != "reports"
            or match.view_name == "reports:centre"):
        return None
    from django.urls import reverse

    from .ops_views import report_groups

    names = [(name, str(label)) for _title, items in report_groups(membership) for name, label, _text, ok in items if ok]
    keys = [name for name, _label in names]
    if match.view_name not in keys:
        return None
    i = keys.index(match.view_name)

    def step(j):
        if not 0 <= j < len(names):
            return None
        name, label = names[j]
        return {"url": reverse(name), "label": label, "key": name.split(":", 1)[1]}

    return {"previous": step(i - 1), "next": step(i + 1), "position": i + 1, "total": len(names),
            "key": match.view_name.split(":", 1)[1]}


def _framable_by_self(config):
    if not config:
        return config
    config = dict(config)
    config["frame-ancestors"] = [CSP.SELF]
    return config


class ReportPaneMiddleware:
    """Lets our own report centre frame a report drawn with ?pane=1. Listed right inside the CSP middleware, so it
    sees every response, also an error raised by a middleware further in, and overrides the DENY that the clickjacking
    middleware set. The CSP middleware, outside it, then uses the policy given here."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if is_pane(request) and response.get("Content-Type", "").startswith("text/html"):
            if getattr(settings, "SECURE_CSP", None):
                response._csp_config = _framable_by_self(settings.SECURE_CSP)
            if getattr(settings, "SECURE_CSP_REPORT_ONLY", None):
                response._csp_ro_config = _framable_by_self(settings.SECURE_CSP_REPORT_ONLY)
            response.headers["X-Frame-Options"] = "SAMEORIGIN"
        return response
