"""Reading statement CSVs (D-064 items 3 and 7). Pure functions over the file's bytes; no database.

`read_bank` reads a bank statement and `read_mpesa` an M-Pesa organization statement. Both find the
header row among the first rows by known column names, keep money in, count money out, and report
lines that could not be read with their line number.
"""

import csv
import datetime
import io
import re
import zoneinfo
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

MAX_BYTES = 1_000_000
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
    "date": ("transaction date", "trans date", "tran date", "txn date", "posting date", "post date",
             "booking date", "date", "value date", "val date"),
    "description": ("description", "narrative", "narration", "particulars", "details", "transaction details",
                    "transaction description", "remarks", "memo"),
    "reference": ("reference", "ref", "ref no", "reference no", "reference number", "transaction reference",
                  "transaction ref", "bank reference", "customer reference", "cheque no", "chq no",
                  "cheque number", "receipt no", "document no"),
    "credit": ("credit", "credits", "credit amount", "money in", "paid in", "deposit", "deposits", "cr"),
    "debit": ("debit", "debits", "debit amount", "money out", "withdrawn", "withdrawal", "withdrawals", "dr"),
    "amount": ("amount", "transaction amount"),
    "balance": ("balance", "running balance", "book balance", "ledger balance", "closing balance",
                "available balance"),
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


def normalize_header(value: str) -> str:
    words = re.sub(r"[^a-z0-9]+", " ", (value or "").lower()).split()
    if words and words[-1] in ("kes", "ksh", "kshs"):
        words = words[:-1]
    return " ".join(words)


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


def _decode(data: bytes) -> str:
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1")


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


def _table(data: bytes, names, is_header) -> tuple[list[list[str]], int, dict[str, int]]:
    """(rows, index of the header row, {field: column}) for the first delimiter that finds a header."""
    if len(data) > MAX_BYTES:
        raise StatementError(f"The file is larger than {MAX_BYTES // 1_000_000} MB.")
    text = _decode(data)
    for delimiter in DELIMITERS:
        rows = list(csv.reader(io.StringIO(text), delimiter=delimiter))
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
             "column and a Reference column help matching.")


def read_bank(data: bytes) -> Parsed:
    try:
        rows, start, columns = _table(data, BANK_COLUMNS,
                                      lambda c: "date" in c and ("credit" in c or "amount" in c))
    except StatementError as e:
        raise StatementError(str(e) or BANK_HELP) from None
    when = _When()
    result = Parsed()
    for number, row in enumerate(rows[start + 1:], start=start + 2):
        if not any(cell.strip() for cell in row):
            continue
        raw_date = _cell(row, columns, "date")
        try:
            if "credit" in columns:
                amount = parse_amount(_cell(row, columns, "credit"))
                debit = parse_amount(_cell(row, columns, "debit"))
            else:
                amount, debit = parse_amount(_cell(row, columns, "amount")), None
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
        if amount is None or amount <= 0:
            result.skipped += 1
            continue
        try:
            balance = parse_amount(_cell(row, columns, "balance"))
        except ValueError:
            balance = None
        result.lines.append(BankLine(
            line=number, posted_on=posted, description=_cell(row, columns, "description")[:300],
            reference=_cell(row, columns, "reference")[:60], amount=amount, balance=balance))
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
                                      lambda c: {"receipt", "completed", "paid_in"} <= c.keys())
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
