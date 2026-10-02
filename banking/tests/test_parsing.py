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
