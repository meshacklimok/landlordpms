"""Public articles: reading them, and turning the text a writer typed into safe HTML (D-178).

A writer types plain text with a few marks. Everything is escaped first and the marks are turned into tags after,
so no article can carry a script, an image or an attribute of its own.

    ## A heading            ### A smaller heading
    - a bullet               1. a numbered step
    **bold**   *italic*   [link text](https://example.com)   [link text](/pricing/)
    > A note set apart
    A blank line starts a new paragraph.
"""

import re

from django.db.models import Case, IntegerField, Value, When
from django.utils import timezone
from django.utils.html import escape
from django.utils.safestring import mark_safe
from django.utils.text import slugify

from .models import Article

_BOLD = re.compile(r"\*\*(.+?)\*\*")
_ITALIC = re.compile(r"(?<![*\w])\*(?!\s)(.+?)(?<!\s)\*(?![*\w])")
_LINK = re.compile(r"\[([^\]]+)\]\((https?://[^\s)]+|/[^\s)]*)\)")
_NUMBERED = re.compile(r"^\d{1,2}[.)]\s+")
_BULLET = re.compile(r"^[-*]\s+")


def _inline(text: str) -> str:
    out = escape(text)

    def link(m):
        url = m.group(2)
        external = url.startswith("http")
        extra = ' target="_blank" rel="noopener"' if external else ""
        return f'<a href="{url}"{extra}>{m.group(1)}</a>'

    out = _LINK.sub(link, out)
    out = _BOLD.sub(r"<strong>\1</strong>", out)
    return _ITALIC.sub(r"<em>\1</em>", out)


def render(body: str):
    """(html, toc): the article as safe HTML, and [(anchor, heading)] for its main headings."""
    html, toc, used = [], [], set()
    for block in re.split(r"\n\s*\n", (body or "").replace("\r\n", "\n").strip()):
        lines = [ln.strip() for ln in block.split("\n") if ln.strip()]
        if not lines:
            continue
        first = lines[0]
        if first.startswith("## ") or first.startswith("### "):
            level = 3 if first.startswith("###") else 2
            text = first[level + 1:].strip()
            if level == 2:
                anchor = base = slugify(text) or "section"
                n = 2
                while anchor in used:
                    anchor, n = f"{base}-{n}", n + 1
                used.add(anchor)
                toc.append((anchor, text))
                html.append(f'<h2 id="{anchor}">{_inline(text)}</h2>')
            else:
                html.append(f"<h3>{_inline(text)}</h3>")
            lines = lines[1:]
            if not lines:
                continue
            first = lines[0]
        if all(_BULLET.match(ln) for ln in lines):
            html.append("<ul>" + "".join(f"<li>{_inline(_BULLET.sub('', ln))}</li>" for ln in lines) + "</ul>")
        elif all(_NUMBERED.match(ln) for ln in lines):
            html.append("<ol>" + "".join(f"<li>{_inline(_NUMBERED.sub('', ln))}</li>" for ln in lines) + "</ol>")
        elif first.startswith(">"):
            html.append("<blockquote class=\"pub-note\"><p>" + _inline(" ".join(ln.lstrip("> ").strip() for ln in lines))
                        + "</p></blockquote>")
        else:
            html.append("<p>" + _inline(" ".join(lines)) + "</p>")
    return mark_safe("\n".join(html)), toc  # noqa: S308 - _inline escapes the text before the tags are added


def plain_text(body: str) -> str:
    """The article without marks, for search data and the AI summary."""
    text = _LINK.sub(r"\1", body or "")
    text = _BOLD.sub(r"\1", text)
    text = re.sub(r"^(#{2,3} |[-*] |\d{1,2}[.)] |> ?)", "", text, flags=re.M)
    return re.sub(r"\s+", " ", text).strip()


def published():
    """Articles a visitor may read: switched on and dated no later than now."""
    return Article.objects.filter(is_published=True, published_at__lte=timezone.now())


def get_published(slug: str):
    return published().filter(slug=slug).first() if slug else None


def related(article, limit: int = 3):
    """Others in the same category first, then the newest of the rest."""
    same = Case(When(category=article.category, then=Value(0)), default=Value(1), output_field=IntegerField())
    return list(published().exclude(pk=article.pk).annotate(_near=same).order_by("_near", "-published_at")[:limit])
