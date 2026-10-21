"""The HTML version of every email we send: NyumbaPMS brand on top, the message, a quiet footer.

The plain-text body stays the source of truth and goes along as the fallback part. The HTML is made from it:
- a line that is only a web address becomes a button (the first) or a link,
- "Label: value" lines in a row become a small summary table,
- a line that is only a 4-8 digit code becomes a large code box,
- everything after the "-- " line is set apart in small grey type.
Layout is tables with inline styles because mail apps ignore most other CSS (and SVG, so the logo is a PNG).
"""

import re
from html import escape

from django.conf import settings

FOOTER_MARK = "\n-- \n"
URL = re.compile(r"(https?://[^\s<]+)")
ONLY_URL = re.compile(r"^https?://\S+$")
PAIR = re.compile(r"^([^:\n]{2,32}):\s+(\S.*)$")
CODE = re.compile(r"^\d{4,8}$")
CODE_IN_TEXT = re.compile(r"\b(\d{6})\b")

# The brand tokens (static/css/app.css): green button, warm neutral ink.
INK, MUTED, LINE, PAGE = "#1f2933", "#5f6b76", "#e3e7ea", "#f3f5f4"
BRAND, BRAND_SOFT = "#146c4a", "#e7f3ed"
ACCENT = BRAND
FONT = "-apple-system,'Segoe UI',Inter,Helvetica,Arial,sans-serif"


# Dark mode follows the reader's phone or mail app (prefers-color-scheme), with the app's own dark tokens
# (static/css/app.css): dark page, lighter card, mint green. Mail apps that ignore it show the light email.
DARK = (
    "<style>"
    ":root{color-scheme:light dark;supported-color-schemes:light dark}"
    "@media (prefers-color-scheme:dark){"
    ".bg-page{background:#0f1411 !important}"
    ".bg-card{background:#171d1a !important}"
    ".ln{border-color:#2b3530 !important}"
    ".tint{background:#1c231f !important}"
    ".ink{color:#dfe5e0 !important}"
    ".muted{color:#a3aca6 !important}"
    ".lk{color:#6fd3a1 !important}"
    ".bg-brand{background:#6fd3a1 !important}"
    ".on-brand{color:#05281a !important}"
    ".soft{background:#1c2b24 !important}"
    ".brand{color:#6fd3a1 !important}"
    "}</style>"
)


def _wordmark(name: str) -> str:
    """The brand name with "PMS" in green, as in the app; any other name is plain text."""
    if name == "NyumbaPMS":
        return f'Nyumba<span class="brand" style="color:{BRAND};">PMS</span>'
    return escape(name)


STOP = re.compile(r"(To stop these emails|Kusitisha barua pepe hizi),? (?:open|fungua): (https?://\S+)")
STOP_LABEL = {"To stop these emails": "Stop these emails", "Kusitisha barua pepe hizi": "Sitisha barua pepe hizi"}


def _footer_reason(foot: str) -> str:
    """The footer sentence, with the long stop address turned into a short link."""
    if not foot:
        return ""
    m = STOP.search(foot)
    if not m:
        return _linked(foot).replace("\n", "<br>")
    head = foot[:m.start()].rstrip()
    link = (f'<a class="lk" href="{escape(m.group(2))}" style="color:{ACCENT};">'
            f'{STOP_LABEL[m.group(1)]}</a>')
    return (_linked(head).replace("\n", "<br>") + (" " if head else "") + link + _linked(foot[m.end():])).strip()


def _footer(reason_html: str, org_name: str) -> str:
    """Why the person got this, where to get help, the legal links, and the platform line."""
    site = settings.SITE_URL
    help_bits = [f'<a class="lk" href="{site}/help/" style="color:{ACCENT};">Help centre</a>']
    support = getattr(settings, "SUPPORT_EMAIL", "")
    if support:
        help_bits.append(f'<a class="lk" href="mailto:{escape(support)}" style="color:{ACCENT};">{escape(support)}</a>')
    from core.contacts import support_whatsapp

    wa = support_whatsapp()
    if wa:
        help_bits.append(f'<a class="lk" href="https://wa.me/{wa}" style="color:{ACCENT};">WhatsApp us</a>')
    legal = " &nbsp;·&nbsp; ".join(
        f'<a class="muted" href="{site}/{path}/" style="color:{MUTED};">{label}</a>'
        for label, path in (("Terms", "terms"), ("Privacy", "privacy")))
    why = (f'<p class="muted" style="margin:0 0 14px;font-size:12px;line-height:1.6;color:{MUTED};">{reason_html}</p>'
           if reason_html else "")
    if org_name == "NyumbaPMS":
        lead = ""
    else:
        lead = "Powered by "
    return (
        f'<tr><td class="ln" style="padding:20px 32px 26px;border-top:1px solid {LINE};background:transparent;'
        f'font-size:12px;line-height:1.6;color:{MUTED};">{why}'
        f'<p class="ink" style="margin:0 0 4px;font-size:13px;font-weight:600;color:{INK};">Need a hand?</p>'
        f'<p class="muted" style="margin:0 0 16px;font-size:12px;line-height:1.6;color:{MUTED};">'
        f'{" &nbsp;·&nbsp; ".join(help_bits)}</p>'
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" class="ln" '
        f'style="border-top:1px solid {LINE};"><tr><td style="padding-top:14px;font-size:12px;line-height:1.6;'
        f'color:{MUTED};" class="muted"><span class="ink" style="color:{INK};font-weight:700;">'
        f'{lead}{_wordmark("NyumbaPMS")}</span><br>'
        f'{legal} &nbsp;·&nbsp; <a class="muted" href="{site}" style="color:{MUTED};">'
        f'{escape(site.split("//", 1)[-1])}</a>'
        f'</td></tr></table></td></tr>')


def _logo_url() -> str:
    return settings.SITE_URL + "/static/icons/apple-touch-icon.png"


def _linked(text: str) -> str:
    """Escaped text with web addresses made into links."""
    return URL.sub(lambda m: f'<a class="lk" href="{m.group(1)}" style="color:{ACCENT};word-break:break-all;">{m.group(1)}</a>',
                   escape(text))


def _button(url: str, label: str) -> str:
    return (f'<table role="presentation" cellpadding="0" cellspacing="0" style="margin:6px 0 18px;"><tr>'
            f'<td class="bg-brand" style="background:{BRAND};border-radius:8px;">'
            f'<a class="on-brand" href="{escape(url)}" style="display:inline-block;padding:12px 22px;font-size:15px;font-weight:600;'
            f'color:#ffffff;text-decoration:none;font-family:{FONT};">{escape(label)}</a></td></tr></table>'
            f'<p class="muted" style="margin:0 0 16px;font-size:12px;line-height:1.5;color:{MUTED};">If the button does not work, '
            f'copy this address into your browser:<br><a class="lk" href="{escape(url)}" style="color:{ACCENT};'
            f'word-break:break-all;">{escape(url)}</a></p>')


def _code(code: str) -> str:
    return (f'<div class="soft brand" style="margin:6px 0 18px;padding:16px;background:{BRAND_SOFT};border-radius:8px;text-align:center;'
            f'font-size:30px;font-weight:700;letter-spacing:8px;color:{BRAND};font-family:Consolas,Menlo,monospace;">'
            f'{escape(code)}</div>')


def _pairs(lines: list[str]) -> str:
    rows = "".join(
        f'<tr><td class="muted ln" style="padding:8px 12px;font-size:13px;color:{MUTED};border-bottom:1px solid {LINE};">'
        f'{escape(m.group(1))}</td><td class="ink ln" align="right" style="padding:8px 12px;font-size:14px;font-weight:600;'
        f'color:{INK};border-bottom:1px solid {LINE};">{_linked(m.group(2))}</td></tr>'
        for m in (PAIR.match(x) for x in lines))
    return (f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" class="tint ln" style="margin:4px 0 18px;'
            f'background:#fafbfa;border:1px solid {LINE};border-radius:8px;">{rows}</table>')


def _paragraph(text: str) -> str:
    return (f'<p class="ink" style="margin:0 0 14px;font-size:15px;line-height:1.6;color:{INK};">'
            f'{_linked(text).replace(chr(10), "<br>")}</p>')


def _block(block: str, state: dict, button_label: str) -> str:
    lines = [x.strip() for x in block.strip().split("\n") if x.strip()]
    if len(lines) == 1 and CODE.match(lines[0]):
        return _code(lines[0])
    if len(lines) == 1 and ONLY_URL.match(lines[0]):
        if not state["button"]:
            state["button"] = True
            return _button(lines[0], button_label)
        return _paragraph(lines[0])
    if len(lines) >= 2 and all(PAIR.match(x) and "http" not in x.split(":")[0] for x in lines):
        return _pairs(lines)
    # A paragraph that ends in a lone address: the text, then the button.
    if len(lines) >= 2 and ONLY_URL.match(lines[-1]) and not state["button"]:
        state["button"] = True
        return _paragraph("\n".join(lines[:-1])) + _button(lines[-1], button_label)
    # A code sentence ("Your code is 123456.") shows the code large below the sentence.
    one = " ".join(lines)
    m = CODE_IN_TEXT.search(one)
    if m and re.search(r"\bcode\b", one, re.I) and not state["code"]:
        state["code"] = True
        return _paragraph(block.strip()) + _code(m.group(1))
    return _paragraph(block.strip())


def _blocks(text: str, button_label: str) -> str:
    state = {"button": False, "code": False}
    parts = [b for b in re.split(r"\n\s*\n", text.strip()) if b.strip()]
    return "".join(_block(b, state, button_label) for b in parts)


GREETING = re.compile(r"^(dear|hello|hi|mpendwa|habari)\b", re.I)


def preheader_for(main: str, limit: int = 110) -> str:
    """The grey line inbox lists show after the subject: the first real sentence, without the greeting or links."""
    for block in re.split(r"\n\s*\n", main.strip()):
        text = URL.sub("", " ".join(block.split())).strip()
        if text and not GREETING.match(text):
            return text if len(text) <= limit else text[:limit - 1].rsplit(" ", 1)[0] + "…"
    return ""


def _preheader(text: str) -> str:
    """Hidden in the email itself; the padding stops mail apps from filling the rest of the line with the body."""
    if not text:
        return ""
    return (f'<div style="display:none;max-height:0;overflow:hidden;opacity:0;mso-hide:all;font-size:1px;'
            f'line-height:1px;color:{PAGE};">{escape(text)}{"&#847;&zwnj;&nbsp;" * 40}</div>')


def render_html(org_name: str, body: str, *, note: str = "", button_label: str = "Open", logo_cid: str = "",
                preheader: str = "") -> str:
    """`body` may end in the "-- " footer the sender adds.

    An email from a landlord leads with their name and logo (`logo_cid` names the inline image, else their initial);
    NyumbaPMS is credited under the name and in the footer. Our own emails lead with NyumbaPMS."""
    main, _sep, foot = body.partition(FOOTER_MARK)
    foot = foot.strip()
    footer_text = _footer_reason(foot)
    sub = f'<div class="muted" style="font-size:12px;color:{MUTED};margin-top:2px;">{escape(note)}</div>' if note else ""
    # The organization's name leads when the email is from a landlord; "via NyumbaPMS" credits the platform.
    brand_name = "NyumbaPMS"
    title = _wordmark(org_name)
    via = "" if org_name == brand_name else (f'<div class="muted" style="font-size:11px;color:{MUTED};margin-top:2px;">'
                                             f'Powered by {_wordmark(brand_name)}</div>')
    if logo_cid:
        mark = (f'<img src="cid:{escape(logo_cid)}" height="40" alt="{escape(org_name)}" '
                f'style="display:block;height:40px;width:auto;max-width:140px;border:0;border-radius:6px;">')
    elif org_name != brand_name:
        initial = escape((org_name.strip() or "?")[0].upper())
        mark = (f'<div class="bg-brand on-brand" style="width:40px;height:40px;line-height:40px;border-radius:9px;'
                f'background:{BRAND};color:#ffffff;text-align:center;font-size:18px;font-weight:700;">{initial}</div>')
    else:
        mark = (f'<img src="{escape(_logo_url())}" width="40" height="40" alt="NyumbaPMS" '
                f'style="display:block;border-radius:9px;border:0;">')
    footer = _footer(footer_text, org_name)
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<meta name="color-scheme" content="light dark">'
        '<meta name="supported-color-schemes" content="light dark">' + DARK + '</head>'
        f'<body class="bg-page" style="margin:0;padding:0;background:{PAGE};">'
        + _preheader(preheader or preheader_for(main)) +
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" class="bg-page" style="background:{PAGE};">'
        '<tr><td align="center" style="padding:28px 12px;">'
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" class="bg-card ln" style="max-width:580px;'
        f'background:#ffffff;border:1px solid {LINE};border-radius:12px;overflow:hidden;font-family:{FONT};">'
        f'<tr><td class="bg-brand" style="height:5px;background:{BRAND};font-size:0;line-height:0;">&nbsp;</td></tr>'
        f'<tr><td class="ln" style="padding:22px 32px 18px;border-bottom:1px solid {LINE};">'
        f'<table role="presentation" cellpadding="0" cellspacing="0"><tr>'
        f'<td style="padding-right:12px;">{mark}</td>'
        f'<td><div class="ink" style="font-size:18px;font-weight:700;color:{INK};letter-spacing:-.01em;">{title}</div>'
        f'{via}{sub}</td></tr></table></td></tr>'
        f'<tr><td style="padding:28px 32px 10px;">{_blocks(main, button_label)}</td></tr>'
        f'{footer}</table>'
        f'<p class="muted" style="max-width:580px;margin:14px 0 0;font-size:11px;line-height:1.5;color:{MUTED};'
        f'font-family:{FONT};text-align:center;">Never share a sign-in code or a private link with anyone. '
        f'NyumbaPMS staff will never ask you for one.</p>'
        '</td></tr></table></body></html>'
    )


LOGO_CID = "orglogo"
LOGO_MAX_BYTES = 400_000


def inline_logo(org):
    """The organization's logo as an inline image part, or None when it has none or it is too big or unreadable."""
    from email.mime.image import MIMEImage

    try:
        if org is None or not org.logo:
            return None
        with org.logo.open("rb") as f:
            data = f.read(LOGO_MAX_BYTES + 1)
        if not data or len(data) > LOGO_MAX_BYTES:
            return None
        part = MIMEImage(data)
    except Exception:  # noqa: BLE001 - the email goes without the logo
        return None
    part.add_header("Content-ID", f"<{LOGO_CID}>")
    part.add_header("Content-Disposition", "inline", filename="logo")
    return part


def attach_html(message, body: str, *, org_name: str = "NyumbaPMS", button_label: str = "Open"):
    """Gives a plain `EmailMessage` the branded HTML version of `body` and returns it."""
    from django.core.mail import EmailMultiAlternatives

    if not isinstance(message, EmailMultiAlternatives):
        # EmailMessage cannot carry an alternative; swap it for a multi-part one with the same content.
        alt = EmailMultiAlternatives(message.subject, message.body, message.from_email, message.to,
                                     cc=message.cc, bcc=message.bcc, reply_to=message.reply_to,
                                     headers=message.extra_headers, connection=message.connection)
        alt.attachments = list(message.attachments)
        message = alt
    message.attach_alternative(render_html(org_name, body, button_label=button_label), "text/html")
    return message


def send_branded(subject: str, body: str, to: list[str], *, using: str, from_email: str | None = None,
                 org_name: str = "NyumbaPMS", button_label: str = "Open", attachments=(), org=None) -> None:
    """Sends `body` as plain text with the branded HTML version beside it. Raises what the mailer raises."""
    from django.core.mail import EmailMultiAlternatives

    message = EmailMultiAlternatives(subject, body, from_email, to)
    logo = inline_logo(org)
    message.attach_alternative(render_html(org_name, body, button_label=button_label,
                                           logo_cid=LOGO_CID if logo else ""), "text/html")
    if logo is not None:
        message.attach(logo)
    for name, content, mimetype in attachments:
        message.attach(name, content, mimetype)
    message.send(using=using)


def subscription_link() -> str:
    """The Subscription page address, set on its own line at the end of a billing email so it becomes a button."""
    from django.urls import reverse

    return settings.SITE_URL + reverse("subscriptions:page")
