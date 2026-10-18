"""One place for CSV downloads: UTF-8 with a byte-order mark (so Excel reads Swahili names and "·" correctly) and
cells a spreadsheet will not run as formulas.

Text that starts with = + - @ (or a tab or return) is prefixed with an apostrophe. Real numbers, and text that is only a
number such as "-1250.50", are left alone so a negative amount stays an amount.
"""

import csv
import re
from decimal import Decimal

from django.http import HttpResponse

BOM = "﻿"
CSV_TYPE = "text/csv; charset=utf-8"
_NUMBER = re.compile(r"^-?\d+(\.\d+)?$")


def safe(value):
    """The value as a cell: numbers unchanged, None as empty, text made safe against formula injection."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, int | float | Decimal):
        return value
    text = str(value)
    if text[:1] in ("=", "+", "-", "@", "\t", "\r") and not _NUMBER.match(text):
        return "'" + text
    return text


class Writer:
    """csv.writer that makes every cell safe."""

    def __init__(self, target):
        self._writer = csv.writer(target)

    def writerow(self, row):
        return self._writer.writerow([safe(v) for v in row])

    def writerows(self, rows):
        for row in rows:
            self.writerow(row)


def csv_response(filename: str) -> HttpResponse:
    """An empty CSV download with the byte-order mark already written."""
    response = HttpResponse(content_type=CSV_TYPE)
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    response.write(BOM)
    return response


def writer(response) -> Writer:
    return Writer(response)
