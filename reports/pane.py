"""Reports read inside the report centre (D-148).

The report centre shows the chosen report beside the list of reports, in a frame. A report asked for with ?pane=1 is
drawn without the menus, top bar and footer, and only that response may be framed, and only by our own pages
(frame-ancestors 'self', X-Frame-Options SAMEORIGIN). Every other page keeps frame-ancestors 'none' and DENY.
"""

from django.conf import settings
from django.utils.csp import CSP

PANE_PARAM = "pane"
PREFIX = "/reports/"
NOT_IN_PANE = ("/reports/centre/", "/reports/print-all/")  # the centre itself never goes inside its own frame


REPORT_ICONS = {
    "dashboard": "bi-grid-1x2", "income": "bi-cash-stack", "units": "bi-door-closed", "hostel": "bi-building", "outlook": "bi-graph-up-arrow",
    "receivables": "bi-hourglass-split", "arrears": "bi-exclamation-circle", "scorecard": "bi-clipboard-data",
    "performance": "bi-speedometer2", "profitability": "bi-piggy-bank", "deposits": "bi-safe", "profit_loss": "bi-pie-chart", "cash_flow": "bi-arrow-left-right",
    "fees": "bi-percent", "owner_statement": "bi-file-earmark-text", "payments": "bi-credit-card", "behaviour": "bi-people",
    "leases": "bi-file-earmark-ruled", "maintenance": "bi-tools", "delivery": "bi-send", "utilities": "bi-droplet",
    "accounting_export": "bi-box-arrow-up-right", "tenant_statement": "bi-person-lines-fill",
    "expenses": "bi-receipt", "month_on_month": "bi-calendar3",
}


def report_icon(key: str) -> str:
    return REPORT_ICONS.get(key, "bi-file-earmark-bar-graph")


def is_pane(request) -> bool:
    path = request.path
    return (request.method == "GET" and request.GET.get(PANE_PARAM) == "1" and path.startswith(PREFIX)
            and not path.startswith(NOT_IN_PANE))


def report_pane(request):
    """Template context: report_pane is True when the page is drawn for the report centre's frame, and
    report_steps (on a report's own page) holds the reports before and after it, for Previous and Next."""
    pane = is_pane(request)
    steps = _steps(request)
    return {"report_pane": pane, "report_steps": None if pane else steps,
            "report_next": steps["next"] if steps else None}


def _steps(request):
    match = getattr(request, "resolver_match", None)
    membership = getattr(request, "membership", None)
    if (request.method != "GET" or not membership or not match or match.namespace != "reports"
            or match.view_name == "reports:centre"):
        return None
    from django.urls import reverse

    from .ops_views import report_groups

    grouped = report_groups(membership)
    names = [(name, str(label)) for _title, items in grouped for name, label, _text, ok in items if ok]
    keys = [name for name, _label in names]
    if match.view_name not in keys:
        return None
    i = keys.index(match.view_name)

    def step(j):
        if not 0 <= j < len(names):
            return None
        name, label = names[j]
        return {"url": reverse(name), "label": label, "key": name.split(":", 1)[1]}

    # Every report by group, for the "Jump to" sheet opened from the section menu.
    menu = [(title, [{"url": reverse(name), "label": str(label), "current": name == match.view_name,
                      "icon": report_icon(name.split(":", 1)[1])}
                     for name, label, _text, ok in items if ok]) for title, items in grouped]
    return {"previous": step(i - 1), "next": step(i + 1), "position": i + 1, "total": len(names),
            "key": match.view_name.split(":", 1)[1], "menu": [g for g in menu if g[1]]}


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
