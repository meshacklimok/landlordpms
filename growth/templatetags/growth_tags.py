import json

from django import template
from django.utils.safestring import mark_safe

register = template.Library()


@register.filter
def faq_jsonld(pairs):
    """The questions as schema.org FAQPage data, so search engines can show them. Not executed by browsers, so the
    page's script policy does not apply; `<` is escaped so an answer can never close the tag."""
    data = {"@context": "https://schema.org", "@type": "FAQPage", "mainEntity": [
        {"@type": "Question", "name": q, "acceptedAnswer": {"@type": "Answer", "text": a}} for q, a in pairs]}
    return mark_safe(json.dumps(data, ensure_ascii=False).replace("<", "\\u003c").replace(">", "\\u003e")
                     .replace("&", "\\u0026"))


@register.simple_tag
def footer_solutions():
    """The solution pages for the public footer's Solutions column (D-158)."""
    from growth.solutions import SOLUTIONS

    return [{"slug": s["slug"], "title": s["title"]} for s in SOLUTIONS]
