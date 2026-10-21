"""A QR code on each meter (D-157): the phone's camera opens that meter's reading form.

The code holds only the page's address; the page needs a signed-in member who may record readings on the
property, so a sticker photographed by a stranger opens nothing. The in-app camera is off (Permissions-Policy), so
the phone's own camera app reads the code.
"""

import io

import segno
from django.conf import settings
from django.urls import reverse


def reading_url(meter) -> str:
    return settings.SITE_URL + reverse("meters:quick", args=[meter.public_id])


def svg(meter, *, scale: int = 4) -> str:
    """An inline <svg> of the code, dark on light so it prints and scans in either colour mode."""
    return segno.make(reading_url(meter), error="m").svg_inline(scale=scale, border=2, dark="#000", light="#fff")


def svg_file(meter, *, scale: int = 10) -> bytes:
    """The code as a standalone SVG file, to print large or send to a sticker printer."""
    out = io.BytesIO()
    segno.make(reading_url(meter), error="m").save(out, kind="svg", scale=scale, border=4, dark="#000", light="#fff",
                                                   title=str(meter.label))
    return out.getvalue()
