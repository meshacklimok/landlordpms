from django import template

register = template.Library()


@register.filter
def percent(value) -> str:
    """A fraction as a whole percent, 0.845 → 85%; no rate → a dash (never 0%)."""
    if value is None:
        return "—"
    return f"{value * 100:.0f}%"
