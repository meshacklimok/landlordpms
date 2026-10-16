"""The end-of-month nudge to read the meters (D-155)."""

import datetime

import pytest

from meters import reminders, services
from notifications.models import Message

from .conftest import let, make_meter, read

pytestmark = pytest.mark.django_db
D = datetime.date


@pytest.fixture
def rooms(owner, prop):
    _l, a1 = let(owner, prop, code="A1")
    _l, a2 = let(owner, prop, code="A2")
    return make_meter(owner, prop, [a1], label="W-A1"), make_meter(owner, prop, [a2], label="W-A2")


def sent():
    return Message.objects.filter(type="meters_unread")


def test_nothing_is_sent_before_the_reminder_days(owner, prop, rooms):
    assert reminders.run_daily(D(2026, 3, 20)) == {"meter_reminders": 0}
    assert not sent().exists()


def test_the_unread_count_is_sent_once_a_day(owner, prop, rooms):
    first = reminders.run_daily(D(2026, 3, 26))
    assert first["meter_reminders"] >= 1
    message = sent().get(user=owner.user)
    assert "2 meter(s)" in message.body and prop.name in message.body
    assert reminders.run_daily(D(2026, 3, 26))["meter_reminders"] == 0  # same day: no second alert
    assert reminders.run_daily(D(2026, 3, 27))["meter_reminders"] >= 1


def test_a_meter_read_this_month_is_not_counted(owner, prop, rooms):
    read(owner, rooms[0], D(2026, 3, 24), 10)
    assert [m.label for m in reminders.unread_this_month(prop, D(2026, 3, 26))] == ["W-A2"]
    reminders.run_daily(D(2026, 3, 26))
    assert "1 meter(s)" in sent().get(user=owner.user).body


def test_nothing_is_sent_when_all_are_read_or_nothing_is_billed(owner, prop, rooms):
    for meter in rooms:
        read(owner, meter, D(2026, 3, 25), 10)
    assert reminders.run_daily(D(2026, 3, 28)) == {"meter_reminders": 0}
    assert not sent().exists()


def test_meters_the_landlord_pays_and_prepaid_ones_are_left_out(owner, prop):
    _l, a1 = let(owner, prop, code="A1")
    _l, a2 = let(owner, prop, code="A2")
    make_meter(owner, prop, [a1], label="Free", payer="LANDLORD")
    make_meter(owner, prop, [a2], label="Token", kind="PREPAID")
    assert reminders.unread_this_month(prop, D(2026, 3, 26)) == []
