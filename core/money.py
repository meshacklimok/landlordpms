"""Money helpers (doc 11 §25). Every app parses, rounds, prorates and formats money through here.

Money is a Decimal with 2 places, never a float. Rounding is ROUND_HALF_UP at the line level;
document totals are the sum of rounded lines and are never rounded again.
"""

import calendar
import re
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.utils.translation import gettext as _

CENT = Decimal("0.01")
ZERO = Decimal("0.00")
# Largest value a DecimalField(max_digits=14, decimal_places=2) holds.
MAX = Decimal("999999999999.99")

_STRIP = re.compile(r"(?i)kshs|ksh|kes|[,\s]")


def round_money(value) -> Decimal:
    """Rounds to cents, half up: 0.005 becomes 0.01."""
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


def parse_money(value, *, allow_zero: bool = True, allow_negative: bool = False) -> Decimal:
    """Reads an amount typed by a person or found in a file: "KES 20,000", "20000.5", Decimal("15000").

    Rejects anything that is not a plain number, more than 2 decimal places, and negatives
    unless allowed. Raises ValidationError with a message fit to show the user.
    """
    if isinstance(value, float):
        raise TypeError("Money must not be a float.")
    text = _STRIP.sub("", str(value if value is not None else ""))
    if not re.fullmatch(r"-?\d+(\.\d+)?", text):
        raise ValidationError(_("Enter an amount, like 15000 or 15,000.50."))
    amount = Decimal(text)
    if amount != amount.quantize(CENT, rounding=ROUND_HALF_UP):
        raise ValidationError(_("Use at most 2 decimal places."))
    amount = amount.quantize(CENT)
    if amount < 0 and not allow_negative:
        raise ValidationError(_("The amount cannot be negative."))
    if amount == 0 and not allow_zero:
        raise ValidationError(_("Enter an amount above zero."))
    if abs(amount) > MAX:
        raise ValidationError(_("That amount is too large."))
    return amount


def days_in_month(day: date) -> int:
    return calendar.monthrange(day.year, day.month)[1]


def prorate(monthly, days_used: int, month_days: int) -> Decimal:
    """round(monthly × days_used ÷ days_in_month, 2). A full month is exactly the monthly amount."""
    if not 0 <= days_used <= month_days:
        raise ValueError("days_used must be between 0 and the days in the month.")
    monthly = Decimal(monthly)
    if days_used == month_days:
        return round_money(monthly)
    return round_money(monthly * days_used / month_days)


def prorate_between(monthly, first: date, last: date) -> Decimal:
    """Share of one calendar month's amount for the days first..last inclusive, both in that month."""
    if (first.year, first.month) != (last.year, last.month) or last < first:
        raise ValueError("first and last must be in one month, in order.")
    return prorate(monthly, (last - first).days + 1, days_in_month(first))


def format_money(value, currency: str = "KES") -> str:
    """KES 20,000.00. Negative amounts read KES -1,500.00."""
    if value is None or value == "":
        return ""
    try:
        amount = round_money(value)
    except (InvalidOperation, TypeError, ValueError):
        return str(value)
    return f"{currency} {amount:,.2f}"
