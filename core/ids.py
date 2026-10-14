"""Public ids typed into forms and links (D-097)."""

import uuid


def uuid_or_none(value) -> uuid.UUID | None:
    """The id as a UUID, or None for anything else, so a lookup finds nothing instead of failing."""
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        return None
