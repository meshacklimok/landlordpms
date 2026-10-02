"""The item register a unit starts with (D-047 item 1), by unit type. Staff edit it from there."""

from django.utils.translation import gettext_lazy as _

from properties.models import Unit

T = Unit.Type

_GENERAL = [
    (_("Entrance"), _("Door, lock and handle")),
    (_("General"), _("Walls and paint")),
    (_("General"), _("Floor")),
    (_("General"), _("Ceiling")),
    (_("General"), _("Windows, glass and grilles")),
    (_("General"), _("Lights and switches")),
    (_("General"), _("Sockets")),
]

_KITCHEN = [
    (_("Kitchen"), _("Sink and taps")),
    (_("Kitchen"), _("Cabinets and worktop")),
]

_BATHROOM = [
    (_("Bathroom"), _("Toilet")),
    (_("Bathroom"), _("Shower and water heater")),
    (_("Bathroom"), _("Wash basin and taps")),
]

_KEYS = [(_("Handover"), _("Keys"))]

_HOME = _GENERAL + _KITCHEN + _BATHROOM + [
    (_("Bedroom"), _("Wardrobe")),
    (_("Bedroom"), _("Door and lock")),
] + _KEYS

_ROOM = _GENERAL + _KITCHEN + _BATHROOM + _KEYS

_COMMERCIAL = [
    (_("Entrance"), _("Door or shutter, lock")),
    (_("General"), _("Walls and paint")),
    (_("General"), _("Floor")),
    (_("General"), _("Ceiling")),
    (_("General"), _("Windows and glass")),
    (_("General"), _("Lights and switches")),
    (_("General"), _("Sockets")),
    (_("General"), _("Toilet and wash basin")),
] + _KEYS

_PARKING = [
    (_("Bay"), _("Floor and markings")),
    (_("Bay"), _("Gate access card or key")),
]

_BED = [
    (_("Room"), _("Bed and mattress")),
    (_("Room"), _("Locker or wardrobe")),
    (_("Room"), _("Walls, floor and window")),
] + _KEYS

DEFAULT_ITEMS = {
    T.APARTMENT: _HOME,
    T.HOUSE: _HOME,
    T.BEDSITTER: _ROOM,
    T.STUDIO: _ROOM,
    T.SHOP: _COMMERCIAL,
    T.OFFICE: _COMMERCIAL,
    T.WAREHOUSE: _COMMERCIAL,
    T.PARKING: _PARKING,
    T.BED_SPACE: _BED,
    T.OTHER: _GENERAL + _KEYS,
}


def default_items(unit_type: str) -> list[tuple[str, str]]:
    """(area, item) pairs as plain strings, in the organization's language at the time."""
    return [(str(area), str(name)) for area, name in DEFAULT_ITEMS.get(unit_type, _GENERAL + _KEYS)]
