import datetime
from unittest import mock

import pytest
import pytz
import responses
from responses.matchers import json_params_matcher

from inbox.config import config
from inbox.events.microsoft.events_provider import MicrosoftEventsProvider
from inbox.events.microsoft.graph_client import BASE_URL
from inbox.events.remote_sync import WebhookEventSync
from inbox.models.calendar import Calendar
from inbox.models.event import Event, RecurringEvent


@pytest.fixture(autouse=True)
def populate_microsoft_subscrtipion_secret():
    with mock.patch.dict(config, {"MICROSOFT_SUBSCRIPTION_SECRET": "good_s3cr3t"}):
        yield


@pytest.fixture(autouse=True)
def populate_url_prefix():
    with mock.patch("inbox.events.remote_sync.URL_PREFIX", "https://example.com"):
        yield


@pytest.fixture
def calendars_response():
    responses.get(
        BASE_URL + "/me/calendars",
        json={
            "value": [
                {
                    "id": "fake_calendar_id",
                    "name": "Calendar",
                    "canEdit": True,
                    "isDefaultCalendar": True,
                },
                {
                    "id": "fake_test_calendar_id",
                    "name": "Test",
                    "canEdit": True,
                    "isDefaultCalendar": False,
                },
            ],
        },
    )


@pytest.fixture
def events_responses():
    responses.get(
        BASE_URL + "/me/calendars/fake_calendar_id/events",
        json={
            "value": [
                {
                    "@odata.etag": 'W/"bX+IPMmPs02YGYesdF/+dAAB/52fpA=="',
                    "id": "singular_id",
                    "createdDateTime": "2022-09-07T08:39:36.2273624Z",
                    "lastModifiedDateTime": "2022-09-07T08:41:36.5027961Z",
                    "changeKey": "bX+IPMmPs02YGYesdF/+dAAB/52fpA==",
                    "categories": [],
                    "transactionId": "962593bf-9e1b-ef34-bff6-da63d058df7f",
                    "originalStartTimeZone": "Eastern Standard Time",
                    "originalEndTimeZone": "Eastern Standard Time",
                    "iCalUId": "040000008200E00074C5B7101A82E00800000000D0C4525C95C2D80100000000000000001000000007003FD5ECC09F42A0ACCA4299772507",
                    "reminderMinutesBeforeStart": 15,
                    "isReminderOn": True,
                    "hasAttachments": False,
                    "subject": "Singular",
                    "bodyPreview": "",
                    "importance": "normal",
                    "sensitivity": "normal",
                    "isAllDay": False,
                    "isCancelled": False,
                    "isOrganizer": True,
                    "responseRequested": True,
                    "seriesMasterId": None,
                    "showAs": "busy",
                    "type": "singleInstance",
                    "onlineMeetingUrl": None,
                    "isOnlineMeeting": True,
                    "onlineMeetingProvider": "teamsForBusiness",
                    "allowNewTimeProposals": True,
                    "isDraft": False,
                    "hideAttendees": False,
                    "recurrence": None,
                    "onlineMeeting": {
                        "joinUrl": "https://teams.microsoft.com/l/meetup-join/xyz"
                    },
                    "responseStatus": {
                        "response": "organizer",
                        "time": "0001-01-01T00:00:00Z",
                    },
                    "body": {"contentType": "html", "content": "<i>Singular</i>"},
                    "start": {
                        "dateTime": "2022-09-15T12:00:00.0000000",
                        "timeZone": "UTC",
                    },
                    "end": {
                        "dateTime": "2022-09-15T12:30:00.0000000",
                        "timeZone": "UTC",
                    },
                    "location": {
                        "displayName": "Balcony",
                        "locationType": "default",
                        "uniqueIdType": "unknown",
                        "address": {},
                        "coordinates": {},
                    },
                    "locations": [],
                    "attendees": [],
                    "organizer": {
                        "emailAddress": {
                            "name": "Example <>",
                            "address": "example_2@example.com",
                        }
                    },
                },
                {
                    "@odata.etag": 'W/"bX+IPMmPs02YGYesdF/+dAACDYEM6g=="',
                    "id": "recurrence_id",
                    "createdDateTime": "2022-09-24T15:32:22.239054Z",
                    "lastModifiedDateTime": "2022-09-27T14:41:23.1042764Z",
                    "changeKey": "bX+IPMmPs02YGYesdF/+dAACDYEM6g==",
                    "categories": [],
                    "transactionId": "68faba75-324e-1e37-018d-b239fe0d3c8b",
                    "originalStartTimeZone": "Pacific Standard Time",
                    "originalEndTimeZone": "Pacific Standard Time",
                    "iCalUId": "040000008200E00074C5B7101A82E00800000000F8620CD72AD0D801000000000000000010000000EB99E61264138D46A203CC0931BB688A",
                    "reminderMinutesBeforeStart": 15,
                    "isReminderOn": True,
                    "hasAttachments": False,
                    "subject": "Recurring",
                    "bodyPreview": "",
                    "importance": "normal",
                    "sensitivity": "normal",
                    "isAllDay": False,
                    "isCancelled": False,
                    "isOrganizer": True,
                    "responseRequested": True,
                    "seriesMasterId": None,
                    "showAs": "busy",
                    "type": "seriesMaster",
                    "onlineMeetingUrl": None,
                    "isOnlineMeeting": False,
                    "onlineMeetingProvider": "unknown",
                    "allowNewTimeProposals": True,
                    "isDraft": False,
                    "hideAttendees": False,
                    "onlineMeeting": None,
                    "responseStatus": {
                        "response": "organizer",
                        "time": "0001-01-01T00:00:00Z",
                    },
                    "body": {"contentType": "html", "content": "<b>Hello world!</b>"},
                    "start": {
                        "dateTime": "2022-09-19T15:00:00.0000000",
                        "timeZone": "UTC",
                    },
                    "end": {
                        "dateTime": "2022-09-19T15:30:00.0000000",
                        "timeZone": "UTC",
                    },
                    "location": {
                        "displayName": "Parking",
                        "locationType": "default",
                        "uniqueIdType": "unknown",
                        "address": {},
                        "coordinates": {},
                    },
                    "locations": [
                        {
                            "displayName": "Parking",
                            "locationType": "default",
                            "uniqueIdType": "unknown",
                            "address": {},
                            "coordinates": {},
                        },
                    ],
                    "recurrence": {
                        "pattern": {
                            "type": "daily",
                            "interval": 1,
                            "month": 0,
                            "dayOfMonth": 0,
                            "firstDayOfWeek": "sunday",
                            "index": "first",
                        },
                        "range": {
                            "type": "endDate",
                            "startDate": "2022-09-19",
                            "endDate": "2022-09-21",
                            "recurrenceTimeZone": "Pacific Standard Time",
                            "numberOfOccurrences": 0,
                        },
                    },
                    "attendees": [
                        {
                            "type": "required",
                            "status": {
                                "response": "declined",
                                "time": "2022-09-08T15:40:17Z",
                            },
                            "emailAddress": {
                                "name": "attendee@example.com",
                                "address": "attendee@example.com",
                            },
                        }
                    ],
                    "organizer": {
                        "emailAddress": {
                            "name": "Example",
                            "address": "example@example.com",
                        }
                    },
                },
            ]
        },
    )
    responses.get(
        BASE_URL + "/me/calendars/fake_test_calendar_id/events", json={"value": []}
    )


@pytest.fixture
def subscribe_responses():
    responses.post(
        BASE_URL + "/subscriptions",
        json={
            "id": "f798ca9d-d630-4306-b065-af52199f5613",
            "resource": "/me/calendars",
            "expirationDateTime": "2022-11-24T18:31:12.829451Z",
        },
        match=[json_params_matcher({"resource": "/me/calendars"}, strict_match=False)],
    )

    responses.post(
        BASE_URL + "/subscriptions",
        json={
            "id": "f798ca9d-d630-4306-b065-af52199f5613",
            "resource": "/me/calendars/fake_calendar_id/events",
            "expirationDateTime": "2022-10-25T04:22:34.929451Z",
        },
        match=[
            json_params_matcher(
                {"resource": "/me/calendars/fake_calendar_id/events"},
                strict_match=False,
            )
        ],
    )

    responses.post(
        BASE_URL + "/subscriptions",
        json={
            "id": "f798ca9d-d630-4306-b065-af52199f5613",
            "resource": "/me/calendars/fake_calendar_id/events",
            "expirationDateTime": "2022-10-25T04:22:34.929451Z",
        },
        match=[
            json_params_matcher(
                {"resource": "/me/calendars/fake_test_calendar_id/events"},
                strict_match=False,
            )
        ],
    )


@pytest.fixture
def instances_response():
    responses.get(
        BASE_URL + "/me/events/recurrence_id/instances",
        json={
            "value": [
                {
                    "type": "occurrence",
                    "start": {
                        "dateTime": "2022-09-19T15:00:00.0000000",
                        "timeZone": "UTC",
                    },
                },
                {
                    "type": "occurrence",
                    "start": {
                        "dateTime": "2022-09-20T15:00:00.0000000",
                        "timeZone": "UTC",
                    },
                },
                {
                    "type": "occurrence",
                    "start": {
                        "dateTime": "2022-09-21T15:00:00.0000000",
                        "timeZone": "UTC",
                    },
                },
            ]
        },
    )


@pytest.fixture
def provider(client):
    provider = MicrosoftEventsProvider("fake_account_id", "fake_namespace_id")
    provider.client = client

    return provider


@responses.activate
@pytest.mark.usefixtures("calendars_response")
def test_sync_calendars(provider):
    _, calendars = provider.sync_calendars()
    calendars_by_name = {calendar.name: calendar for calendar in calendars}

    assert not calendars_by_name["Calendar"].read_only
    assert calendars_by_name["Calendar"].default
    assert not calendars_by_name["Test"].read_only
    assert not calendars_by_name["Test"].default


@responses.activate
@pytest.mark.usefixtures("events_responses", "instances_response")
def test_sync_events(provider):
    events = provider.sync_events("fake_calendar_id")
    events_by_title = {event.title: event for event in events}

    assert isinstance(events_by_title["Singular"], Event)
    assert events_by_title["Singular"].description == "Singular"
    assert isinstance(events_by_title["Recurring"], RecurringEvent)
    assert events_by_title["Recurring"].description == "Hello world!"


@responses.activate
@pytest.mark.usefixtures("subscribe_responses")
def test_watch_calendar_list(provider, outlook_account):
    expiration = provider.watch_calendar_list(outlook_account)
    assert expiration == datetime.datetime(2022, 11, 24, 18, 31, 12, tzinfo=pytz.UTC)


@responses.activate
@pytest.mark.usefixtures("subscribe_responses")
def test_watch_calendar(provider, outlook_account):
    calendar = Calendar(uid="fake_calendar_id", public_id="fake_public_id")

    expiration = provider.watch_calendar(outlook_account, calendar)
    assert expiration == datetime.datetime(2022, 10, 25, 4, 22, 34, tzinfo=pytz.UTC)


def test_webhook_notifications_enabled(provider, outlook_account):
    assert provider.webhook_notifications_enabled(outlook_account)


@responses.activate
@pytest.mark.usefixtures(
    "calendars_response",
    "events_responses",
    "subscribe_responses",
    "instances_response",
)
def test_sync(db, provider, outlook_account):
    event_sync = WebhookEventSync(
        outlook_account.email_address,
        outlook_account.verbose_provider,
        outlook_account.id,
        outlook_account.namespace.id,
        provider_class=lambda *args, **kwargs: provider,
    )

    # First sync, initially we just read without subscriptions
    event_sync.sync()

    calendars = db.session.query(Calendar).filter_by(
        namespace_id=outlook_account.namespace.id
    )
    calendars_by_name = {calendar.name: calendar for calendar in calendars}

    # Emailed events is the calendar we always create for ICS files in mail
    assert set(calendars_by_name) == {"Emailed events", "Calendar", "Test"}
    assert {event.title for event in calendars_by_name["Calendar"].events} == {
        "Singular",
        "Recurring",
    }
    assert calendars_by_name["Test"].events == []

    assert outlook_account.webhook_calendar_list_expiration is None
    assert outlook_account.webhook_calendar_list_last_ping is None
    assert calendars_by_name["Calendar"].webhook_subscription_expiration is None
    assert calendars_by_name["Calendar"].webhook_last_ping is None
    assert calendars_by_name["Test"].webhook_subscription_expiration is None
    assert calendars_by_name["Test"].webhook_last_ping is None

    db.session.expire_all()

    # Second sync, creates subscriptions
    event_sync.sync()

    assert outlook_account.webhook_calendar_list_expiration is not None
    assert outlook_account.webhook_calendar_list_last_ping is not None
    assert calendars_by_name["Calendar"].webhook_subscription_expiration is not None
    assert calendars_by_name["Calendar"].webhook_last_ping is not None
    assert calendars_by_name["Test"].webhook_subscription_expiration is not None
    assert calendars_by_name["Test"].webhook_last_ping is not None
