"""Phone number normalisation to E.164 (doc 11 §6, doc 14 A11).

Kenyan numbers are the default: 0712 345678, 712345678, 254712345678 and
+254 712-345-678 all become +254712345678. Other countries must be typed with
a leading +.
"""

import re

KENYA_CODE = "254"
_KENYA_MOBILE = re.compile(r"^[17]\d{8}$")
_E164 = re.compile(r"^\+[1-9]\d{7,14}$")


class InvalidPhoneNumber(ValueError):
    pass


def normalize_phone(raw: str) -> str:
    if raw is None:
        raise InvalidPhoneNumber("Phone number is required.")
    value = re.sub(r"[\s\-().]", "", str(raw))
    if value.startswith("00"):
        value = "+" + value[2:]

    if value.startswith("+"):
        if value.startswith("+" + KENYA_CODE):
            return _kenyan(value[1 + len(KENYA_CODE):])
        if _E164.match(value):
            return value
        raise InvalidPhoneNumber("Enter a valid phone number.")

    if not value.isdigit():
        raise InvalidPhoneNumber("Enter a valid phone number.")
    if value.startswith(KENYA_CODE) and len(value) == 12:
        return _kenyan(value[len(KENYA_CODE):])
    if value.startswith("0") and len(value) == 10:
        return _kenyan(value[1:])
    return _kenyan(value)


def _kenyan(national: str) -> str:
    if not _KENYA_MOBILE.match(national):
        raise InvalidPhoneNumber("Enter a valid Kenyan mobile number, e.g. 0712 345678.")
    return f"+{KENYA_CODE}{national}"


def mask_phone(phone: str) -> str:
    """+254712345678 -> +254 7** *** 678, for screens and messages."""
    if not phone or len(phone) < 7:
        return phone or ""
    return f"{phone[:4]} {phone[4]}** *** {phone[-3:]}"
