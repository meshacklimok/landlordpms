from django import template

from core.money import format_money

register = template.Library()


@register.filter
def money(value, currency="KES"):
    """{{ invoice.total|money }} → KES 20,000.00"""
    return format_money(value, currency or "KES")


@register.filter
def absolute(value):
    """{{ balance|absolute|money }}: the size of a signed amount, when the sign is said in words."""
    try:
        return abs(value)
    except TypeError:
        return value
