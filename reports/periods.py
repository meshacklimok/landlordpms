"""The reporting period shared by the report pages (D-132): a preset or a custom range of days.

?period=this_month|last_month|this_quarter|last_quarter|this_year|last_year|custom; custom reads ?from= and ?to=
as dates. The year and the quarters start at the organization's financial year month (D-199). Missing or unreadable: this month. Always at most MAX_DAYS long and never the wrong way round.
"""

import datetime
from dataclasses import dataclass

from django.utils.translation import gettext_lazy as _

from .metrics import add_months, month_end, month_start

MAX_DAYS = 731

PRESETS = [
    ("this_month", _("This month")),
    ("last_month", _("Last month")),
    ("this_quarter", _("This quarter")),
    ("last_quarter", _("Last quarter")),
    ("this_year", _("This year")),
    ("last_year", _("Last year")),
    ("custom", _("Custom dates")),
]


@dataclass(frozen=True)
class Period:
    key: str
    start: datetime.date
    end: datetime.date

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1

    @property
    def label(self) -> str:
        start = f"{self.start.day} {self.start:%b}"
        if self.start.year != self.end.year:
            start += f" {self.start.year}"
        return f"{start} – {self.end.day} {self.end:%b %Y}"

    def previous(self) -> "Period":
        """The same number of days straight before this period, to compare with."""
        end = self.start - datetime.timedelta(days=1)
        return Period("previous", end - datetime.timedelta(days=self.days - 1), end)

    def query(self) -> dict:
        """The GET values that give this period back, to carry between pages."""
        if self.key == "custom":
            return {"period": "custom", "from": self.start.isoformat(), "to": self.end.isoformat()}
        return {"period": self.key}


def year_start(day: datetime.date, first_month: int = 1) -> datetime.date:
    """First day of the financial year `day` falls in, when the year starts in `first_month` (1 is January)."""
    return datetime.date(day.year if day.month >= first_month else day.year - 1, first_month, 1)


def _quarter_start(day: datetime.date, first_month: int = 1) -> datetime.date:
    """Quarters count from the start of the financial year."""
    start = year_start(day, first_month)
    months = (day.year - start.year) * 12 + day.month - start.month
    return add_months(start, 3 * (months // 3))


def _date(value: str | None) -> datetime.date | None:
    try:
        return datetime.date.fromisoformat((value or "").strip())
    except ValueError:
        return None


def preset(key: str, today: datetime.date, first_month: int = 1) -> Period | None:
    if key == "this_month":
        return Period(key, month_start(today), month_end(today))
    if key == "last_month":
        first = add_months(month_start(today), -1)
        return Period(key, first, month_end(first))
    if key == "this_quarter":
        first = _quarter_start(today, first_month)
        return Period(key, first, month_end(add_months(first, 2)))
    if key == "last_quarter":
        first = add_months(_quarter_start(today, first_month), -3)
        return Period(key, first, month_end(add_months(first, 2)))
    if key in ("this_year", "last_year"):
        first = year_start(today, first_month)
        if key == "last_year":
            first = add_months(first, -12)
        return Period(key, first, month_end(add_months(first, 11)))
    return None


def from_request(params, today: datetime.date, first_month: int = 1) -> Period:
    key = (params.get("period") or "").strip()
    if key == "custom":
        start, end = _date(params.get("from")), _date(params.get("to"))
        if start and end:
            if start > end:
                start, end = end, start
            if (end - start).days + 1 > MAX_DAYS:
                start = end - datetime.timedelta(days=MAX_DAYS - 1)
            return Period("custom", start, end)
    return preset(key, today, first_month) or preset("this_month", today)
