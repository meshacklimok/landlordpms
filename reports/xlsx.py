"""Excel downloads (D-132). Every report that offers CSV also offers Excel: the same rows, one workbook sheet."""

import csv
import io
import re
from decimal import Decimal
from functools import wraps

from django.http import HttpResponse

CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
# Plain numbers become numbers; anything with a leading zero (a phone number, a code) stays text.
_NUMBER = re.compile(r"^-?(0|[1-9]\d*)(\.\d+)?$")


def response(filename: str, title: str, rows: list[list]) -> HttpResponse:
    """The rows as a workbook. Text is always stored as text, so a cell that starts with = is never a formula."""
    from openpyxl import Workbook
    from openpyxl.cell.cell import TYPE_STRING

    book = Workbook()
    sheet = book.active
    sheet.title = "".join(" " if ch in "[]*?:/" + chr(92) else ch for ch in title)[:31] or "Report"
    for row in rows:
        sheet.append([float(v) if isinstance(v, Decimal) else v for v in row])
        for cell in sheet[sheet.max_row]:
            if isinstance(cell.value, str):
                cell.data_type = TYPE_STRING
    sheet.column_dimensions["A"].width = 34
    out = HttpResponse(content_type=CONTENT_TYPE)
    out["Content-Disposition"] = f'attachment; filename="{filename}"'
    book.save(out)
    return out


def from_csv(csv_response: HttpResponse) -> HttpResponse:
    text = csv_response.content.decode("utf-8-sig")
    rows = [[float(c) if _NUMBER.match(c) else c for c in row] for row in csv.reader(io.StringIO(text))]
    name = re.search(r'filename="([^"]+)"', csv_response.get("Content-Disposition", ""))
    stem = re.sub(r"\.csv$", "", name.group(1)) if name else "report"
    return response(f"{stem}.xlsx", stem, rows)


def also_excel(view):
    """Wrap a view so `?format=xlsx` runs it as `?format=csv` (same rights, same audit) and converts the result."""

    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if request.GET.get("format") != "xlsx":
            return view(request, *args, **kwargs)
        query = request.GET.copy()
        query["format"] = "csv"
        request.GET = query
        result = view(request, *args, **kwargs)
        if result.status_code == 200 and result.get("Content-Type", "").startswith("text/csv"):
            return from_csv(result)
        return result

    for attr in ("view_class", "view_initkwargs"):
        if hasattr(view, attr):
            setattr(wrapped, attr, getattr(view, attr))
    return wrapped
