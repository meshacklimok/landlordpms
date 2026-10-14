"""The HTML version of a tenant email: the organization's name on top, the message, a quiet footer.

The plain-text body stays the source of truth and goes along as the fallback part. Layout is tables with inline
styles because mail apps ignore most other CSS.
"""

import re
from html import escape

FOOTER_MARK = "\n-- \n"
URL = re.compile(r"(https?://[^\s<]+)")

INK, MUTED, LINE, PAGE = "#1f2933", "#6b7280", "#e5e7eb", "#f4f5f7"
ACCENT = "#1d4e89"


def _linked(text: str) -> str:
    """Escaped text with web addresses made into links."""
    return URL.sub(lambda m: f'<a href="{m.group(1)}" style="color:{ACCENT};">{m.group(1)}</a>', escape(text))


def _paragraphs(text: str) -> str:
    blocks = [b for b in re.split(r"\n\s*\n", text.strip()) if b.strip()]
    return "".join(
        f'<p style="margin:0 0 14px;font-size:15px;line-height:1.55;color:{INK};">'
        f'{_linked(b.strip()).replace(chr(10), "<br>")}</p>'
        for b in blocks
    )


def render_html(org_name: str, body: str, *, note: str = "") -> str:
    """`body` may end in the "-- " footer the sender adds; it is set apart in small grey type."""
    main, _sep, foot = body.partition(FOOTER_MARK)
    foot = foot.strip()
    footer = (f'<tr><td style="padding:16px 28px 22px;border-top:1px solid {LINE};font-size:12px;'
              f'line-height:1.5;color:{MUTED};">{_linked(foot)}</td></tr>') if foot else ""
    sub = f'<div style="font-size:12px;color:{MUTED};margin-top:2px;">{escape(note)}</div>' if note else ""
    return (
        '<!doctype html><html><body style="margin:0;padding:0;background:' + PAGE + ';">'
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:{PAGE};">'
        '<tr><td align="center" style="padding:24px 12px;">'
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:560px;'
        f'background:#ffffff;border:1px solid {LINE};border-radius:6px;'
        'font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif;">'
        f'<tr><td style="padding:20px 28px;border-bottom:3px solid {ACCENT};">'
        f'<div style="font-size:17px;font-weight:700;color:{INK};">{escape(org_name)}</div>{sub}</td></tr>'
        f'<tr><td style="padding:24px 28px 8px;">{_paragraphs(main)}</td></tr>'
        f'{footer}</table></td></tr></table></body></html>'
    )
