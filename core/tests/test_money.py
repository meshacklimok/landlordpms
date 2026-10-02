from datetime import date
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.template import Context, Template

from core.money import days_in_month, format_money, parse_money, prorate, prorate_between, round_money


@pytest.mark.parametrize("raw, expected", [
    ("20000", "20000.00"), ("KES 20,000", "20000.00"), ("ksh 1,500.5", "1500.50"), ("Kshs 99", "99.00"),
    (" 0 ", "0.00"), (Decimal("15000"), "15000.00"), (15000, "15000.00"), ("12.30", "12.30"),
])
def test_parse_money_accepts(raw, expected):
    assert parse_money(raw) == Decimal(expected)


@pytest.mark.parametrize("raw", ["", None, "abc", "1.2.3", "1e5", "NaN", "Infinity", "12.345", "--1", "1,000.001"])
def test_parse_money_rejects(raw):
    with pytest.raises(ValidationError):
        parse_money(raw)


def test_parse_money_sign_and_zero_rules():
    with pytest.raises(ValidationError):
        parse_money("-1")
    assert parse_money("-1", allow_negative=True) == Decimal("-1.00")
    with pytest.raises(ValidationError):
        parse_money("0", allow_zero=False)
    with pytest.raises(ValidationError):
        parse_money("1000000000000")
    with pytest.raises(TypeError):
        parse_money(1.5)


def test_round_half_up_at_half_a_cent():
    assert round_money(Decimal("0.005")) == Decimal("0.01")
    assert round_money(Decimal("0.004")) == Decimal("0.00")
    assert round_money(Decimal("2.675")) == Decimal("2.68")  # a float would give 2.67
    assert round_money(Decimal("-0.005")) == Decimal("-0.01")


def test_days_in_month_handles_leap_years():
    assert days_in_month(date(2028, 2, 10)) == 29
    assert days_in_month(date(2026, 2, 10)) == 28
    assert days_in_month(date(2100, 2, 1)) == 28
    assert days_in_month(date(2026, 1, 31)) == 31


def test_prorate():
    assert prorate(Decimal("15000"), 30, 30) == Decimal("15000.00")
    assert prorate(Decimal("15000"), 0, 30) == Decimal("0.00")
    assert prorate(Decimal("15000"), 10, 31) == Decimal("4838.71")  # 4838.709…
    assert prorate(Decimal("10000"), 1, 3) == Decimal("3333.33")
    assert prorate(Decimal("0.03"), 1, 2) == Decimal("0.02")  # 0.015 rounds up
    with pytest.raises(ValueError):
        prorate(Decimal("1"), 32, 31)


def test_prorate_between():
    # Moving in on 20 Feb in a leap year: 10 of 29 days.
    assert prorate_between(Decimal("29000"), date(2028, 2, 20), date(2028, 2, 29)) == Decimal("10000.00")
    assert prorate_between(Decimal("31000"), date(2026, 1, 1), date(2026, 1, 31)) == Decimal("31000.00")
    assert prorate_between(Decimal("31000"), date(2026, 1, 15), date(2026, 1, 15)) == Decimal("1000.00")
    with pytest.raises(ValueError):
        prorate_between(Decimal("1"), date(2026, 1, 30), date(2026, 2, 2))


def test_format_money():
    assert format_money(Decimal("20000")) == "KES 20,000.00"
    assert format_money(Decimal("1234567.5")) == "KES 1,234,567.50"
    assert format_money(Decimal("-1500")) == "KES -1,500.00"
    assert format_money(None) == ""
    assert format_money(Decimal("0.005"), "USD") == "USD 0.01"


def test_money_template_filter():
    out = Template("{% load money %}{{ v|money }}").render(Context({"v": Decimal("15000")}))
    assert out == "KES 15,000.00"
