"""Prints the WhatsApp templates to submit to Meta for approval (D-044 item 16).

Each `{field}` becomes `{{1}}`, `{{2}}`… in order; the sample values help the reviewer.
"""

import json
import string

from django.core.management.base import BaseCommand

from notifications import catalog
from notifications.rendering import fields_in

SAMPLES = {
    "org_name": "Riverside Homes", "tenant_name": "Amina", "unit": "A1", "property": "Riverside Court",
    "invoice_number": "INV-2026-000123", "amount": "KES 15,000.00", "amount_due": "KES 15,000.00",
    "due_date": "05/10/2026", "balance": "KES 15,000.00", "pay_reference": "A1-AMINA",
    "paid_on": "03/10/2026", "receipt_number": "RCT-2026-000045",
    "receipt_link": "https://example.com/r/abc123/", "text": "Water will be off on Friday from 9am to 1pm.",
}


def meta_body(text: str) -> str:
    out, n = [], 0
    for literal, name, _spec, _conv in string.Formatter().parse(text):
        out.append(literal)
        if name is not None:
            n += 1
            out.append(f"{{{{{n}}}}}")
    return "".join(out)


def templates() -> list[dict]:
    rows = []
    for ntype in catalog.TYPES:
        for (channel, language), text in sorted(ntype.bodies.items()):
            if channel != catalog.WHATSAPP:
                continue
            fields = fields_in(text)
            rows.append({"name": catalog.whatsapp_template(ntype), "language": language, "category": "UTILITY",
                         "body": meta_body(text), "fields": fields,
                         "examples": [SAMPLES.get(f, f) for f in fields]})
    return rows


class Command(BaseCommand):
    help = "Print the WhatsApp message templates to submit to Meta for approval."

    def add_arguments(self, parser):
        parser.add_argument("--json", action="store_true", help="Print JSON instead of text.")

    def handle(self, *args, **options):
        rows = templates()
        if options["json"]:
            self.stdout.write(json.dumps(rows, indent=2, ensure_ascii=False))
            return
        for row in rows:
            self.stdout.write(f"== {row['name']} ({row['language']}, {row['category']})")
            self.stdout.write(row["body"])
            for i, (field, example) in enumerate(zip(row["fields"], row["examples"], strict=True), 1):
                self.stdout.write(f"  {{{{{i}}}}} {field}: {example}")
            self.stdout.write("")
