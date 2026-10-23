"""The NyumbaPMS look on paper: colours, a letterhead with the organization's logo, and a footer with page numbers.

Every PDF we draw (invoices, receipts, letters, agreements, statements, packs, import sheets) takes its colours from
here, so a printout from any part of the app looks like it came from the same system. The organization's own logo
and colour sit in the letterhead; ours is a small mark in the footer.
"""

from xml.sax.saxutils import escape

from django.conf import settings
from django.utils.translation import gettext as _
from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader, simpleSplit
from reportlab.pdfgen import canvas as pdfcanvas
from reportlab.platypus import (
    BaseDocTemplate,
    Flowable,
    Frame,
    Image,
    PageTemplate,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

PRIMARY = colors.HexColor("#146C4A")  # the app's green
INK = colors.HexColor("#14201A")
MUTED = colors.HexColor("#5B6660")
LINE = colors.HexColor("#D9DFDB")
TINT = colors.HexColor("#EEF4F0")
WARN = colors.HexColor("#B02A37")

_BODY = ParagraphStyle("brand_body", fontName="Helvetica", fontSize=10, leading=14, textColor=INK)
_NAME = ParagraphStyle("brand_name", parent=_BODY, fontName="Helvetica-Bold", fontSize=15, leading=19)
_SMALL = ParagraphStyle("brand_small", parent=_BODY, fontSize=8, leading=11, textColor=MUTED)
_KIND = ParagraphStyle("brand_kind", parent=_BODY, fontName="Helvetica-Bold", fontSize=14, leading=18, alignment=2)
_NUMBER = ParagraphStyle("brand_number", parent=_SMALL, alignment=2)


def accent(org=None):
    """The organization's chosen colour, or ours."""
    colour = getattr(org, "brand_color", "") if org is not None else ""
    try:
        return colors.HexColor(colour) if colour else PRIMARY
    except ValueError:
        return PRIMARY


def picture(field, high, widest=50 * mm):
    """An uploaded picture scaled to `high` tall (at most `widest` wide), or None if absent or unreadable."""
    if not field:
        return None
    try:
        width, height = ImageReader(field.path).getSize()
        wide = min(high * width / height, widest)
        return Image(field.path, width=wide, height=wide * height / width, mask="auto")
    except Exception:  # missing file or an unreadable picture: print the document without it
        return None


def draw_mark(canvas, x, y, size, tile=PRIMARY, glyph=colors.white):
    """The NyumbaPMS mark (a roof over an N) with its lower left corner at x, y."""
    scale = size / 64

    def path(points):
        p = canvas.beginPath()
        for i, (u, v) in enumerate(points):
            (p.moveTo if i == 0 else p.lineTo)(x + u * scale, y + (64 - v) * scale)
        p.close()
        return p

    canvas.saveState()
    canvas.setFillColor(tile)
    canvas.roundRect(x, y, size, size, 15 * scale, stroke=0, fill=1)
    canvas.setFillColor(glyph)
    canvas.setStrokeColor(glyph)
    canvas.setLineWidth(2.4 * scale)
    canvas.setLineJoin(1)
    roof = [(17, 21.75), (32, 10.5), (47, 21.75), (47, 31.75), (32, 20.5), (17, 31.75)]
    letter = [(17, 53.5), (17, 35.75), (25, 29.75), (39, 42.25), (39, 29.75), (47, 35.75), (47, 53.5), (39, 53.5),
              (25, 41), (25, 53.5)]
    canvas.drawPath(path(roof), stroke=1, fill=1)
    canvas.drawPath(path(letter), stroke=1, fill=1)
    canvas.restoreState()


class Mark(Flowable):
    """Our mark as a flowable, for documents whose organization has no logo of its own."""

    def __init__(self, size):
        super().__init__()
        self.size = size
        self.width = self.height = size
        self.hAlign = "LEFT"

    def draw(self):
        draw_mark(self.canv, 0, 0, self.size)


def site_link() -> tuple[str, str]:
    """Our address as (shown text, full URL) for the footer."""
    url = settings.SITE_URL
    return url.split("://", 1)[-1], url


def letterhead(name, *, org=None, kind="", number="", lines=(), width=None):
    """The top of a document: logo, name and contact on the left, what the document is on the right, an accent rule."""
    colour = accent(org)
    left = []
    logo = picture(getattr(org, "logo", None), 15 * mm) if org is not None else None
    if logo is None:
        logo = Mark(12 * mm)  # no logo of their own: ours, so the page never starts bare
    logo.hAlign = "LEFT"
    left += [logo, Spacer(1, 2 * mm)]
    left.append(Paragraph(escape(str(name)), _NAME))
    left += [Paragraph(escape(str(line)), _SMALL) for line in lines if line]
    right = []
    if kind:
        right.append(Paragraph(escape(str(kind)), ParagraphStyle("brand_kind_c", parent=_KIND, textColor=colour)))
    if number:
        right.append(Paragraph(escape(str(number)), _NUMBER))
    table = Table([[left, right]], colWidths=[None, 62 * mm] if width is None else [width - 62 * mm, 62 * mm])
    table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0), ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ("LINEBELOW", (0, 0), (-1, 0), 1.2, colour),
    ]))
    return table


def org_lines(org) -> list[str]:
    """Address, contact and KRA PIN of an organization, one per line, empty ones left out."""
    contact = " · ".join(x for x in (org.billing_phone, org.billing_email) if x)
    pin = _("KRA PIN %(pin)s") % {"pin": org.kra_pin} if org.kra_pin else ""
    return [x for x in (org.address, contact, pin) if x]


def _draw_tagline(canvas, centre, y):
    """Our motto and a clickable address, centred on `centre`, on the last line of the page."""
    shown, url = site_link()
    motto = _("Every shilling accounted for.")
    font, size, gap = "Helvetica", 7, 2 * mm
    motto_width = canvas.stringWidth(motto, font, size)
    link_width = canvas.stringWidth(shown, font, size)
    x = centre - (motto_width + gap + link_width) / 2
    canvas.setFont(font, size)
    canvas.setFillColor(MUTED)
    canvas.drawString(x, y, motto)
    x += motto_width + gap
    canvas.setFillColor(PRIMARY)
    canvas.drawString(x, y, shown)
    canvas.linkURL(url, (x, y - 1.5, x + link_width, y + size), relative=0)


def _footer_canvas(label, left, right):
    class NumberedCanvas(pdfcanvas.Canvas):
        """Draws the accent band at the top and the footer (label, mark, page n of N) on every page."""

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._saved = []

        def showPage(self):
            self._saved.append(dict(self.__dict__))
            self._startPage()

        def save(self):
            total = len(self._saved)
            for state in self._saved:
                self.__dict__.update(state)
                self._decorate(total)
                super().showPage()
            super().save()

        def _decorate(self, total):
            width, height = self._pagesize
            self.saveState()
            self.setFillColor(PRIMARY)
            self.rect(0, height - 2.2 * mm, width, 2.2 * mm, stroke=0, fill=1)
            self.setStrokeColor(LINE)
            self.setLineWidth(0.4)
            self.line(left, 11.5 * mm, width - right, 11.5 * mm)
            self.setFont("Helvetica", 7.5)
            self.setFillColor(MUTED)
            self.drawString(left, 7.5 * mm, str(label)[:55])
            self.drawRightString(width - right, 7.5 * mm, _("Page %(n)s of %(total)s") % {
                "n": self._pageNumber, "total": total})
            centre = width / 2
            draw_mark(self, centre - 17 * mm, 6.2 * mm, 4.4 * mm)
            self.setFont("Helvetica-Bold", 7.5)
            self.setFillColor(MUTED)
            self.drawString(centre - 11.8 * mm, 7.5 * mm, "NyumbaPMS")
            # The page state was restored from a snapshot, so the annotation counter restarted: keep link names unique.
            self._annotationCount = self._pageNumber * 10
            _draw_tagline(self, centre, 3.4 * mm)
            self.restoreState()

    return NumberedCanvas


class Doc(SimpleDocTemplate):
    """A document template with the branded top band and footer (title, our mark, page n of N) on every page."""

    def build(self, flowables, **kwargs):
        kwargs.setdefault("canvasmaker", _footer_canvas(self.title or "", self.leftMargin, self.rightMargin))
        # A frame with no padding, so body, letterhead and footer line share the same left and right edges.
        self._calc()
        frame = Frame(self.leftMargin, self.bottomMargin, self.width, self.height, id="normal",
                      leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
        self.addPageTemplates([PageTemplate(id="First", frames=frame, pagesize=self.pagesize),
                               PageTemplate(id="Later", frames=frame, pagesize=self.pagesize)])
        BaseDocTemplate.build(self, flowables, **kwargs)


def qr_drawing(url, size):
    """A QR code for `url` as a flowable, `size` points square, black on white with a quiet border."""
    import segno
    from reportlab.graphics.shapes import Drawing, Rect

    matrix = [list(row) for row in segno.make(url, error="m", boost_error=False).matrix]
    quiet = size * 0.06
    step = (size - 2 * quiet) / len(matrix)
    d = Drawing(size, size)
    d.add(Rect(0, 0, size, size, fillColor=colors.white, strokeColor=None))
    for r, row in enumerate(matrix):
        for c, bit in enumerate(row):
            if bit:
                d.add(Rect(quiet + c * step, size - quiet - (r + 1) * step, step, step,
                           fillColor=colors.black, strokeColor=None))
    return d


def fit(text, font, size, width) -> str:
    """The text cut with an ellipsis so that it is no wider than `width` points."""
    from reportlab.pdfbase.pdfmetrics import stringWidth

    text = str(text)
    if stringWidth(text, font, size) <= width:
        return text
    while text and stringWidth(text + "…", font, size) > width:
        text = text[:-1]
    return text.rstrip() + "…"


class Stub:
    """A small A5 page (a receipt, a subscription invoice): header with logo, rows of label and value, a footer.

    Rows are drawn top down; nothing flows to a second page, so keep to what fits (about twenty rows).
    """

    def __init__(self, pagesize, title, name, kind, number, issued, *, org=None, lines=(), footer_label=""):
        import io

        self.buf = io.BytesIO()
        self.pdf = pdfcanvas.Canvas(self.buf, pagesize=pagesize)
        self.pdf.setTitle(title)
        self.pdf.setAuthor("NyumbaPMS")
        self.width, self.height = pagesize
        self.left, self.right = 14 * mm, self.width - 14 * mm
        self.colour = accent(org)
        self.footer_label = footer_label or title
        pdf = self.pdf
        pdf.setFillColor(PRIMARY)
        pdf.rect(0, self.height - 2.2 * mm, self.width, 2.2 * mm, stroke=0, fill=1)
        y = self.height - 9 * mm
        x = self.left
        logo = picture(getattr(org, "logo", None), 12 * mm, widest=30 * mm) if org is not None else None
        if logo is not None:
            pdf.drawImage(logo.filename, x, y - logo.drawHeight,
                          width=logo.drawWidth, height=logo.drawHeight, mask="auto", preserveAspectRatio=True)
            x += logo.drawWidth + 3 * mm
            y -= logo.drawHeight / 2 - 2 * mm
        else:  # no logo of their own: ours
            draw_mark(pdf, x, y - 10 * mm, 10 * mm)
            x += 10 * mm + 3 * mm
            y -= 5 * mm
        kind_width = 38 * mm
        pdf.setFillColor(INK)
        pdf.setFont("Helvetica-Bold", 13)
        room = self.right - x - kind_width
        name_lines = simpleSplit(str(name), "Helvetica-Bold", 13, room) or [""]
        if len(name_lines) > 2:  # a long name takes two lines; only a very long one is cut
            name_lines = [name_lines[0], fit(" ".join(name_lines[1:]), "Helvetica-Bold", 13, room)]
        line_y = y
        for text in name_lines:
            pdf.drawString(x, line_y, text)
            line_y -= 5 * mm
        pdf.setFillColor(self.colour)
        pdf.setFont("Helvetica-Bold", 12)
        pdf.drawRightString(self.right, y, kind)
        pdf.setFillColor(MUTED)
        pdf.setFont("Helvetica", 8.5)
        sub = line_y + 0.5 * mm
        for line in lines:
            pdf.drawString(x, sub, fit(line, "Helvetica", 8.5, self.right - x - kind_width))
            sub -= 4 * mm
        pdf.drawRightString(self.right, y - 4.5 * mm, number)
        pdf.drawRightString(self.right, y - 9 * mm, issued)
        y = min(sub, y - 9 * mm) - 2 * mm
        pdf.setStrokeColor(self.colour)
        pdf.setLineWidth(1.2)
        pdf.line(self.left, y, self.right, y)
        self.y = y - 8 * mm

    def row(self, label, value, bold=False):
        pdf = self.pdf
        pdf.setFillColor(MUTED)
        pdf.setFont("Helvetica", 9)
        pdf.drawString(self.left, self.y, label)
        font, size = ("Helvetica-Bold", 10) if bold else ("Helvetica", 9)
        pdf.setFillColor(INK)
        pdf.setFont(font, size)
        room = self.right - self.left - pdf.stringWidth(str(label), "Helvetica", 9) - 4 * mm
        pdf.drawRightString(self.right, self.y, fit(value, font, size, room))
        self.y -= 6 * mm

    def total(self, label, value):
        """The figure the page is about, on a tinted band."""
        pdf = self.pdf
        pdf.setFillColor(TINT)
        pdf.rect(self.left - 2 * mm, self.y - 2.2 * mm, self.right - self.left + 4 * mm, 8 * mm, stroke=0, fill=1)
        pdf.setFillColor(INK)
        pdf.setFont("Helvetica-Bold", 10)
        pdf.drawString(self.left, self.y, label)
        pdf.drawRightString(self.right, self.y, fit(value, "Helvetica-Bold", 11, 70 * mm))
        self.y -= 9 * mm

    def rule(self):
        self.y -= 2 * mm
        self.pdf.setStrokeColor(LINE)
        self.pdf.setLineWidth(0.5)
        self.pdf.line(self.left, self.y + 4 * mm, self.right, self.y + 4 * mm)

    def heading(self, text):
        self.pdf.setFillColor(self.colour)
        self.pdf.setFont("Helvetica-Bold", 9)
        self.pdf.drawString(self.left, self.y, text)
        self.y -= 6 * mm

    def note(self, text):
        self.pdf.setFillColor(MUTED)
        self.pdf.setFont("Helvetica-Oblique", 8)
        self.pdf.drawString(self.left, self.y, fit(text, "Helvetica-Oblique", 8, self.right - self.left))
        self.y -= 5 * mm

    def qr(self, url, caption):
        """A QR code above the footer, bottom right, that opens `url` (a page that checks the document)."""
        import segno

        pdf = self.pdf
        matrix = [list(row) for row in segno.make(url, error="m", boost_error=False).matrix]
        size, quiet = 19 * mm, 1.5 * mm
        step = size / len(matrix)
        x0, y0 = self.right - size, 14 * mm
        pdf.setFillColor(colors.white)
        pdf.rect(x0 - quiet, y0 - quiet, size + 2 * quiet, size + 2 * quiet, stroke=0, fill=1)
        pdf.setFillColor(colors.black)
        for r, row in enumerate(matrix):
            for c, bit in enumerate(row):
                if bit:
                    pdf.rect(x0 + c * step, y0 + size - (r + 1) * step, step, step, stroke=0, fill=1)
        pdf.setFillColor(MUTED)
        pdf.setFont("Helvetica", 6.5)
        pdf.drawRightString(x0 - quiet - 1.5 * mm, y0 + size - 3 * mm, fit(caption, "Helvetica", 6.5, 60 * mm))

    def finish(self) -> bytes:
        pdf = self.pdf
        pdf.setStrokeColor(LINE)
        pdf.setLineWidth(0.4)
        pdf.line(self.left, 11.5 * mm, self.right, 11.5 * mm)
        pdf.setFillColor(MUTED)
        pdf.setFont("Helvetica", 7.5)
        pdf.drawString(self.left, 7.5 * mm, fit(self.footer_label, "Helvetica", 7.5, 70 * mm))
        draw_mark(pdf, self.right - 22.5 * mm, 6.2 * mm, 4.4 * mm)
        pdf.setFont("Helvetica-Bold", 7.5)
        pdf.drawString(self.right - 17.3 * mm, 7.5 * mm, "NyumbaPMS")
        _draw_tagline(pdf, self.width / 2, 3.4 * mm)
        pdf.showPage()
        pdf.save()
        return self.buf.getvalue()
