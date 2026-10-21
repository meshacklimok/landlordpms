"""The reading sheet: one row per meter in room order, with who lives there, and the lines pasted or scanned
into it. Nothing here saves a reading; the sheet posts to the same round services as before."""

import csv
import datetime
import io
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from django.db.models import Prefetch
from django.utils.translation import gettext as _

from leases.models import Lease, LeaseTenant

from . import services
from .models import Meter

MAX_LINES = 400
NUMBER = re.compile(r"^\d[\d,]*(?:\.\d+)?$")


def natural_key(text: str) -> list:
    """A2 before A10: digits compare as numbers, the rest as lower-case text."""
    return [(0, int(p), "") if p.isdigit() else (1, 0, p.casefold()) for p in re.split(r"(\d+)", text or "") if p]


def tenants_on(units, day: datetime.date) -> dict[int, str]:
    """Unit pk -> the name of the tenant living there on `day`. A unit with nobody is left out."""
    links = Prefetch("lease_tenants", queryset=LeaseTenant.objects.select_related("tenant"))
    leases = (services.covering_leases(units, day, day).filter(status=Lease.Status.ACTIVE)
              .prefetch_related(links).order_by("start_date"))
    names = {}
    for lease in leases:
        tenant = lease.primary_tenant or next((lt.tenant for lt in lease.lease_tenants.all()), None)
        if tenant is not None:
            names[lease.unit_id] = tenant.name
    return names


def sheet_meters(meters: list[Meter], day: datetime.date) -> list[dict]:
    """The meters as sheet rows, in room order. Each row says which rooms the meter serves and who is in them."""
    unit_ids = {mu.unit_id for meter in meters for mu in meter.served.all()}
    names = tenants_on(list(unit_ids), day) if unit_ids else {}
    rows = []
    for meter in meters:
        served = [{"code": mu.unit.code, "tenant": names.get(mu.unit_id), "pk": mu.unit_id, "weight": mu.weight}
                  for mu in sorted(meter.served.all(), key=lambda mu: natural_key(mu.unit.code))]
        rows.append({"meter": meter, "served": served, "shared": len(served) > 1,
                     "vacant": bool(served) and not any(s["tenant"] for s in served),
                     "sort": natural_key(served[0]["code"] if served else meter.label)})
    rows.sort(key=lambda r: (r["sort"], r["shared"], natural_key(r["meter"].label)))
    return rows


# ---------------------------------------------------------------------------
# Lines pasted or scanned: "A1, 134"
# ---------------------------------------------------------------------------


@dataclass
class Imported:
    """What a block of lines made of a property's meters: matches to fill in, and lines that were left out."""

    matches: list[dict] = field(default_factory=list)
    skipped: list[dict] = field(default_factory=list)


def to_number(text: str) -> Decimal | None:
    text = (text or "").strip()
    if not NUMBER.match(text):
        return None
    try:
        return Decimal(text.replace(",", ""))
    except InvalidOperation:
        return None


def parse_lines(text: str) -> list[tuple[str, Decimal, str]]:
    """(room, number, original line) for each line that has a room and a number; others come back with None."""
    out = []
    for raw in (text or "").splitlines()[:MAX_LINES]:
        line = raw.strip()
        if not line:
            continue
        out.append((*_split_line(line), line))
    return out


def _split_line(line: str) -> tuple[str, Decimal | None]:
    for sep in ("\t", ";", ","):
        if sep in line:
            cells = [c.strip().strip('"') for c in next(csv.reader(io.StringIO(line), delimiter=sep))]
            cells = [c for c in cells if c != ""]
            if len(cells) >= 2 and (num := to_number(cells[-1])) is not None:
                return cells[0], num
            return "", None
    for pattern in (r"^(?P<room>.+?)\s*[:=]\s*(?P<num>[\d,.]+)$", r"^(?P<room>.+?)\s+(?P<num>[\d,.]+)$"):
        m = re.match(pattern, line)
        if m and (num := to_number(m.group("num"))) is not None:
            return m.group("room").strip(), num
    return "", None


def _key(text: str) -> str:
    return re.sub(r"[\s_]+", "", text or "").casefold()


# Words caretakers write before a room ("Rm A3", "Hse 4", "No. 12"), tried only when the room as written matches none.
ROOM_WORD = re.compile(r"^\s*(?:room|rm|house|hse|hs|unit|flat|apt|door|no)\b[\s.:#-]*|^\s*#\s*", re.IGNORECASE)


def match_lines(lines: list[tuple[str, Decimal | None, str]], meters: list[Meter], *, units_used: bool,
                utility: str = "") -> Imported:
    """Matches each line's room to one meter by the code of a room it serves, or by the meter's label."""
    pool = [m for m in meters if m.kind == Meter.Kind.POSTPAID and (not utility or m.utility == utility)]
    by_key: dict[str, list[Meter]] = {}
    for meter in pool:
        keys = {_key(meter.label)} | {_key(mu.unit.code) for mu in meter.served.all()}
        for key in keys:
            by_key.setdefault(key, []).append(meter)
    result = Imported()
    taken: dict[int, int] = {}
    for room, number, line in lines:
        if number is None or not room:
            result.skipped.append({"line": line, "reason": _("No room and number found in this line.")})
            continue
        found = by_key.get(_key(room)) or by_key.get(_key(ROOM_WORD.sub("", room)), [])
        if not found:
            result.skipped.append({"line": line, "reason": _("No meter is set up for %(room)s.") % {"room": room}})
            continue
        if len(found) > 1:
            result.skipped.append({"line": line, "reason": _("%(room)s has more than one meter. Use the meter's "
                                                              "label.") % {"room": room}})
            continue
        meter = found[0]
        if meter.pk in taken:
            result.matches[taken[meter.pk]] = {**result.matches[taken[meter.pk]], "value": str(number),
                                               "line": line, "repeated": True}
            continue
        taken[meter.pk] = len(result.matches)
        result.matches.append({"meter": str(meter.public_id), "pk": meter.pk, "room": room, "value": str(number),
                               "mode": "units" if units_used else "reading", "line": line})
    return result
