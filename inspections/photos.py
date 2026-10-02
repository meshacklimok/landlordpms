"""Photo uploads for condition reports (D-047 item 4).

Every upload is decoded and saved again as a JPEG: that proves it is an image, turns it the right
way up, keeps the size sensible, and drops EXIF data such as the phone's location.
"""

import io
import secrets

from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.utils.translation import gettext as _
from PIL import Image, ImageOps, UnidentifiedImageError

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_PIXELS = 50_000_000
LONG_SIDE = 1600
JPEG_QUALITY = 82


def process(upload) -> tuple[ContentFile, int, int]:
    """Returns (file, width, height) ready for ConditionPhoto.image, or raises ValidationError."""
    if upload is None:
        raise ValidationError({"image": _("Choose a photo.")})
    if upload.size > MAX_UPLOAD_BYTES:
        raise ValidationError({"image": _("The photo is larger than 10 MB.")})
    try:
        with Image.open(upload) as img:
            if img.width * img.height > MAX_PIXELS:
                raise ValidationError({"image": _("The photo is too large.")})
            img = ImageOps.exif_transpose(img)
            if img.mode in ("RGBA", "LA", "P"):
                img = img.convert("RGBA")
                flat = Image.new("RGB", img.size, "white")
                flat.paste(img, mask=img.getchannel("A"))
                img = flat
            elif img.mode != "RGB":
                img = img.convert("RGB")
            img.thumbnail((LONG_SIDE, LONG_SIDE))
            out = io.BytesIO()
            img.save(out, "JPEG", quality=JPEG_QUALITY, optimize=True)
            width, height = img.size
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError, ValueError):
        raise ValidationError({"image": _("That file is not a photo we can read. Use JPEG or PNG.")}) from None
    return ContentFile(out.getvalue(), name=f"{secrets.token_hex(16)}.jpg"), width, height
