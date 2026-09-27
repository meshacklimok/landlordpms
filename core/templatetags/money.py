from django import template

from core.money import format_money

register = template.Library()


@register.filter
def money(value, currency="KES"):
    """{{ invoice.total|money }} → KES 20,000.00"""
    return format_money(value, currency or "KES")
