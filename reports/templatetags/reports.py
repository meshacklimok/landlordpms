from urllib.parse import urlencode

from django import template
from django.urls import reverse
from django.utils.formats import date_format
from django.utils.html import escape as html_escape
from django.utils.html import format_html, format_html_join
from django.utils.safestring import mark_safe
from django.utils.translation import get_language
from django.utils.translation import gettext as _

from accounts.permissions import can
from core.money import format_money
from reports import metrics
from reports.finance import AGING_CHART_COLORS

register = template.Library()


@register.filter
def percent(value) -> str:
    """A fraction as a whole percent, 0.845 → 85%; no rate → a dash (never 0%)."""
    if value is None:
        return "—"
    return f"{value * 100:.0f}%"


@register.filter
def percent1(value) -> str:
    """A fraction as a percent with one decimal, 0.0845 → 8.5%; no rate → a dash."""
    if value is None:
        return "—"
    return f"{value * 100:.1f}%"


@register.filter
def signed_percent(value) -> str:
    """A change as a signed whole percent, 0.125 → +13%; no change to show → an empty string."""
    if value is None:
        return ""
    return f"{value * 100:+.0f}%"


@register.filter
def points(value) -> str:
    """A change between two rates in percentage points, 0.031 → +3.1 pts."""
    if value is None:
        return ""
    return _("%(n)s pts") % {"n": f"{value * 100:+.1f}"}


@register.filter
def percent_number(value) -> int:
    """A fraction as a whole number from 0 to 100, for a progress bar's width."""
    if value is None:
        return 0
    return max(0, min(100, round(value * 100)))


@register.filter
def share_of(part, whole):
    """part ÷ whole as a fraction, or None when whole is 0."""
    return None if not whole else part / whole


@register.filter
def change(report, name: str):
    """A finance report's total for one figure against the previous period's (D-110)."""
    return report.change(name)


_AGING_COLORS = {"current": "bg-success", "d1_30": "bg-info", "d31_60": "bg-warning", "d61_90": "bg-danger-subtle",
                 "d90": "bg-danger"}


@register.filter
def aging_color(key: str) -> str:
    """The bar colour for an aged receivables bucket: green when not yet due through red when over 90 days."""
    return _AGING_COLORS.get(key, "bg-secondary")


@register.filter
def aging_chart_color(key: str) -> str:
    """The chart colour token for an aged receivables bucket (D-114), for keys beside the charts."""
    return AGING_CHART_COLORS.get(key, "neutral")


@register.filter
def over_60(lease_row):
    """What a lease owes more than 60 days late (D-110)."""
    return lease_row.buckets["d61_90"] + lease_row.buckets["d90"]


@register.filter
def glance_change(glance, name: str):
    """One property's figure this month against last month (D-110)."""
    return glance.change(name)


_STATE_CELLS = {"paid": ("bg-success text-white", "✓"), "late": ("bg-warning text-dark", "L"),
                "part": ("bg-info text-dark", "½"), "unpaid": ("bg-danger text-white", "✗"),
                "due": ("bg-secondary-subtle text-body", "·"), "none": ("bg-body-tertiary text-body-secondary", "")}


@register.filter
def state_class(state: str) -> str:
    """The colour of one month in the unit-by-unit grid (D-112); each also has its own mark, not colour alone."""
    return _STATE_CELLS.get(state, _STATE_CELLS["none"])[0]


@register.filter
def state_mark(state: str) -> str:
    return _STATE_CELLS.get(state, _STATE_CELLS["none"])[1]


_MONTH_NAMES: dict = {}


def _month_name(month, language) -> str:
    """A month's short name and year, worked out once per language and not once per unit."""
    key = (month, language)
    name = _MONTH_NAMES.get(key)
    if name is None:
        if len(_MONTH_NAMES) > 600:
            _MONTH_NAMES.clear()
        name = _MONTH_NAMES[key] = str(date_format(month, "M Y"))
    return name


@register.simple_tag
def unit_months(cells, currency="KES"):
    """The twelve month squares of one unit in the unit-by-unit grid. Built here, not in the template: a landlord with
    100 units draws 1,200 of them, and each used to cost a dozen template lookups and two date formats (pilot
    sizing, 2026-10-19). The markup is joined as plain text, escaping each distinct piece once."""
    language = get_language()
    escaped: dict = {}

    def esc(text) -> str:
        done = escaped.get(text)
        if done is None:
            done = escaped[text] = html_escape(str(text))
        return done

    out = []
    for c in cells:
        name = _month_name(c.month, language)
        css, mark = _STATE_CELLS.get(c.state, _STATE_CELLS["none"])
        label = c.label
        detail = f"{name}: {label}"
        if c.expected:
            detail += f" · {format_money(c.collected, currency)} / {format_money(c.expected, currency)}"
        out.append(f'<span class="unit-month {esc(css)}" title="{esc(detail)}"><span aria-hidden="true">{esc(mark)}'
                   f'</span><span class="visually-hidden">{esc(name)}: {esc(label)}</span></span>')
    return mark_safe("".join(out))  # noqa: S308 - every dynamic piece above is escaped


@register.simple_tag
def sparkline(values, label=""):
    """A small trend line for a figure tile (D-113): the last months of one figure, drawn in SVG on the server so it
    needs no script. Missing months are gaps. The tile's own number and words carry the meaning; the line is extra."""
    points = [None if v is None else float(v) for v in values or []]
    known = [v for v in points if v is not None]
    if len(known) < 2:
        return ""
    low, high = min(known), max(known)
    span = (high - low) or 1.0
    step = 100 / (len(points) - 1)
    paths, run = [], []
    for i, v in enumerate(points):
        if v is None:
            if run:
                paths.append(run)
            run = []
            continue
        run.append(f"{i * step:.1f},{26 - (v - low) / span * 24:.1f}")
    if run:
        paths.append(run)
    lines = format_html_join("", '<polyline points="{}"/>', ((" ".join(p),) for p in paths if len(p) > 1))
    return format_html('<svg class="spark" viewBox="0 0 100 28" preserveAspectRatio="none" role="img" '
                       'aria-label="{}">{}</svg>', label, lines)


@register.simple_tag
def ring(fraction, label="", tone="", size=""):
    """A progress circle (D-114): a share such as rent collected ÷ expected, drawn in SVG on the server so it needs
    no script, with the percent in the middle. `tone` "bad" draws it in the late colour; `size` "sm" is smaller.
    The words beside it carry the meaning; no rate gives an empty circle and a dash, never 0%."""
    shown = percent(fraction)
    filled = percent_number(fraction)
    # r = 100 ÷ 2π, so the circle is 100 long and the dash is the percent itself.
    arc = (format_html('<circle class="ring-arc" cx="18" cy="18" r="15.915" pathLength="100" '
                       'stroke-dasharray="{} 100"/>', filled) if filled else "")
    return format_html('<span class="ring{}" role="img" aria-label="{}: {}">'
                       '<svg viewBox="0 0 36 36" aria-hidden="true">'
                       '<circle class="ring-track" cx="18" cy="18" r="15.915"/>{}</svg>'
                       '<span class="ring-value" aria-hidden="true">{}</span></span>',
                       (" ring-bad" if tone == "bad" else "") + (" ring-sm" if size == "sm" else ""),
                       label, shown, arc, shown)


@register.filter
def series_color(index) -> str:
    """The chart colour token for the nth slice or series (D-114), as static/js/charts.js colours them in order."""
    from core.charts import SERIES

    return SERIES[index] if 0 <= index < len(SERIES) else "neutral"


@register.filter
def as_excel(query: str) -> str:
    """A CSV download's query string, pointed at the Excel download instead."""
    return str(query).replace("format=csv", "format=xlsx")


@register.inclusion_tag("reports/_trail.html")
def money_trail(membership, kind, day, prop=None, lease=None, counted=True):
    """Where one payment or expense shows in the reports, each link opened on its month and property.

    `kind` is "payment" or "expense"; `counted` False says it is not in the figures yet (or any more).
    A link is left out when the person may not open that report.
    """
    first, last = metrics.month_start(day), metrics.month_end(day)
    pid = str(prop.public_id) if prop is not None else ""
    ops = {"period": "custom", "from": first.isoformat(), "to": last.isoformat()}
    fin = {"from": first.strftime("%Y-%m"), "to": first.strftime("%Y-%m")}
    if pid:
        ops["property"] = pid
        fin["property"] = pid

    def link(name, label, note, query):
        return {"label": label, "note": note, "url": reverse(name) + "?" + urlencode(query)}

    financial = can(membership, "reports.view_financial")
    links = []
    if kind == "payment":
        if can(membership, "payments.view"):
            links.append(link("reports:payments", _("Payments report"), _("Every payment received that month"), ops))
        if financial:
            links += [
                link("reports:profit_loss", _("Profit and loss"), _("Counted in rent or utilities income"), fin),
                link("reports:cash_flow", _("Cash flow"), _("Money in that month"), fin),
            ]
        if lease is not None and financial:
            links.append(link("reports:tenant_statement", _("Tenant statement"), _("On this tenant's account"),
                              {**ops, "lease": str(lease.public_id)}))
        if can(membership, "invoices.view"):
            links.append(link("reports:arrears", _("Arrears"), _("Lowers what the tenant still owes"), ops))
        if financial:
            links.append(link("reports:owner_statement", _("Owner statement"), _("Paid over to the owner"), {}))
    else:
        if financial:
            links += [
                link("reports:expenses", _("Expenses report"), _("By category and supplier"), ops),
                link("reports:profit_loss", _("Profit and loss"), _("Taken off income"), fin),
                link("reports:cash_flow", _("Cash flow"), _("Money out that month"), fin),
                link("reports:profitability", _("Profitability"), _("Counted against the property"), ops),
                link("reports:owner_statement", _("Owner statement"), _("Deducted before the owner is paid"), {}),
            ]
    return {"links": links, "counted": counted, "kind": kind}


@register.filter
def last_day(day):
    """The last day of the month a date falls in."""
    return metrics.month_end(metrics.month_start(day))
