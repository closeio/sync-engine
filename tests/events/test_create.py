from functools import partial

import pytest

from inbox.models.event import Event, RecurringEvent, RecurringEventOverride

rrule = "RRULE:FREQ=WEEKLY"


@pytest.mark.parametrize(
    "cls", [Event, partial(RecurringEvent, recurrence=rrule), RecurringEventOverride]
)
def test_init_explodes(cls):
    with pytest.raises(AssertionError, match="Use Event.create"):
        cls()


@pytest.mark.parametrize(
    "kwargs,cls",
    [
        ({}, Event),
        ({"recurrence": rrule}, RecurringEvent),
        (
            {"master_event_uid": "123e4567-e89b-12d3-a456-426655440000"},
            RecurringEventOverride,
        ),
    ],
)
def test_create_class(kwargs, cls):
    assert isinstance(Event.create(**kwargs), cls)
