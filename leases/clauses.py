"""The standard conditions printed on every tenancy agreement (D-187).

The text is a plain-language Kenyan residential template for the owner to have an advocate review. An organization
may replace it (Organization.agreement_clauses). Format: one clause per block, blocks separated by a blank line, the
first line of a block is its heading and the rest is its body. `{rent}`, `{due_day}`, `{grace_days}`, `{deposit}`,
`{notice_days}`, `{landlord}`, `{property}` and `{unit}` are filled from the lease, or left as blank lines on the
blank template.
"""

import re

from django.utils.translation import gettext as _

PLACEHOLDERS = ("landlord", "property", "unit", "rent", "due_day", "grace_days", "deposit", "notice_days")
MAX_LENGTH = 12000

_TOKEN = re.compile(r"\{(\w+)\}")


def default_text() -> str:
    return "\n\n".join(f"{heading}\n{body}" for heading, body in [
        (_("Use of the premises"),
         _("The tenant will use the premises only as a private residence and will not use them for any unlawful "
           "purpose or in a way that is a nuisance to neighbours.")),
        (_("Rent"),
         _("The tenant will pay the rent of {rent} on or before day {due_day} of each month, and it is late after "
           "{grace_days} days' grace. Payment is made as the landlord directs and a receipt is given for every payment.")),
        (_("Deposit"),
         _("The tenant has paid or will pay a deposit of {deposit}. It is held as security for rent, utilities and "
           "damage beyond fair wear and tear. It is not rent and may not be used by the tenant as the last month's "
           "rent. What remains is refunded after the tenant gives up the premises, with a statement of any deductions.")),
        (_("Utilities and service charges"),
         _("Unless this agreement says otherwise, the tenant pays for the electricity, water and other services the "
           "tenant uses, and any service charge recorded on the lease.")),
        (_("Care of the premises"),
         _("The tenant will keep the premises clean and in good condition, report any damage or fault to the landlord "
           "promptly, and pay for damage the tenant or the tenant's visitors cause. The tenant will not alter the "
           "premises or fix anything to the structure without the landlord's written consent.")),
        (_("Repairs"),
         _("The landlord is responsible for the structure and the main fixtures and services, and will repair faults "
           "the tenant reports within a reasonable time.")),
        (_("Sub-letting and occupants"),
         _("The tenant will not sub-let, share or give up possession of the premises, or let more people live there "
           "than the landlord has agreed, without the landlord's written consent.")),
        (_("Entry and inspection"),
         _("The landlord or the landlord's agent may enter the premises at reasonable times, after giving the tenant "
           "reasonable notice, to inspect them, carry out repairs or show them to a new tenant or buyer. In an "
           "emergency no notice is needed.")),
        (_("Quiet enjoyment"),
         _("As long as the tenant pays the rent and keeps to this agreement, the landlord will let the tenant live "
           "in the premises without interference.")),
        (_("Ending the tenancy"),
         _("Either party may end the tenancy by giving {notice_days} days' written notice. At the end the tenant will "
           "give up the premises clean, in the condition in which they were received (fair wear and tear excepted), "
           "and return all keys.")),
        (_("Default"),
         _("If the tenant does not pay the rent when due or breaks any other condition, the landlord may give written "
           "notice and, if it is not put right, end the tenancy and recover the premises as the law allows. The "
           "landlord will not lock out the tenant or remove the tenant's belongings except by lawful process.")),
        (_("Notices"),
         _("A notice under this agreement is given in writing, by hand, post, email or SMS to the address, email or "
           "phone number of the other party in this agreement, or any later one they give in writing.")),
        (_("Law"),
         _("This agreement is governed by the laws of Kenya. A dispute is first referred to the parties to settle by "
           "talking, and failing that to the Business Premises Rent Tribunal, Rent Restriction Tribunal or the courts, "
           "as applies.")),
    ])


def parse(text: str) -> list[tuple[str, str]]:
    """[(heading, body)] from the owner's text, skipping empty blocks."""
    clauses = []
    for block in re.split(r"\n\s*\n", (text or "").strip().replace("\r\n", "\n")):
        lines = [line.rstrip() for line in block.strip().splitlines()]
        if not lines:
            continue
        clauses.append((lines[0].strip(), " ".join(line.strip() for line in lines[1:])))
    return clauses


def fill(text: str, values: dict[str, str]) -> str:
    """Put the lease's values into `{name}` markers. Unknown markers are left as written; nothing else is evaluated."""
    return _TOKEN.sub(lambda m: str(values[m.group(1)]) if m.group(1) in values else m.group(0), text)


def clauses_for(org) -> list[tuple[str, str]]:
    """The owner's clauses, or the standard ones when they have not written their own."""
    return parse(org.agreement_clauses) or parse(default_text())


def check(text: str) -> None:
    """Raise ValueError for text that cannot be printed as clauses."""
    if len(text) > MAX_LENGTH:
        raise ValueError(_("The wording is too long. Keep it under %(n)s characters.") % {"n": MAX_LENGTH})
    unknown = sorted({name for name in _TOKEN.findall(text) if name not in PLACEHOLDERS})
    if unknown:
        raise ValueError(_("Unknown marker: %(names)s. Use only %(allowed)s.") % {
            "names": ", ".join("{" + n + "}" for n in unknown),
            "allowed": ", ".join("{" + n + "}" for n in PLACEHOLDERS)})
    if text.strip() and not parse(text):
        raise ValueError(_("Write at least one clause."))
