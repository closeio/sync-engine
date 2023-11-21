import datetime
from datetime import timedelta

import arrow
import pytest
from dateutil import tz
from dateutil.rrule import rrulestr

from inbox.events.recurring import (
    get_start_times,
    link_events,
    parse_exdate,
    rrule_to_json,
)
from inbox.events.remote_sync import handle_event_updates
from inbox.logging import get_logger
from inbox.models.event import Event, RecurringEvent, RecurringEventOverride
from inbox.models.when import Date, DateSpan, Time, TimeSpan

log = get_logger()

TEST_RRULE = ["RRULE:FREQ=WEEKLY;UNTIL=20140918T203000Z;BYDAY=TH"]
TEST_EXDATE = ["EXDATE;TZID=America/Los_Angeles:20140904T133000"]
ALL_DAY_RRULE = ["RRULE:FREQ=WEEKLY;UNTIL=20140911;BYDAY=TH"]
TEST_EXDATE_RULE = TEST_RRULE[:]
TEST_EXDATE_RULE.extend(TEST_EXDATE)


def recurring_event(
    db,
    account,
    calendar,
    rrule,
    start=None,
    end=None,
    all_day=False,
    commit=True,
):
    start = start or arrow.get(2014, 8, 7, 20, 30, 0)
    end = end or arrow.get(2014, 8, 7, 21, 30, 0)

    # commit: are we returning a commited instance object?
    if commit:
        ev = db.session.query(Event).filter_by(uid="myuid").first()
        if ev:
            db.session.delete(ev)
    ev = Event.create(
        namespace_id=account.namespace.id,
        calendar=calendar,
        title="recurring",
        description="",
        uid="myuid",
        location="",
        busy=False,
        read_only=False,
        reminders="",
        recurrence=rrule,
        start=start,
        end=end,
        all_day=all_day,
        is_owner=False,
        participants=[],
        provider_name="inbox",
        raw_data="",
        original_start_tz="America/Los_Angeles",
        original_start_time=None,
        master_event_uid=None,
        source="local",
    )

    if commit:
        db.session.add(ev)
        db.session.commit()

    return ev


def recurring_override(db, master, original_start, start, end):
    # Returns an Override that is explicitly linked to master
    ev = recurring_override_instance(db, master, original_start, start, end)
    ev.master = master
    db.session.commit()
    return ev


def recurring_override_instance(db, master, original_start, start, end):
    # Returns an Override that has the master's UID, but is not linked yet
    override_uid = "{}_{}".format(master.uid, original_start.strftime("%Y%m%dT%H%M%SZ"))
    ev = db.session.query(Event).filter_by(uid=override_uid).first()
    if ev:
        db.session.delete(ev)
    db.session.commit()
    ev = Event.create(
        original_start_time=original_start,
        master_event_uid=master.uid,
        namespace_id=master.namespace_id,
        calendar_id=master.calendar_id,
    )
    ev.update(master)
    ev.uid = override_uid
    ev.start = start
    ev.end = end
    ev.master_event_uid = master.uid
    db.session.add(ev)
    return ev


def test_create_recurrence(db, default_account, calendar):
    event = recurring_event(db, default_account, calendar, TEST_EXDATE_RULE)
    assert isinstance(event, RecurringEvent)
    assert event.rrule is not None
    assert event.exdate is not None
    assert event.until is not None


def test_link_events_from_override(db, default_account, calendar, other_calendar):
    # Test that by creating a recurring event and override separately, we
    # can link them together based on UID and namespace_id when starting
    # from the override.
    master = recurring_event(db, default_account, calendar, TEST_EXDATE_RULE)
    original_start = parse_exdate(master)[0]
    override = Event.create(
        original_start_time=original_start,
        master_event_uid=master.uid,
        namespace_id=master.namespace_id,
        calendar_id=calendar.id,
        source="local",
    )
    assert isinstance(override, RecurringEventOverride)
    link_events(db.session, override)
    assert override.master == master


def test_linking_events_from_different_calendars(
    db, default_account, calendar, other_calendar
):
    # Test that two events with the same UID but in different calendars don't
    # get linked together. This is important because with the Google API, a
    # recurring events can be in two calendars and have the same UID.
    # In this case, we create two different recurring events.
    master = recurring_event(db, default_account, calendar, TEST_EXDATE_RULE)
    original_start = parse_exdate(master)[0]
    override = Event.create(
        original_start_time=original_start,
        master_event_uid=master.uid,
        namespace_id=master.namespace_id,
        calendar_id=other_calendar.id,
        uid="blah",
        source="local",
    )

    assert isinstance(override, RecurringEventOverride)
    link_events(db.session, override)
    assert override.master is None


def test_link_events_from_master(db, default_account, calendar):
    # Test that by creating a recurring event and override separately, we
    # can link them together based on UID and namespace_id when starting
    # from the master event.
    master = recurring_event(db, default_account, calendar, TEST_EXDATE_RULE)
    original_start = parse_exdate(master)[0]
    override = recurring_override_instance(
        db, master, original_start, master.start, master.end
    )
    assert isinstance(master, RecurringEvent)
    assert len(link_events(db.session, master)) == 1
    assert override in master.overrides
    assert override.uid in [o.uid for o in master.overrides]


def test_link_events_from_master_diff_calendars(
    db, default_account, calendar, other_calendar
):
    # Same as the previous test except that we check that it doesn't work across
    # calendars (see test_link_events_from_master_diff_calendars for more
    # details).
    master = recurring_event(db, default_account, calendar, TEST_EXDATE_RULE)
    original_start = parse_exdate(master)[0]
    override = recurring_override_instance(
        db, master, original_start, master.start, master.end
    )
    override.calendar = other_calendar
    assert isinstance(master, RecurringEvent)
    o = link_events(db.session, master)
    assert len(o) == 0


def test_rrule_parsing(db, default_account, calendar):
    # This test event starts on Aug 7 and recurs every Thursday at 20:30
    # until Sept 18.
    # There should be 7 total occurrences including Aug 7 and Sept 18.
    event = recurring_event(db, default_account, calendar, TEST_RRULE)
    g = get_start_times(event)
    assert len(g) == 7
    # Check we can supply an end date to cut off recurrence expansion
    g = get_start_times(event, end=arrow.get(2014, 9, 12, 21, 30, 0))
    assert len(g) == 6


def test_all_day_rrule_parsing(db, default_account, calendar):
    event = recurring_event(
        db,
        default_account,
        calendar,
        ALL_DAY_RRULE,
        start=arrow.get(2014, 8, 7),
        end=arrow.get(2014, 8, 7),
        all_day=True,
    )
    g = get_start_times(event)
    assert len(g) == 6


@pytest.mark.parametrize(
    "rule",
    [
        "RRULE:FREQ=DAILY;UNTIL=20160913",
        "RRULE:FREQ=DAILY;UNTIL=20160913T070000Z",
        "RRULE:FREQ=DAILY;UNTIL=20160913T070000",
    ],
)
def test_all_day_rrule_parsing_utc(db, default_account, calendar, rule):
    # Use an RRULE with timezone away until date + all day event
    event = recurring_event(
        db,
        default_account,
        calendar,
        rule,
        start=arrow.get(2016, 9, 10),
        end=arrow.get(2016, 9, 13),
        all_day=True,
    )

    start_boundary = datetime.datetime(2016, 9, 8, 1, 21, 55)
    end_boundary = datetime.datetime(2016, 9, 16, 0, 31, 55)

    g = get_start_times(event, start=start_boundary, end=end_boundary)
    assert len(g) == 4


def test_rrule_exceptions(db, default_account, calendar):
    # This test event starts on Aug 7 and recurs every Thursday at 20:30
    # until Sept 18, except on September 4.
    event = recurring_event(db, default_account, calendar, TEST_EXDATE_RULE)
    g = get_start_times(event)
    assert len(g) == 6
    assert arrow.get(2014, 9, 4, 13, 30, 0) not in g


def test_inflation(db, default_account, calendar):
    event = recurring_event(db, default_account, calendar, TEST_RRULE)
    infl = event.inflate()
    for i in infl:
        assert i.title == event.title
        assert (i.end - i.start) == (event.end - event.start)
        assert i.public_id.startswith(event.public_id)
    # make sure the original event instance appears too
    assert event.start in [e.start for e in infl]


def test_inflation_exceptions(db, default_account, calendar):
    event = recurring_event(db, default_account, calendar, TEST_RRULE)
    infl = event.inflate()
    for i in infl:
        assert i.title == event.title
        assert (i.end - i.start) == (event.end - event.start)
        assert i.start != arrow.get(2014, 9, 4, 13, 30, 0)


def test_inflate_across_DST(db, default_account, calendar):
    # If we inflate a RRULE that covers a change to/from Daylight Savings Time,
    # adjust the base time accordingly to account for the new UTC offset.
    # Daylight Savings for US/PST: March 8, 2015 - Nov 1, 2015
    dst_rrule = ["RRULE:FREQ=WEEKLY;BYDAY=TU"]
    dst_event = recurring_event(
        db,
        default_account,
        calendar,
        dst_rrule,
        start=arrow.get(2015, 3, 3, 3, 3, 3),
        end=arrow.get(2015, 3, 3, 4, 3, 3),
    )
    g = get_start_times(dst_event, end=arrow.get(2015, 3, 21))

    # In order for this event to occur at the same local time, the recurrence
    # rule should be expanded to 03:03:03 before March 8, and 02:03:03 after,
    # keeping the local time of the event consistent at 19:03.
    # This is consistent with how Google returns recurring event instances.
    local_tz = tz.gettz(dst_event.start_timezone)

    for time in g:
        if time < arrow.get(2015, 3, 8):
            assert time.hour == 3
        else:
            assert time.hour == 2
        # Test that localizing these times is consistent
        assert time.astimezone(local_tz).hour == 19

    # Test an event that starts during local daylight savings time
    dst_event = recurring_event(
        db,
        default_account,
        calendar,
        dst_rrule,
        start=arrow.get(2015, 10, 27, 2, 3, 3),
        end=arrow.get(2015, 10, 27, 3, 3, 3),
    )
    g = get_start_times(dst_event, end=arrow.get(2015, 11, 11))
    for time in g:
        if time > arrow.get(2015, 11, 1):
            assert time.hour == 3
        else:
            assert time.hour == 2
        assert time.astimezone(local_tz).hour == 19


def test_inflate_all_day_event(db, default_account, calendar):
    event = recurring_event(
        db,
        default_account,
        calendar,
        ALL_DAY_RRULE,
        start=arrow.get(2014, 9, 4),
        end=arrow.get(2014, 9, 4),
        all_day=True,
    )
    infl = event.inflate()
    for i in infl:
        assert i.all_day
        assert isinstance(i.when, Date)
        assert i.start in [arrow.get(2014, 9, 4), arrow.get(2014, 9, 11)]


def test_inflate_multi_day_event(db, default_account, calendar):
    event = recurring_event(
        db,
        default_account,
        calendar,
        ALL_DAY_RRULE,
        start=arrow.get(2014, 9, 4),
        end=arrow.get(2014, 9, 5),
        all_day=True,
    )
    infl = event.inflate()
    for i in infl:
        assert i.all_day
        assert isinstance(i.when, DateSpan)
        assert i.start in [arrow.get(2014, 9, 4), arrow.get(2014, 9, 11)]
        assert i.end in [arrow.get(2014, 9, 5), arrow.get(2014, 9, 12)]


def test_invalid_rrule_entry(db, default_account, calendar):
    # If we don't know how to expand the RRULE, we treat the event as if
    # it were a single instance.
    event = recurring_event(db, default_account, calendar, "INVALID_RRULE_YAY")
    infl = event.inflate()
    assert len(infl) == 1
    assert infl[0].start == event.start


def test_invalid_parseable_rrule_entry(db, default_account, calendar):
    event = recurring_event(
        db, default_account, calendar, ["RRULE:FREQ=CHRISTMAS;UNTIL=1984;BYDAY=QQ"]
    )
    infl = event.inflate()
    assert len(infl) == 1
    assert infl[0].start == event.start


def test_non_recurring_events_behave(db, default_account, calendar):
    event = Event.create(
        namespace_id=default_account.namespace.id,
        calendar=calendar,
        title="not recurring",
        description="",
        uid="non_recurring_uid",
        location="",
        busy=False,
        read_only=False,
        reminders="",
        recurrence=None,
        start=arrow.get(2014, 7, 7, 13, 30),
        end=arrow.get(2014, 7, 7, 13, 55),
        all_day=False,
        is_owner=False,
        participants=[],
        provider_name="inbox",
        raw_data="",
        original_start_tz="America/Los_Angeles",
        original_start_time=None,
        master_event_uid=None,
        source="local",
    )
    assert isinstance(event, Event)
    with pytest.raises(AttributeError):
        event.inflate()


def test_inflated_events_cant_persist(db, default_account, calendar):
    event = recurring_event(db, default_account, calendar, TEST_RRULE)
    infl = event.inflate()
    for i in infl:
        db.session.add(i)
    with pytest.raises(Exception) as excinfo:
        # FIXME "No handlers could be found for logger" - ensure this is only
        # a test issue or fix.
        db.session.commit()
        assert "should not be committed" in str(excinfo.value)


def test_override_instantiated(db, default_account, calendar):
    # Test that when a recurring event has overrides, they show up as
    # RecurringEventOverrides, have links back to the parent, and don't
    # appear twice in the event list.
    event = recurring_event(db, default_account, calendar, TEST_EXDATE_RULE)
    override = recurring_override(
        db,
        event,
        arrow.get(2014, 9, 4, 20, 30, 0),
        arrow.get(2014, 9, 4, 21, 30, 0),
        arrow.get(2014, 9, 4, 22, 30, 0),
    )
    all_events = event.all_events()
    assert len(all_events) == 7
    assert override in all_events


def test_override_same_start(db, default_account, calendar):
    # Test that when a recurring event has an override without a modified
    # start date (ie. the RRULE has no EXDATE for that event), it doesn't
    # appear twice in the all_events list.
    event = recurring_event(db, default_account, calendar, TEST_RRULE)
    override = recurring_override(
        db,
        event,
        arrow.get(2014, 9, 4, 20, 30, 0),
        arrow.get(2014, 9, 4, 20, 30, 0),
        arrow.get(2014, 9, 4, 21, 30, 0),
    )
    all_events = event.all_events()
    assert len(all_events) == 7
    unique_starts = list({e.start for e in all_events})
    assert len(unique_starts) == 7
    assert override in all_events


def test_override_updated(db, default_account, calendar):
    # Test that when a recurring event override is created or updated
    # remotely, we update our override links appropriately.
    event = recurring_event(db, default_account, calendar, TEST_RRULE)
    assert event is not None
    # create a new Event, as if we just got it from Google
    master_uid = event.uid
    override_uid = master_uid + "_20140814T203000Z"
    override = Event.create(
        title="new override from google",
        description="",
        uid=override_uid,
        location="",
        busy=False,
        read_only=False,
        reminders="",
        recurrence=None,
        start=arrow.get(2014, 8, 14, 22, 30, 0),
        end=arrow.get(2014, 8, 14, 23, 30, 0),
        all_day=False,
        is_owner=False,
        participants=[],
        provider_name="inbox",
        raw_data="",
        original_start_tz="America/Los_Angeles",
        original_start_time=arrow.get(2014, 8, 14, 21, 30, 0),
        master_event_uid=master_uid,
        source="local",
    )
    handle_event_updates(
        default_account.namespace.id, calendar.id, [override], log, db.session
    )
    db.session.commit()
    # Lets see if the event got saved with the right info
    find_override = db.session.query(Event).filter_by(uid=override_uid).one()
    assert find_override is not None
    assert find_override.master_event_id == event.id

    # Update the same override, making sure we don't create two
    override = Event.create(
        title="new override from google",
        description="",
        uid=override_uid,
        location="walk and talk",
        busy=False,
        read_only=False,
        reminders="",
        recurrence=None,
        start=arrow.get(2014, 8, 14, 22, 15, 0),
        end=arrow.get(2014, 8, 14, 23, 15, 0),
        all_day=False,
        is_owner=False,
        participants=[],
        provider_name="inbox",
        raw_data="",
        original_start_tz="America/Los_Angeles",
        original_start_time=arrow.get(2014, 8, 14, 21, 30, 0),
        master_event_uid=master_uid,
        source="local",
    )
    handle_event_updates(
        default_account.namespace.id, calendar.id, [override], log, db.session
    )
    db.session.commit()
    # Let's see if the event got saved with the right info
    find_override = db.session.query(Event).filter_by(uid=override_uid).one()
    assert find_override is not None
    assert find_override.master_event_id == event.id
    assert find_override.location == "walk and talk"


def test_override_cancelled(db, default_account, calendar):
    # Test that overrides with status 'cancelled' are appropriately missing
    # from the expanded event.
    event = recurring_event(db, default_account, calendar, TEST_EXDATE_RULE)
    override = recurring_override(
        db,
        event,
        arrow.get(2014, 9, 4, 20, 30, 0),
        arrow.get(2014, 9, 4, 21, 30, 0),
        arrow.get(2014, 9, 4, 22, 30, 0),
    )
    override.cancelled = True
    all_events = event.all_events()
    assert len(all_events) == 6
    assert override not in all_events
    assert not any([e.start == arrow.get(2014, 9, 4, 20, 30, 0) for e in all_events])


def test_new_instance_cancelled(db, default_account, calendar):
    # Test that if we receive a cancelled override from Google, we save it
    # as an override with cancelled status rather than deleting it.
    event = recurring_event(db, default_account, calendar, TEST_EXDATE_RULE)
    override_uid = event.uid + "_20140814T203000Z"
    override = Event.create(
        title="CANCELLED",
        description="",
        uid=override_uid,
        location="",
        busy=False,
        read_only=False,
        reminders="",
        recurrence=None,
        start=arrow.get(2014, 8, 14, 22, 15, 0),
        end=arrow.get(2014, 8, 14, 23, 15, 0),
        all_day=False,
        is_owner=False,
        participants=[],
        provider_name="inbox",
        raw_data="",
        original_start_tz="America/Los_Angeles",
        original_start_time=arrow.get(2014, 8, 14, 21, 30, 0),
        master_event_uid=event.uid,
        cancelled=True,
        source="local",
    )
    handle_event_updates(
        default_account.namespace.id, calendar.id, [override], log, db.session
    )
    db.session.commit()
    # Check the event got saved with the cancelled flag
    find_override = (
        db.session.query(Event)
        .filter_by(uid=override_uid, namespace_id=default_account.namespace.id)
        .one()
    )
    assert find_override.cancelled is True


def test_when_delta():
    # Test that the event length is calculated correctly
    ev = Event.create(namespace_id=0)
    # Time: minutes is 0 if start/end at same time
    ev.start = arrow.get(2015, 1, 1, 10, 0, 0)
    ev.end = arrow.get(2015, 1, 1, 10, 0, 0)
    when = ev.when
    assert isinstance(when, Time)
    assert ev.length == timedelta(minutes=0)

    # TimeSpan
    ev.start = arrow.get(2015, 1, 1, 10, 0, 0)
    ev.end = arrow.get(2015, 1, 1, 10, 30, 0)
    when = ev.when
    assert isinstance(when, TimeSpan)
    assert ev.length == timedelta(minutes=30)

    # Date: notice days is 0 if starts/ends on same day
    ev.all_day = True
    ev.start = arrow.get(2015, 1, 1, 0, 0, 0)
    ev.end = arrow.get(2015, 1, 1, 0, 0, 0)
    when = ev.when
    assert isinstance(when, Date)
    assert ev.length == timedelta(days=0)

    # DateSpan
    ev.all_day = True
    ev.start = arrow.get(2015, 1, 1, 10, 0, 0)
    ev.end = arrow.get(2015, 1, 2, 10, 0, 0)
    when = ev.when
    assert isinstance(when, DateSpan)
    assert ev.length == timedelta(days=1)


def test_rrule_to_json():
    # Generate more test cases!
    # http://jakubroztocil.github.io/rrule/
    r = "RRULE:FREQ=WEEKLY;UNTIL=20140918T203000Z;BYDAY=TH"
    r = rrulestr(r, dtstart=None)
    j = rrule_to_json(r)
    assert j.get("freq") == "WEEKLY"
    assert j.get("byweekday") == "TH"

    r = "FREQ=HOURLY;COUNT=30;WKST=MO;BYMONTH=1;BYMINUTE=42;BYSECOND=24"
    r = rrulestr(r, dtstart=None)
    j = rrule_to_json(r)
    assert j.get("until") is None
    assert j.get("byminute") == 42


def test_master_cancelled(db, default_account, calendar):
    # Test that when the master recurring event is cancelled, we cancel every
    # override too.
    event = recurring_event(db, default_account, calendar, TEST_EXDATE_RULE)
    override = recurring_override(
        db,
        event,
        arrow.get(2014, 9, 4, 20, 30, 0),
        arrow.get(2014, 9, 4, 21, 30, 0),
        arrow.get(2014, 9, 4, 22, 30, 0),
    )

    update = recurring_event(
        db, default_account, calendar, TEST_EXDATE_RULE, commit=False
    )
    update.status = "cancelled"
    updates = [update]

    handle_event_updates(
        default_account.namespace.id, calendar.id, updates, log, db.session
    )
    db.session.commit()
    find_master = db.session.query(Event).filter_by(uid=event.uid).first()
    assert find_master.status == "cancelled"

    find_override = db.session.query(Event).filter_by(uid=override.uid).first()
    assert find_override.status == "cancelled"


def test_made_recurring_then_cancelled(db, default_account, calendar):
    # Test that when an event is updated with a recurrence and cancelled at
    # the same time, we cancel it.
    normal = recurring_event(db, default_account, calendar, None)
    # Check this is specifically an Event, not a RecurringEvent
    assert type(normal) == Event

    # Update with a recurrence rule *and* cancellation
    update = recurring_event(
        db, default_account, calendar, TEST_EXDATE_RULE, commit=False
    )
    update.status = "cancelled"
    updates = [update]

    handle_event_updates(
        default_account.namespace.id, calendar.id, updates, log, db.session
    )
    db.session.commit()

    find_master = db.session.query(Event).filter_by(uid=normal.uid).first()
    assert find_master.status == "cancelled"
