import urllib.error
import urllib.parse
import urllib.request

import arrow
import pytest

from inbox.models import Calendar, Event


@pytest.fixture
def make_recurring_event(db, default_namespace):
    def _make_recurring_event(**kwargs):
        default_kwargs = {
            "namespace_id": default_namespace.id,
            "title": "recurring-weekly",
            "description": "",
            "uid": "recurapitest",
            "location": "",
            "busy": False,
            "read_only": False,
            "reminders": "",
            "recurrence": [
                "RRULE:FREQ=WEEKLY",
                "EXDATE:20150324T013000,20150331T013000Z",
            ],
            "start": arrow.get(2015, 3, 17, 1, 30, 0),
            "end": arrow.get(2015, 3, 17, 1, 45, 0),
            "all_day": False,
            "is_owner": True,
            "participants": [],
            "provider_name": "inbox",
            "raw_data": "",
            "original_start_tz": "America/Los_Angeles",
            "original_start_time": None,
            "master_event_uid": None,
            "source": "local",
        }
        if "calendar" not in kwargs:
            default_kwargs["calendar"] = (
                db.session.query(Calendar)
                .filter_by(namespace_id=default_namespace.id)
                .order_by("id")
                .first()
            )
        default_kwargs.update(kwargs)

        ev = Event.create(**default_kwargs)
        db.session.add(ev)
        db.session.commit()
        return ev

    return _make_recurring_event


@pytest.fixture(params=[{"all_day": True}, {"all_day": False}])
def recurring_event(request, make_recurring_event):
    params = request.param
    all_day = params.get("all_day", False)
    return make_recurring_event(all_day=all_day)


@pytest.fixture
def recurring_event_with_invalid_tz(make_recurring_event):
    return make_recurring_event(original_start_tz="GMT+09:00")


@pytest.fixture
def recurring_event_with_bonkers_tz(make_recurring_event):
    return make_recurring_event(original_start_tz="ThisAint/NoTimeZone")


def test_api_expand_recurring(db, api_client, recurring_event):
    event = recurring_event

    events = api_client.get_data("/events?expand_recurring=false")
    assert len(events) == 1
    # Make sure the recurrence info is on the recurring event
    for e in events:
        if e["title"] == "recurring-weekly":
            assert e.get("recurrence") is not None

    thirty_weeks = event.start.shift(weeks=+30).isoformat()
    starts_after = event.start.shift(days=-1).isoformat()
    recur = "expand_recurring=true&starts_after={}&ends_before={}".format(
        urllib.parse.quote_plus(starts_after), urllib.parse.quote_plus(thirty_weeks)
    )
    all_events = api_client.get_data("/events?" + recur)

    if not event.all_day:
        assert len(all_events) == 28

        # the ordering should be correct
        prev = all_events[0]["when"]["start_time"]
        for e in all_events[1:]:
            assert e["when"]["start_time"] > prev
            prev = e["when"]["start_time"]

            # Check that the parent event recurring id is included
            # too.
            assert e["calendar_id"] == recurring_event.calendar.public_id

        events = api_client.get_data("/events?" + recur + "&view=count")
        assert events.get("count") == 28
    else:
        # Since an all-day event starts at 00:00 we're returning one
        # more event.
        assert len(all_events) == 29
        # the ordering should be correct
        prev = all_events[0]["when"]["date"]
        for e in all_events[1:]:
            assert e["when"]["date"] > prev
            prev = e["when"]["date"]

            # Check that the parent event recurring id is included
            # too.
            assert e["calendar_id"] == recurring_event.calendar.public_id

        events = api_client.get_data("/events?" + recur + "&view=count")
        assert events.get("count") == 29

    events = api_client.get_data("/events?" + recur + "&limit=5")
    assert len(events) == 5

    events = api_client.get_data("/events?" + recur + "&offset=5")
    assert events[0]["id"] == all_events[5]["id"]


def urlsafe(dt):
    return urllib.parse.quote_plus(dt.isoformat())


def test_api_expand_recurring_before_after(db, api_client, recurring_event):
    event = recurring_event
    starts_after = event.start.shift(weeks=+15)
    ends_before = starts_after.shift(days=+1)

    recur = "expand_recurring=true&starts_after={}&ends_before={}".format(
        urlsafe(starts_after), urlsafe(ends_before)
    )
    all_events = api_client.get_data("/events?" + recur)
    assert len(all_events) == 1

    recur = "expand_recurring=true&starts_after={}&starts_before={}".format(
        urlsafe(starts_after), urlsafe(ends_before)
    )
    all_events = api_client.get_data("/events?" + recur)
    assert len(all_events) == 1

    recur = "expand_recurring=true&ends_after={}&starts_before={}".format(
        urlsafe(starts_after), urlsafe(ends_before)
    )
    all_events = api_client.get_data("/events?" + recur)
    assert len(all_events) == 1

    recur = "expand_recurring=true&ends_after={}&ends_before={}".format(
        urlsafe(starts_after), urlsafe(ends_before)
    )
    all_events = api_client.get_data("/events?" + recur)
    assert len(all_events) == 1


def test_api_override_serialization(db, api_client, default_namespace, recurring_event):
    event = recurring_event

    override = Event.create(
        original_start_time=event.start,
        master_event_uid=event.uid,
        namespace_id=default_namespace.id,
        calendar_id=event.calendar_id,
    )
    override.update(event)
    override.uid = event.uid + "_" + event.start.strftime("%Y%m%dT%H%M%SZ")
    override.master = event
    override.master_event_uid = event.uid
    override.cancelled = True
    db.session.add(override)
    db.session.commit()

    filter = "starts_after={}&ends_before={}".format(
        urlsafe(event.start.shift(hours=-1)), urlsafe(event.start.shift(weeks=+1))
    )
    events = api_client.get_data("/events?" + filter)
    # We should have the base event and the override back, but no extras;
    # this allows clients to do their own expansion, should they ever desire
    # to experience the joy that is RFC 2445 section 4.8.5.4.
    assert len(events) == 2
    assert events[0].get("object") == "event"
    assert events[0].get("recurrence") is not None
    assert events[1].get("object") == "event"
    assert events[1].get("status") == "cancelled"


def test_api_expand_recurring_message(db, api_client, message, recurring_event):
    # This is a regression test for https://phab.nylas.com/T3556
    # ("InflatedEvent should not be committed" exception in API").
    event = recurring_event
    event.message = message
    db.session.commit()

    events = api_client.get_data("/events?expand_recurring=false")
    assert len(events) == 1

    # Make sure the recurrence info is on the recurring event
    for e in events:
        if e["title"] == "recurring-weekly":
            assert e.get("recurrence") is not None
            assert e.get("message_id") is not None

    r = api_client.get_raw("/events?expand_recurring=true")
    assert r.status_code == 200

    all_events = api_client.get_data("/events?expand_recurring=true")
    assert len(all_events) != 0

    for event in all_events:
        assert event["master_event_id"] is not None
        assert "message_id" not in event


def test_api_get_specific_event(db, api_client, recurring_event):
    event = api_client.get_data("/events/{}".format(recurring_event.public_id))
    assert event["calendar_id"] == recurring_event.calendar.public_id
    assert event["title"] == "recurring-weekly"
    assert event["recurrence"] is not None


def test_get_specific_event_invalid_tz_fixed(
    db, api_client, recurring_event_with_invalid_tz
):
    event = api_client.get_data(
        "/events/{}".format(recurring_event_with_invalid_tz.public_id)
    )
    assert event["calendar_id"] == recurring_event_with_invalid_tz.calendar.public_id
    assert event["title"] == "recurring-weekly"
    assert event["recurrence"]["timezone"] == "Etc/GMT+9"


def test_get_specific_event_with_bonkers_tz(
    db, api_client, recurring_event_with_bonkers_tz
):
    event = api_client.get_data(
        "/events/{}".format(recurring_event_with_bonkers_tz.public_id)
    )
    assert event["calendar_id"] == recurring_event_with_bonkers_tz.calendar.public_id
    assert event["title"] == "recurring-weekly"
    assert event["recurrence"]["timezone"] == "ThisAint/NoTimeZone"
