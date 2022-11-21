import datetime
from unittest import mock

import pytest
import pytz
import responses

from inbox.config import config
from inbox.events.microsoft.events_provider import MicrosoftEventsProvider
from inbox.events.microsoft.graph_client import BASE_URL
from inbox.events.remote_sync import EventSync
from inbox.models.calendar import Calendar
from inbox.models.event import Event, RecurringEvent


@pytest.fixture(autouse=True)
def populate_microsoft_subscrtipion_secret():
    with mock.patch.dict(config, {"MICROSOFT_SUBSCRIPTION_SECRET": "good_s3cr3t"}):
        yield


@pytest.fixture
def provider(client):
    provider = MicrosoftEventsProvider("fake_account_id", "fake_namespace_id")
    provider.client = client

    responses.get(
        BASE_URL + "/me/calendars", json=calendars_json,
    )
    responses.get(
        BASE_URL + "/me/calendars/fake_calendar_id/events", json=events_json,
    )
    responses.get(
        BASE_URL + "/me/calendars/fake_test_calendar_id/events", json={"value": []}
    )

    responses.post(BASE_URL + "/subscriptions", json=subscribe_json)

    return provider


calendars_json = {
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
}


@responses.activate
def test_sync_calendars(provider):
    _, calendars = provider.sync_calendars()
    calendars_by_name = {calendar.name: calendar for calendar in calendars}

    assert not calendars_by_name["Calendar"].read_only
    assert calendars_by_name["Calendar"].default
    assert not calendars_by_name["Test"].read_only
    assert not calendars_by_name["Test"].default


events_json = {
    "value": [
        {
            "@odata.etag": 'W/"bX+IPMmPs02YGYesdF/+dAAB/52fpA=="',
            "id": "AAMkADdiYzg5OGRlLTY1MjktNDc2Ni05YmVkLWMxMzFlNTQ0MzU3YQBGAAAAAACi9RQWB-SNTZBuALM6KIOsBwBtf4g8yY_zTZgZh6x0X-50AAIAQ6TlAABtf4g8yY_zTZgZh6x0X-50AAIARNwsAAA=",
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
            "responseStatus": {"response": "organizer", "time": "0001-01-01T00:00:00Z"},
            "body": {"contentType": "html", "content": "<i>Singular</i>"},
            "start": {"dateTime": "2022-09-15T12:00:00.0000000", "timeZone": "UTC"},
            "end": {"dateTime": "2022-09-15T12:30:00.0000000", "timeZone": "UTC"},
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
            "id": "AAMkADdiYzg5OGRlLTY1MjktNDc2Ni05YmVkLWMxMzFlNTQ0MzU3YQBGAAAAAACi9RQWB-SNTZBuALM6KIOsBwBtf4g8yY_zTZgZh6x0X-50AAIM02sjAABtf4g8yY_zTZgZh6x0X-50AAIM0_o4AAA=",
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
            "responseStatus": {"response": "organizer", "time": "0001-01-01T00:00:00Z"},
            "body": {"contentType": "html", "content": "<b>Hello world!</b>"},
            "start": {"dateTime": "2022-09-19T15:00:00.0000000", "timeZone": "UTC"},
            "end": {"dateTime": "2022-09-19T15:30:00.0000000", "timeZone": "UTC"},
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
                    "status": {"response": "declined", "time": "2022-09-08T15:40:17Z"},
                    "emailAddress": {
                        "name": "attendee@example.com",
                        "address": "attendee@example.com",
                    },
                }
            ],
            "organizer": {
                "emailAddress": {"name": "Example", "address": "example@example.com",}
            },
        },
    ]
}


@responses.activate
def test_sync_events(provider):
    events = provider.sync_events("fake_calendar_id")
    events_by_title = {event.title: event for event in events}

    assert isinstance(events_by_title["Singular"], Event)
    assert events_by_title["Singular"].description == "Singular"
    assert isinstance(events_by_title["Recurring"], RecurringEvent)
    assert events_by_title["Recurring"].description == "Hello world!"


subscribe_json = {
    "@odata.context": "https://graph.microsoft.com/v1.0/$metadata#subscriptions/$entity",
    "id": "f798ca9d-d630-4306-b065-af52199f5613",
    "resource": "/me/calendars",
    "applicationId": "de8bc8b5-d9f9-48b1-a8ad-b748da725064",
    "changeType": "updated,deleted",
    "clientState": "mxMHZhfnRRntwzKCWhPgRQFVuWymHyja",
    "notificationUrl": "https://nylas-a-googlewebhooksync.close.com/w/microsoft/calendar_update/asd",
    "notificationQueryOptions": None,
    "lifecycleNotificationUrl": None,
    "expirationDateTime": "2022-11-24T18:31:12.829451Z",
    "creatorId": "0db5de84-a1b3-47bf-8342-44ab4f415fe4",
    "includeResourceData": None,
    "latestSupportedTlsVersion": "v1_2",
    "encryptionCertificate": None,
    "encryptionCertificateId": None,
    "notificationUrlAppId": None,
}


@responses.activate
def test_watch_calendar_list(provider, outlook_account):
    expiration = provider.watch_calendar_list(outlook_account)
    assert expiration == datetime.datetime(2022, 11, 24, 18, 31, 12, tzinfo=pytz.UTC)


@responses.activate
def test_sync(db, provider, outlook_account):
    event_sync = EventSync(
        outlook_account.email_address,
        outlook_account.verbose_provider,
        outlook_account.id,
        outlook_account.namespace.id,
        provider_class=lambda *args, **kwargs: provider,
    )
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
