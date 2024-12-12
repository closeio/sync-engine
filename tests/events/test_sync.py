from datetime import datetime

from inbox.events.google import GoogleEventsProvider
from inbox.events.remote_sync import EventSync
from inbox.events.util import CalendarSyncResponse
from inbox.models import Calendar, Event, Transaction
from inbox.models.constants import MAX_INDEXABLE_LENGTH

# Placeholder values for non-nullable attributes
default_params = dict(
    raw_data="",
    busy=True,
    all_day=False,
    read_only=False,
    start=datetime(2015, 2, 22, 11, 11),
    end=datetime(2015, 2, 22, 22, 22),
    is_owner=True,
    participants=[{"email": "japandroids@example.com", "name": "Japandroids"}],
)


# Mock responses from the provider with adds/updates/deletes


def calendar_response():
    return CalendarSyncResponse(
        [],
        [
            Calendar(
                name="Important Meetings",
                uid="first_calendar_uid",
                read_only=False,
            ),
            Calendar(
                name="Nefarious Schemes",
                uid="second_calendar_uid",
                read_only=False,
            ),
        ],
    )


# Returns a calendar with name that is longer that our allowed column length of
# 191 (MAX_INDEXABLE_LENGTH). This name is 192 characters
def calendar_long_name():
    return CalendarSyncResponse(
        [],
        [
            Calendar(
                name="Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed do eiusmod tempor incididunt ut labore et dolore magna aliqua. Ut enim ad minim veniam, quis nostrud exercitation ullamco laboris_!",
                uid="long_calendar_uid",
                read_only=True,
            )
        ],
    )


def calendar_response_with_update():
    return CalendarSyncResponse(
        [],
        [
            Calendar(
                name="Super Important Meetings",
                uid="first_calendar_uid",
                read_only=False,
            )
        ],
    )


def calendar_response_with_delete():
    return (["first_calendar_uid"], [])


def event_response(calendar_uid, sync_from_time):
    if calendar_uid == "first_calendar_uid":
        return [
            Event.create(
                uid="first_event_uid",
                title="Plotting Meeting",
                **default_params,
            ),
            Event.create(
                uid="second_event_uid",
                title="Scheming meeting",
                **default_params,
            ),
            Event.create(
                uid="third_event_uid",
                title="Innocent Meeting",
                **default_params,
            ),
        ]
    else:
        return [
            Event.create(
                uid="second_event_uid",
                title="Plotting Meeting",
                **default_params,
            ),
            Event.create(
                uid="third_event_uid",
                title="Scheming meeting",
                **default_params,
            ),
        ]


def event_response_with_update(calendar_uid, sync_from_time):
    if calendar_uid == "first_calendar_uid":
        return [
            Event.create(
                uid="first_event_uid",
                title="Top Secret Plotting Meeting",
                **default_params,
            )
        ]


def event_response_with_participants_update(calendar_uid, sync_from_time):
    if calendar_uid == "first_calendar_uid":
        new_events = [Event.create(uid="first_event_uid", **default_params)]
        new_events[0].participants = [
            {"name": "Johnny Thunders", "email": "johnny@thunde.rs"}
        ]
        return new_events


def event_response_with_delete(calendar_uid, sync_from_time):
    if calendar_uid == "first_calendar_uid":
        return [
            Event.create(
                uid="first_event_uid", status="cancelled", **default_params
            )
        ]


def test_handle_changes(db, generic_account):
    namespace_id = generic_account.namespace.id
    event_sync = EventSync(
        generic_account.email_address,
        "google",
        generic_account.id,
        namespace_id,
        provider_class=GoogleEventsProvider,
    )

    # Sync calendars/events
    event_sync.provider.sync_calendars = calendar_response
    event_sync.provider.sync_events = event_response
    event_sync.sync()

    assert (
        db.session.query(Calendar)
        .filter(
            Calendar.namespace_id == namespace_id,
            Calendar.name != "Emailed events",
        )
        .count()
        == 2
    )

    assert (
        db.session.query(Event)
        .join(Calendar)
        .filter(
            Event.namespace_id == namespace_id,
            Calendar.uid == "first_calendar_uid",
        )
        .count()
        == 3
    )

    assert (
        db.session.query(Event)
        .join(Calendar)
        .filter(
            Event.namespace_id == namespace_id,
            Calendar.uid == "second_calendar_uid",
        )
        .count()
        == 2
    )

    # Sync a calendar update with long name
    event_sync.provider.sync_calendars = calendar_long_name
    event_sync.sync()

    long_calendar = (
        db.session.query(Calendar)
        .filter(
            Calendar.namespace_id == namespace_id,
            Calendar.uid == "long_calendar_uid",
        )
        .one()
    )

    assert len(long_calendar.name) == MAX_INDEXABLE_LENGTH

    # Sync a calendar update
    event_sync.provider.sync_calendars = calendar_response_with_update
    event_sync.provider.sync_events = event_response
    event_sync.sync()

    # Check that we have the same number of calendars and events as before
    assert (
        db.session.query(Calendar)
        .filter(
            Calendar.namespace_id == namespace_id,
            Calendar.name != "Emailed events",
        )
        .count()
        == 3
    )

    assert (
        db.session.query(Event)
        .join(Calendar)
        .filter(
            Event.namespace_id == namespace_id,
            Calendar.uid == "first_calendar_uid",
        )
        .count()
        == 3
    )

    assert (
        db.session.query(Event)
        .join(Calendar)
        .filter(
            Event.namespace_id == namespace_id,
            Calendar.uid == "second_calendar_uid",
        )
        .count()
        == 2
    )

    assert (
        db.session.query(Event)
        .join(Calendar)
        .filter(
            Event.namespace_id == namespace_id,
            Calendar.uid == "long_calendar_uid",
        )
        .count()
        == 2
    )

    # Check that calendar attribute was updated.
    first_calendar = (
        db.session.query(Calendar)
        .filter(
            Calendar.namespace_id == namespace_id,
            Calendar.uid == "first_calendar_uid",
        )
        .one()
    )
    assert first_calendar.name == "Super Important Meetings"

    # Sync an event update
    event_sync.provider.sync_events = event_response_with_update
    event_sync.sync()
    # Make sure the update was persisted
    first_event = (
        db.session.query(Event)
        .filter(
            Event.namespace_id == namespace_id,
            Event.calendar_id == first_calendar.id,
            Event.uid == "first_event_uid",
        )
        .one()
    )
    assert first_event.title == "Top Secret Plotting Meeting"

    # Sync a participant update
    event_sync.provider.sync_events = event_response_with_participants_update
    event_sync.sync()

    # Make sure the update was persisted
    first_event = (
        db.session.query(Event)
        .filter(
            Event.namespace_id == namespace_id,
            Event.calendar_id == first_calendar.id,
            Event.uid == "first_event_uid",
        )
        .one()
    )

    db.session.refresh(first_event)
    assert first_event.participants == [
        {"name": "Johnny Thunders", "email": "johnny@thunde.rs"}
    ]

    # Sync an event delete
    event_sync.provider.sync_events = event_response_with_delete
    event_sync.sync()
    # Make sure the delete was persisted.
    first_event = (
        db.session.query(Event)
        .filter(
            Event.namespace_id == namespace_id,
            Event.calendar_id == first_calendar.id,
            Event.uid == "first_event_uid",
        )
        .first()
    )

    db.session.refresh(first_event)
    assert first_event.status == "cancelled"

    # Sync a calendar delete
    event_public_ids = [
        id_
        for id_, in db.session.query(Event.public_id).filter(
            Event.namespace_id == namespace_id,
            Event.calendar_id == first_calendar.id,
        )
    ]
    event_sync.provider.sync_calendars = calendar_response_with_delete
    event_sync.sync()
    assert (
        db.session.query(Calendar)
        .filter(
            Calendar.namespace_id == namespace_id,
            Calendar.uid == "first_calendar_uid",
        )
        .first()
        is None
    )

    # Check that delete transactions are created for events on the deleted
    # calendar.
    deleted_event_transactions = (
        db.session.query(Transaction)
        .filter(
            Transaction.object_type == "event",
            Transaction.command == "delete",
            Transaction.namespace_id == namespace_id,
            Transaction.object_public_id.in_(event_public_ids),
        )
        .all()
    )
    assert len(deleted_event_transactions) == 3

    # Check that events with the same uid but associated to a different
    # calendar still survive.
    assert (
        db.session.query(Event)
        .filter(Event.namespace_id == namespace_id)
        .count()
        == 4
    )
