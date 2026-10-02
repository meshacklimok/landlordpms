from django import template

register = template.Library()


@register.filter
def overdue(invoice, today):
    """{% if invoice|overdue:today %}"""
    return invoice.is_overdue(today)


@register.filter
def bucket(buckets, key):
    """{{ row.buckets|bucket:key }}"""
    return buckets.get(key)
