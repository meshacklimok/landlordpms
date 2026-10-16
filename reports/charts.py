"""Chart descriptions for static/js/charts.js (D-114).

A view builds a dict here, the template puts it in the page with json_script, and the script draws it.
The same figures are always in a table on the page; a chart never carries a number the page does not show.
Colours are the tokens in static/css/app.css without "--chart-": "1" to "5", "1-soft", "neutral", "late-1" to "late-4".
"""

from decimal import Decimal


def _number(value):
    if value is None:
        return None
    return float(value) if isinstance(value, Decimal) else value


def series(label, data, color: str, **extra) -> dict:
    """One set of figures: a label, the numbers, a colour token; extra keys (type, colors, negative) pass through."""
    return {"label": str(label), "data": [_number(v) for v in data], "color": color, **extra}


def chart(kind: str, labels, sets: list[dict], *, axis: str = "money", currency: str = "", total=None,
          **extra) -> dict:
    """A whole chart: "bar", "line" or "doughnut"; `total` labels the sum shown under stacked bars' tooltips."""
    out = {"type": kind, "labels": [str(label) for label in labels], "series": sets, "axis": axis,
           "currency": currency, **extra}
    if total is not None:
        out["totalLabel"] = str(total)
    return out


def doughnut(label, rows, colors: list[str], *, axis: str = "money", currency: str = "") -> dict:
    """Slices from (name, amount) rows. Colours go in order; slices past the list are the neutral grey."""
    rows = list(rows)
    return chart("doughnut", [name for name, _amount in rows],
                 [series(label, [amount for _name, amount in rows], colors[0] if colors else "neutral",
                         colors=[colors[i] if i < len(colors) else "neutral" for i in range(len(rows))])],
                 axis=axis, currency=currency)


SERIES = ["1", "2", "3", "4", "5"]
