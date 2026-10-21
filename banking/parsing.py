"""Reading statement files (D-064 items 3 and 7, widened in D-211). Pure functions over the file's bytes; no database.

`read_bank` reads a bank statement and `read_mpesa` an M-Pesa organization statement, from CSV, Excel or
PDF (see `readers`). Both find the header row among the first rows by known column names, keep money in,
count money out, and report lines that could not be read with their line number.
"""

import datetime
import re
import zoneinfo
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from . import readers

MAX_BYTES = 5_000_000
MAX_LINES = 5000
HEADER_SCAN = 30
DELIMITERS = (",", ";", "\t")
NAIROBI = zoneinfo.ZoneInfo("Africa/Nairobi")
_CENT = Decimal("0.01")

DATE_FORMATS = ("%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%Y-%m-%d", "%Y/%m/%d", "%d/%m/%y", "%d-%m-%y",
                "%d %b %Y", "%d-%b-%Y", "%d-%b-%y", "%d %b %y", "%d %B %Y", "%d-%B-%Y", "%d/%b/%Y")
TIME_FORMATS = ("", " %H:%M:%S", " %H:%M", "T%H:%M:%S", " %I:%M:%S %p", " %I:%M %p")

# Column names, already normalized (lower case, punctuation as spaces), in order of preference.
BANK_COLUMNS = {
    "date": ("transaction date", "trans date", "tran date", "txn date", "tran dt", "posting date", "post date",
             "posted date", "booking date", "processing date", "entry date", "date", "value date", "val date",
             "value dt"),
    "description": ("description", "narrative", "narration", "particulars", "details", "transaction details",
                    "transaction description", "trans details", "tran particulars", "transaction particulars",
                    "transaction narration", "txn description", "transaction remarks", "remarks", "memo",
                    "payment details", "tran details"),
    "reference": ("reference", "ref", "ref no", "ref number", "ref num", "reference no", "reference number",
                  "transaction reference", "transaction ref", "bank reference", "customer reference",
                  "cheque no", "chq no", "cheque number", "receipt no", "document no", "transaction id",
                  "txn ref", "trans ref", "instrument no"),
    "credit": ("credit", "credits", "credit amount", "credit amt", "cr amount", "cr amt", "money in", "paid in",
               "amount in", "deposit", "deposits", "deposit amount", "lodgement", "lodgements", "receipts",
               "cr"),
    "debit": ("debit", "debits", "debit amount", "debit amt", "dr amount", "dr amt", "money out", "paid out",
              "amount out", "withdrawn", "withdrawal", "withdrawals", "withdrawal amount", "payments", "dr"),
    "amount": ("amount", "transaction amount", "txn amount", "amt"),
    "type": ("dr cr", "cr dr", "type", "transaction type", "txn type", "debit credit", "credit debit",
             "indicator", "dr cr indicator"),
    "balance": ("balance", "running balance", "book balance", "ledger balance", "closing balance",
                "available balance", "balance after"),
}
MPESA_COLUMNS = {
    "receipt": ("receipt no", "receipt number", "receipt", "transaction id", "m pesa code"),
    "completed": ("completion time", "completion date", "transaction date", "date", "initiation time"),
    "details": ("details", "description"),
    "status": ("transaction status", "status"),
    "paid_in": ("paid in", "money in", "credit"),
    "withdrawn": ("withdrawn", "money out", "debit"),
    "balance": ("balance",),
    "other_party": ("other party info", "other party", "party info"),
    "account": ("a c no", "ac no", "account no", "account number", "acc no", "bill reference"),
}


class StatementError(ValueError):
    """The file as a whole cannot be read."""


class NotRecognised(StatementError):
    """The file opened but its columns are not ones we know; the landlord can match them by hand (D-212)."""


@dataclass
class BankLine:
    line: int
    posted_on: datetime.date
    description: str
    reference: str
    amount: Decimal
    balance: Decimal | None


@dataclass
class MpesaLine:
    line: int
    receipt: str
    paid_at: datetime.datetime
    amount: Decimal
    bill_ref: str
    details: str
    payer_raw: str
    payer_name: str


@dataclass
class Parsed:
    lines: list = field(default_factory=list)
    # Money out, or (M-Pesa) not completed.
    skipped: int = 0
    errors: list[tuple[int, str]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Values
# ---------------------------------------------------------------------------


normalize_header = readers.normalise


_CURRENCY = re.compile(r"\b(KES|KSHS|KSH|SH)\b\.?|/=", re.IGNORECASE)


def parse_amount(raw: str) -> Decimal | None:
    """A signed amount, or None when the cell is empty. Raises ValueError when it cannot be read.

    Money out is negative: `(100)`, `-100` and `100 DR`. `100 CR` is money in.
    """
    s = " ".join((raw or "").split())
    if s in ("", "-", "--"):
        return None
    sign = 1
    upper = s.upper()
    if upper.endswith("DR"):
        s, sign = s[:-2], -1
    elif upper.endswith("CR"):
        s = s[:-2]
    s = _CURRENCY.sub("", s).replace(",", "").replace(" ", "").replace(" ", "")
    if s.startswith("(") and s.endswith(")"):
        s, sign = s[1:-1], -sign
    if s.startswith("-"):
        s, sign = s[1:], -sign
    elif s.startswith("+"):
        s = s[1:]
    try:
        value = Decimal(s)
    except InvalidOperation:
        raise ValueError(raw) from None
    if not value.is_finite():
        raise ValueError(raw)
    return (value * sign).quantize(_CENT, rounding=ROUND_HALF_UP)


class _When:
    """Reads dates, day first, trying the format that worked last time first."""

    def __init__(self):
        self.formats = [d + t for d in DATE_FORMATS for t in TIME_FORMATS]

    def __call__(self, raw: str) -> datetime.datetime:
        s = " ".join((raw or "").split())
        for i, fmt in enumerate(self.formats):
            try:
                value = datetime.datetime.strptime(s, fmt)
            except ValueError:
                continue
            if i:
                self.formats.insert(0, self.formats.pop(i))
            return value
        raise ValueError(raw)


# ---------------------------------------------------------------------------
# The header row
# ---------------------------------------------------------------------------


def _columns(header: list[str], names: dict[str, tuple[str, ...]]) -> dict[str, int]:
    found, used = {}, set()
    cells = [normalize_header(c) for c in header]
    for key, options in names.items():
        for option in options:
            index = next((i for i, c in enumerate(cells) if c == option and i not in used), None)
            if index is not None:
                found[key] = index
                used.add(index)
                break
    return found


def _table(data: bytes, names, is_header, anchors) -> tuple[list[list[str]], int, dict[str, int]]:
    """(rows, index of the header row, {field: column}) for the first table in the file that has a header."""
    if len(data) > MAX_BYTES:
        raise StatementError(f"The file is larger than {MAX_BYTES // 1_000_000} MB.")
    try:
        candidates = readers.tables(data, names, anchors)
    except readers.FileError as e:
        raise StatementError(str(e)) from None
    for rows in candidates:
        for i, row in enumerate(rows[:HEADER_SCAN]):
            columns = _columns(row, names)
            if is_header(columns):
                if len(rows) - i - 1 > MAX_LINES:
                    raise StatementError(f"The file has more than {MAX_LINES} lines; split it by month.")
                return rows, i, columns
    raise StatementError("")


def _cell(row: list[str], columns: dict[str, int], key: str) -> str:
    index = columns.get(key)
    return row[index].strip() if index is not None and index < len(row) else ""


# ---------------------------------------------------------------------------
# Bank statements
# ---------------------------------------------------------------------------

BANK_HELP = ("The statement needs a header row with a date column (Date, Transaction date or Value date) and a "
             "money-in column (Credit, Money in or Paid in) or an Amount column. A Description or Narrative "
             "column and a Reference column help matching. Excel, CSV and PDF statements are read.")


def _description(row: list[str], columns: dict[str, int]) -> str:
    """The description column, or, when the bank's name for it is not known, the text of the unmapped cells."""
    text = _cell(row, columns, "description")
    if text or "description" in columns:
        return text
    mapped = set(columns.values())
    return " ".join(c.strip() for i, c in enumerate(row) if i not in mapped and c.strip())


def _signed(row, columns, amount_key: str) -> Decimal | None:
    """The single amount column as a signed amount, using a Dr/Cr column when there is one."""
    amount = parse_amount(_cell(row, columns, amount_key))
    kind = _cell(row, columns, "type").lower()
    if amount is not None and kind:
        if kind.startswith(("d", "w")):  # DR, Debit, Withdrawal
            return -abs(amount)
        if kind.startswith(("c", "r")):  # CR, Credit, Receipt
            return abs(amount)
    return amount


def _debits_by_balance(rows: list[dict]) -> None:
    """A single unsigned Amount column gives no direction. Each line is compared with the line before it in
    time: where the running balance moved by exactly that amount downwards, it took money out. Statements run
    oldest first or newest first, so the direction that fits more lines is used."""

    def moves(step: int) -> list[Decimal | None]:
        out = []
        for i, row in enumerate(rows):
            earlier = rows[i + step] if 0 <= i + step < len(rows) else None
            usable = earlier is not None and row["balance"] is not None and earlier["balance"] is not None
            out.append(row["balance"] - earlier["balance"] if usable else None)
        return out

    fits = {step: moves(step) for step in (-1, 1)}
    step = max(fits, key=lambda s: sum(1 for m, r in zip(fits[s], rows, strict=True)
                                       if m is not None and r["amount"] is not None and abs(m) == r["amount"]))
    for row, moved in zip(rows, fits[step], strict=True):
        if not row["signed"] and row["amount"] is not None and moved == -row["amount"]:
            row["amount"] = -row["amount"]


MAPPABLE = ("date", "description", "reference", "credit", "debit", "amount", "balance")


def with_mapping(names: dict[str, tuple[str, ...]], mapping: dict[str, str] | None) -> dict[str, tuple[str, ...]]:
    """The known column names, with the names a landlord saved for their bank tried first (D-212)."""
    if not mapping:
        return names
    saved = {key: normalize_header(value) for key, value in mapping.items() if key in names and value}
    return {key: ((saved[key],) if key in saved else ()) + options for key, options in names.items()}


def describe(data: bytes) -> dict:
    """Where the header of an unrecognised statement probably is, for the "match your columns" page:
    {"headers": [...], "samples": [[...], ...]}. The header is the first row of at least three filled cells,
    none of them a date or amount, that is followed by a row starting with a date."""
    try:
        candidates = readers.tables(data, BANK_COLUMNS, ("date",))
    except readers.FileError as e:
        raise StatementError(str(e)) from None
    for rows in candidates:
        rows = [r for r in rows[:HEADER_SCAN + 10]]
        for i, row in enumerate(rows[:HEADER_SCAN]):
            filled = [c.strip() for c in row if c.strip()]
            if len(filled) < 3 or any(_looks_like_value(c) for c in filled):
                continue
            below = [r for r in rows[i + 1:i + 4] if any(c.strip() for c in r)]
            if below and any(readers.starts_with_date(c) for c in below[0]):
                width = len(row)
                return {"headers": [c.strip() for c in row],
                        "samples": [[c.strip() for c in r[:width]] for r in below[:3]]}
    return {"headers": [], "samples": []}


def _looks_like_value(cell: str) -> bool:
    if readers.starts_with_date(cell):
        return True
    try:
        return parse_amount(cell) is not None
    except ValueError:
        return False


def read_bank(data: bytes, mapping: dict[str, str] | None = None) -> Parsed:
    names = with_mapping(BANK_COLUMNS, mapping)
    try:
        rows, start, columns = _table(data, names,
                                      lambda c: "date" in c and ("credit" in c or "amount" in c), ("date",))
    except StatementError as e:
        raise (NotRecognised if not str(e) else StatementError)(str(e) or BANK_HELP) from None
    when = _When()
    result = Parsed()
    split = "credit" in columns
    entries = []
    for number, row in enumerate(rows[start + 1:], start=start + 2):
        if not any(cell.strip() for cell in row):
            continue
        raw_date = _cell(row, columns, "date")
        try:
            if split:
                amount = parse_amount(_cell(row, columns, "credit"))
                debit = parse_amount(_cell(row, columns, "debit"))
            else:
                amount, debit = _signed(row, columns, "amount"), None
        except ValueError as e:
            result.errors.append((number, f"The amount “{e}” could not be read."))
            continue
        if not raw_date:
            if amount or debit:
                result.errors.append((number, "There is no date."))
            continue  # an opening or closing balance line, or a total
        try:
            posted = when(raw_date).date()
        except ValueError:
            result.errors.append((number, f"The date “{raw_date}” could not be read."))
            continue
        try:
            balance = parse_amount(_cell(row, columns, "balance"))
        except ValueError:
            balance = None
        explicit = split or "type" in columns or (amount is not None and amount < 0)
        entries.append({"line": number, "posted": posted, "row": row, "amount": amount, "balance": balance,
                        "signed": explicit})
    if not split:
        _debits_by_balance(entries)
    for e in entries:
        if e["amount"] is None or e["amount"] <= 0:
            result.skipped += 1
            continue
        result.lines.append(BankLine(
            line=e["line"], posted_on=e["posted"], description=_description(e["row"], columns)[:300],
            reference=_cell(e["row"], columns, "reference")[:60], amount=e["amount"], balance=e["balance"]))
    return result


# ---------------------------------------------------------------------------
# M-Pesa organization statements
# ---------------------------------------------------------------------------

MPESA_HELP = ("The statement needs the columns of an M-Pesa organization statement: Receipt No., Completion "
              "Time and Paid In, and ideally Details, Transaction Status, Other Party Info and A/C No.")
_ACC = re.compile(r"\bAcc(?:ount)?\.?\s*(?:No\.?)?\s*[:#]?\s*(.+)$", re.IGNORECASE)
_RECEIPT = re.compile(r"^[A-Z0-9]{6,20}$")


def _other_party(value: str) -> tuple[str, str]:
    """(number as given, name) from Other Party Info, e.g. "254712345678 - JANE DOE"."""
    value = " ".join((value or "").split())
    number, sep, name = value.partition(" - ")
    if not sep:
        return (value, "") if re.fullmatch(r"[\d*+ ]+", value) else ("", value)
    return number.strip(), name.strip()


def read_mpesa(data: bytes) -> Parsed:
    try:
        rows, start, columns = _table(data, MPESA_COLUMNS,
                                      lambda c: {"receipt", "completed", "paid_in"} <= c.keys(),
                                      ("completed",))
    except StatementError as e:
        raise StatementError(str(e) or MPESA_HELP) from None
    when = _When()
    result = Parsed()
    for number, row in enumerate(rows[start + 1:], start=start + 2):
        if not any(cell.strip() for cell in row):
            continue
        receipt = _cell(row, columns, "receipt").upper()
        if not receipt:
            continue
        status = _cell(row, columns, "status").lower()
        if status and status != "completed":
            result.skipped += 1
            continue
        try:
            amount = parse_amount(_cell(row, columns, "paid_in"))
        except ValueError as e:
            result.errors.append((number, f"The amount “{e}” could not be read."))
            continue
        if amount is None or amount <= 0:
            result.skipped += 1
            continue
        if not _RECEIPT.match(receipt):
            result.errors.append((number, f"“{receipt}” is not an M-Pesa receipt number."))
            continue
        raw_time = _cell(row, columns, "completed")
        try:
            paid_at = when(raw_time).replace(tzinfo=NAIROBI)
        except ValueError:
            result.errors.append((number, f"The time “{raw_time}” could not be read."))
            continue
        details = _cell(row, columns, "details")
        bill_ref = _cell(row, columns, "account")
        if not bill_ref:
            found = _ACC.search(details)
            bill_ref = found.group(1).strip() if found else ""
        payer_raw, payer_name = _other_party(_cell(row, columns, "other_party"))
        result.lines.append(MpesaLine(
            line=number, receipt=receipt, paid_at=paid_at, amount=amount, bill_ref=bill_ref[:60],
            details=details[:300], payer_raw=payer_raw[:80], payer_name=payer_name[:150]))
    return result


# ---------------------------------------------------------------------------
# Checking lines read from a photo against the running balance (D-212)
# ---------------------------------------------------------------------------


def unreconciled(entries: list[tuple[Decimal, Decimal | None]]) -> set[int]:
    """Indexes of lines whose running balance disagrees with the line before it in time. `entries` are (signed
    amount, balance) in the order printed, newest first or oldest first (the direction that fits more lines is
    used). A line with no balance, or with no neighbour that has one, cannot be checked and is not reported."""

    def moves(step: int) -> list[Decimal | None]:
        out = []
        for i, (_amount, balance) in enumerate(entries):
            j = i + step
            other = entries[j][1] if 0 <= j < len(entries) else None
            out.append(balance - other if balance is not None and other is not None else None)
        return out

    fits = {step: moves(step) for step in (-1, 1)}
    step = max(fits, key=lambda s: sum(1 for m, (a, _b) in zip(fits[s], entries, strict=True) if m == a))
    return {i for i, (m, (a, _b)) in enumerate(zip(fits[step], entries, strict=True)) if m is not None and m != a}
