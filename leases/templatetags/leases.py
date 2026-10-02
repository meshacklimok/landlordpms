from django import template

register = template.Library()


@register.filter
def expiring(lease, today) -> bool:
    return lease.is_expiring(today)
