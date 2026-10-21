"""Reading bank and M-Pesa statement CSVs (D-064 items 3 and 7)."""

import datetime
from decimal import Decimal

import pytest

from banking import parsing
from banking.parsing import StatementError, parse_amount, read_bank, read_mpesa

D = datetime.date


@pytest.mark.parametrize(("raw", "value"), [
    ("1,234.50", Decimal("1234.50")), ("KES 1,234.50", Decimal("1234.50")), ("1234.5", Decimal("1234.50")),
    ("(1,234.50)", Decimal("-1234.50")), ("-500", Decimal("-500.00")), ("500 DR", Decimal("-500.00")),
    ("500.00CR", Decimal("500.00")), ("Ksh 2 000", Decimal("2000.00")), ("", None), ("-", None),
])
def test_amounts(raw, value):
    assert parse_amount(raw) == value


def test_an_unreadable_amount_raises():
    with pytest.raises(ValueError):
        parse_amount("twelve")


def test_a_bank_statement_with_credit_and_debit_columns():
    data = (
        b"Equity Bank Statement\n"
        b"Account,0123456789\n"
        b"\n"
        b"Transaction Date,Value Date,Narrative,Reference,Debit,Credit,Running Balance\n"
        b"01/02/2026,01/02/2026,Opening balance,,,,10000.00\n"
        b"03/02/2026,03/02/2026,CASH DEP GV-A1 JANE DOE,FT260340001,,\"15,000.00\",\"25,000.00\"\n"
        b"04/02/2026,04/02/2026,Bank charges,,50.00,,24950.00\n"
        b"05-Feb-2026,05/02/2026,RTGS JOHN KAMAU,FT260360002,,7500,32450.00\n"
        b"31/02/2026,,Bad date,,,100,\n"
    )
    parsed = read_bank(data)
    assert [(line.posted_on, line.amount, line.reference) for line in parsed.lines] == [
        (D(2026, 2, 3), Decimal("15000.00"), "FT260340001"), (D(2026, 2, 5), Decimal("7500.00"), "FT260360002")]
    assert parsed.lines[0].description == "CASH DEP GV-A1 JANE DOE"
    assert parsed.lines[0].balance == Decimal("25000.00")
    assert parsed.skipped == 2  # the opening balance and the bank charge
    assert parsed.errors == [(9, "The date “31/02/2026” could not be read.")]


def test_a_single_signed_amount_column_semicolons_and_a_bom():
    data = ("﻿Date;Description;Amount;Balance\n"
            "2026-02-03 10:15:00;MPESA C2B P1-A1;15000;\n"
            "2026-02-04;Standing order;-2000;\n").encode()
    parsed = read_bank(data)
    assert [(line.posted_on, line.amount) for line in parsed.lines] == [(D(2026, 2, 3), Decimal("15000.00"))]
    assert parsed.skipped == 1


def test_windows_1252_is_read():
    data = "Date,Details,Credit\n03/02/2026,Paiement caf\xe9,100\n".encode("cp1252")
    assert read_bank(data).lines[0].description == "Paiement café"


def test_a_file_without_the_columns_says_what_it_needs():
    with pytest.raises(StatementError, match="date column"):
        read_bank(b"Name,Phone\nJane,0712345678\n")


def test_too_many_lines_is_refused(monkeypatch):
    monkeypatch.setattr(parsing, "MAX_LINES", 2)
    with pytest.raises(StatementError, match="more than 2 lines"):
        read_bank(b"Date,Credit\n01/02/2026,1\n02/02/2026,1\n03/02/2026,1\n")


def test_an_mpesa_organization_statement():
    data = (
        b"Organization Name,ACME HOMES\n"
        b"Receipt No.,Completion Time,Initiation Time,Details,Transaction Status,Paid In,Withdrawn,Balance,"
        b"Balance Confirmed,Reason Type,Other Party Info,Linked Transaction ID,A/C No.\n"
        b"qab12cd301,03-02-2026 10:15:00,03-02-2026 10:15:00,Pay Bill from 254712345678 - JANE DOE Acc. GV-A1,"
        b"Completed,15000.00,,15000.00,true,Pay Bill Online,254712345678 - JANE DOE,,GV-A1\n"
        b"QAB12CD302,03-02-2026 11:00:00,,Pay Bill from 2547****5678 - JOHN DOE Acc. GV A2,Completed,500.00,,,"
        b"true,Pay Bill Online,2547****5678 - JOHN DOE,,\n"
        b"QAB12CD303,03-02-2026 12:00:00,,Business Charge,Completed,,30.00,,,,,,\n"
        b"QAB12CD304,03-02-2026 13:00:00,,Pay Bill,Failed,700.00,,,,,,,\n"
    )
    parsed = read_mpesa(data)
    first, second = parsed.lines
    assert (first.receipt, first.amount, first.bill_ref, first.payer_raw, first.payer_name) == (
        "QAB12CD301", Decimal("15000.00"), "GV-A1", "254712345678", "JANE DOE")
    assert first.paid_at == datetime.datetime(2026, 2, 3, 10, 15, tzinfo=parsing.NAIROBI)
    assert (second.bill_ref, second.payer_raw) == ("GV A2", "2547****5678")
    assert parsed.skipped == 2


# ---------------------------------------------------------------------------
# Excel, PDF and other layouts (D-211)
# ---------------------------------------------------------------------------


def _xlsx(rows) -> bytes:
    import io

    from openpyxl import Workbook

    book = Workbook()
    sheet = book.active
    for row in rows:
        sheet.append(row)
    out = io.BytesIO()
    book.save(out)
    return out.getvalue()


def test_an_excel_statement_with_real_dates_and_numbers():
    data = _xlsx([
        ["Co-operative Bank", None, None],
        ["Date", "Transaction Details", "Money In", "Money Out", "Balance"],
        [datetime.datetime(2026, 10, 6), "UJ6PF8WITP-254729024549-MPESAC2B_400 200-MESHACK", 3200.0, None, 3489.98],
        [datetime.datetime(2026, 10, 6), "CLOUDFLARE 795564", None, -1668.3, 289.98],
    ])
    parsed = read_bank(data)
    assert [(line.posted_on, line.amount, line.balance) for line in parsed.lines] == [
        (D(2026, 10, 6), Decimal("3200.00"), Decimal("3489.98"))]
    assert "MESHACK" in parsed.lines[0].description
    assert parsed.skipped == 1


def test_an_old_xls_and_a_web_page_are_refused_with_advice():
    with pytest.raises(StatementError, match="xlsx"):
        read_bank(b"\xd0\xcf\x11\xe0" + b"0" * 50)
    with pytest.raises(StatementError, match="web page"):
        read_bank(b"<!DOCTYPE html><html><table></table></html>")


def test_coop_app_columns_with_signed_amounts_in_csv():
    data = (b"Date,Value Date,Transaction Details,Ref Number,Money In,Money Out,Balance\n"
            b"06-Oct-26,06-Oct-26,UJ6PF8WITP-254729024549-MPESAC2B_400 200-MESHACK,UJ6PF8WITP,"
            b"\"+ 3,200.00\",,\"3,489.98\"\n"
            b"14-Oct-26,14-Oct-26,EXCISE MPESA BANK COMMISSION,,,\"- 3.60\",989.08\n")
    parsed = read_bank(data)
    assert [(line.amount, line.reference) for line in parsed.lines] == [(Decimal("3200.00"), "UJ6PF8WITP")]
    assert parsed.skipped == 1


def test_a_single_amount_column_with_a_dr_cr_column():
    data = (b"Date,Narration,Amount,Dr/Cr\n03/02/2026,RENT GV A1,15000,CR\n04/02/2026,FEE,50,DR\n")
    parsed = read_bank(data)
    assert [line.amount for line in parsed.lines] == [Decimal("15000.00")]
    assert parsed.skipped == 1


def test_an_unsigned_amount_column_is_split_by_the_running_balance_either_way_round():
    newest_first = (b"Date,Description,Amount,Balance\n"
                    b"05/02/2026,RENT B2,2000,12000\n04/02/2026,FEE,50,10000\n03/02/2026,Opening,10050,10050\n")
    assert [line.amount for line in read_bank(newest_first).lines] == [Decimal("2000.00"), Decimal("10050.00")]
    oldest_first = (b"Date,Description,Amount,Balance\n"
                    b"03/02/2026,Opening,10050,10050\n04/02/2026,FEE,50,10000\n05/02/2026,RENT B2,2000,12000\n")
    assert [line.amount for line in read_bank(oldest_first).lines] == [Decimal("10050.00"), Decimal("2000.00")]


def test_unknown_description_header_falls_back_to_the_other_cells():
    data = b"Date,Credit,Particulars of payment\n03/02/2026,100,RENT GV A1 JANE\n"
    assert read_bank(data).lines[0].description == "RENT GV A1 JANE"


COOP_ROWS = [
    # date, value date, description lines, ref, money in, money out, balance
    ("14-Oct-26", ["EXCISE MPESA BANK COMMISSION"], "", "", "- 3.60", "989.08"),
    ("06-Oct-26", ["UJ6PF8WITP-254729024549-01102052606001-MPESAC2B_400", "200-MESHACK KIPLIMO"], "UJ6PF8WITP",
     "+ 3,200.00", "", "3,489.98"),
    ("06-Oct-26", ["CLOUDFLARE 795564 30137777893 01102052606001", "APQUP1VD 4195144516"], "627906812228", "",
     "- 1,668.30", "289.98"),
    ("08-Sep-26", ["UI8PF5KQDV-254729024549-01102052606001-MPESAC2B_40", "0200-MESHACK KIPLIMO"], "UI8PF5KQDV",
     "+ 5,000.00", "", "8,231.37"),
]


def _coop_pdf() -> bytes:
    import io

    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.platypus import SimpleDocTemplate, Spacer, Table, TableStyle

    header = ["Date", "Value Date", "Transaction Details", "Ref Number", "Money In", "Money Out", "Balance"]
    body = [[d, d, "\n".join(desc), ref, money_in, money_out, bal]
            for d, desc, ref, money_in, money_out, bal in COOP_ROWS]
    widths = [22 * mm, 22 * mm, 62 * mm, 28 * mm, 22 * mm, 22 * mm, 22 * mm]
    table = Table([header, *body], colWidths=widths, rowHeights=[10 * mm] + [12 * mm] * len(body))
    table.setStyle(TableStyle([("FONTSIZE", (0, 0), (-1, -1), 7), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                               ("ALIGN", (4, 0), (-1, -1), "CENTER")]))
    out = io.BytesIO()
    SimpleDocTemplate(out, pagesize=A4, leftMargin=8 * mm, rightMargin=8 * mm).build(
        [Spacer(1, 5 * mm), table, Spacer(1, 15 * mm)])
    return out.getvalue()


def test_a_coop_app_pdf_with_wrapped_descriptions_is_rebuilt_into_rows():
    parsed = read_bank(_coop_pdf())
    assert [(line.posted_on, line.amount, line.balance) for line in parsed.lines] == [
        (D(2026, 10, 6), Decimal("3200.00"), Decimal("3489.98")),
        (D(2026, 9, 8), Decimal("5000.00"), Decimal("8231.37"))]
    first = parsed.lines[0]
    assert "MESHACK KIPLIMO" in first.description and "UJ6PF8WITP-254729024549" in first.description
    assert first.reference == "UJ6PF8WITP"
    assert parsed.skipped == 2 and parsed.errors == []


def test_a_pdf_with_no_text_says_what_to_do():
    import io

    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(200, 200)
    out = io.BytesIO()
    writer.write(out)
    with pytest.raises(StatementError, match="no text"):
        read_bank(out.getvalue())
    with pytest.raises(StatementError, match="could not be read"):
        read_bank(b"%PDF-1.4 garbage")
