import datetime

import dateutil
import pytest
import pytz

from inbox.events.microsoft.parse import (
    CombineMode,
    calculate_event_exceptions_and_cancellations,
    combine_msgraph_recurrence_date_with_time,
    convert_msgraph_patterned_recurrence_to_ical_rrule,
    dump_datetime_as_msgraph_datetime_tz,
    get_microsoft_tzinfo,
    parse_msgraph_datetime_tz_as_utc,
)


@pytest.mark.parametrize(
    "windows_tz_id,olson_tz_id",
    [
        ("Eastern Standard Time", "America/New_York"),
        ("Pacific Standard Time", "America/Los_Angeles"),
        ("Europe/Warsaw", "Europe/Warsaw"),
    ],
)
def test_get_microsoft_timezone(windows_tz_id, olson_tz_id):
    assert get_microsoft_tzinfo(windows_tz_id) == pytz.timezone(olson_tz_id)


@pytest.mark.parametrize(
    "datetime_tz,dt",
    [
        (
            {"dateTime": "2022-09-08T12:00:00.0000000", "timeZone": "UTC"},
            datetime.datetime(2022, 9, 8, 12, tzinfo=pytz.UTC),
        ),
        (
            {
                "dateTime": "2022-09-22T12:30:00.0000000",
                "timeZone": "Eastern Standard Time",
            },
            datetime.datetime(2022, 9, 22, 16, 30, tzinfo=pytz.UTC),
        ),
    ],
)
def test_parse_msggraph_datetime_tz_as_utc(datetime_tz, dt):
    assert parse_msgraph_datetime_tz_as_utc(datetime_tz) == dt


def test_dump_datetime_as_msgraph_datetime_tz():
    assert dump_datetime_as_msgraph_datetime_tz(
        datetime.datetime(2022, 9, 22, 16, 31, 45, tzinfo=pytz.UTC)
    ) == {"dateTime": "2022-09-22T16:31:45.0000000", "timeZone": "UTC",}


@pytest.mark.parametrize(
    "mode,dt",
    [
        (CombineMode.START, datetime.datetime(2022, 9, 19, 4, 0, 0, tzinfo=pytz.UTC)),
        (CombineMode.END, datetime.datetime(2022, 9, 20, 3, 59, 59, tzinfo=pytz.UTC)),
    ],
)
def test_combine_msgraph_recurrence_date_with_time(mode, dt):
    assert (
        combine_msgraph_recurrence_date_with_time(
            "2022-09-19", pytz.timezone("America/New_York"), mode
        )
        == dt
    )


@pytest.mark.parametrize(
    "recurrence,rrule",
    [
        (
            {
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
                    "startDate": "2022-09-22",
                    "endDate": "2022-12-22",
                    "recurrenceTimeZone": "Eastern Standard Time",
                    "numberOfOccurrences": 0,
                },
            },
            "RRULE:FREQ=DAILY;UNTIL=20221223T045959Z",
        ),
        (
            {
                "pattern": {
                    "type": "daily",
                    "interval": 2,
                    "month": 0,
                    "dayOfMonth": 0,
                    "firstDayOfWeek": "sunday",
                    "index": "first",
                },
                "range": {
                    "type": "endDate",
                    "startDate": "2022-09-22",
                    "endDate": "2022-12-22",
                    "recurrenceTimeZone": "Eastern Standard Time",
                    "numberOfOccurrences": 0,
                },
            },
            "RRULE:FREQ=DAILY;INTERVAL=2;UNTIL=20221223T045959Z",
        ),
        (
            {
                "pattern": {
                    "type": "weekly",
                    "interval": 1,
                    "month": 0,
                    "dayOfMonth": 0,
                    "daysOfWeek": ["monday", "wednesday", "friday"],
                    "firstDayOfWeek": "sunday",
                    "index": "first",
                },
                "range": {
                    "type": "endDate",
                    "startDate": "2022-09-23",
                    "endDate": "2023-03-16",
                    "recurrenceTimeZone": "Eastern Standard Time",
                    "numberOfOccurrences": 0,
                },
            },
            "RRULE:FREQ=WEEKLY;WKST=SU;BYDAY=MO,WE,FR;UNTIL=20230317T035959Z",
        ),
        (
            {
                "pattern": {
                    "type": "weekly",
                    "interval": 3,
                    "month": 0,
                    "dayOfMonth": 0,
                    "daysOfWeek": ["tuesday", "sunday"],
                    "firstDayOfWeek": "sunday",
                    "index": "first",
                },
                "range": {
                    "type": "endDate",
                    "startDate": "2022-10-09",
                    "endDate": "2023-03-16",
                    "recurrenceTimeZone": "Eastern Standard Time",
                    "numberOfOccurrences": 0,
                },
            },
            "RRULE:FREQ=WEEKLY;INTERVAL=3;WKST=SU;BYDAY=TU,SU;UNTIL=20230317T035959Z",
        ),
        (
            {
                "pattern": {
                    "type": "absoluteMonthly",
                    "interval": 1,
                    "month": 0,
                    "dayOfMonth": 22,
                    "firstDayOfWeek": "sunday",
                    "index": "first",
                },
                "range": {
                    "type": "endDate",
                    "startDate": "2022-09-22",
                    "endDate": "2023-09-22",
                    "recurrenceTimeZone": "Eastern Standard Time",
                    "numberOfOccurrences": 0,
                },
            },
            "RRULE:FREQ=MONTHLY;UNTIL=20230923T035959Z",
        ),
        (
            {
                "pattern": {
                    "type": "absoluteMonthly",
                    "interval": 2,
                    "month": 0,
                    "dayOfMonth": 22,
                    "firstDayOfWeek": "sunday",
                    "index": "first",
                },
                "range": {
                    "type": "endDate",
                    "startDate": "2022-09-22",
                    "endDate": "2023-09-22",
                    "recurrenceTimeZone": "Eastern Standard Time",
                    "numberOfOccurrences": 0,
                },
            },
            "RRULE:FREQ=MONTHLY;INTERVAL=2;UNTIL=20230923T035959Z",
        ),
        (
            {
                "pattern": {
                    "type": "relativeMonthly",
                    "interval": 1,
                    "month": 0,
                    "dayOfMonth": 0,
                    "daysOfWeek": ["thursday"],
                    "firstDayOfWeek": "sunday",
                    "index": "fourth",
                },
                "range": {
                    "type": "endDate",
                    "startDate": "2022-09-22",
                    "endDate": "2023-09-22",
                    "recurrenceTimeZone": "Eastern Standard Time",
                    "numberOfOccurrences": 0,
                },
            },
            "RRULE:FREQ=MONTHLY;WKST=SU;BYDAY=4TH;UNTIL=20230923T035959Z",
        ),
        (
            {
                "pattern": {
                    "type": "relativeMonthly",
                    "interval": 1,
                    "month": 0,
                    "dayOfMonth": 0,
                    "daysOfWeek": ["thursday"],
                    "firstDayOfWeek": "sunday",
                    "index": "last",
                },
                "range": {
                    "type": "endDate",
                    "startDate": "2022-09-29",
                    "endDate": "2023-09-22",
                    "recurrenceTimeZone": "Eastern Standard Time",
                    "numberOfOccurrences": 0,
                },
            },
            "RRULE:FREQ=MONTHLY;WKST=SU;BYDAY=-1TH;UNTIL=20230923T035959Z",
        ),
        (
            {
                "pattern": {
                    "type": "relativeYearly",
                    "interval": 1,
                    "month": 9,
                    "dayOfMonth": 0,
                    "daysOfWeek": ["thursday"],
                    "firstDayOfWeek": "sunday",
                    "index": "last",
                },
                "range": {
                    "type": "endDate",
                    "startDate": "2022-09-29",
                    "endDate": "2023-09-22",
                    "recurrenceTimeZone": "Eastern Standard Time",
                    "numberOfOccurrences": 0,
                },
            },
            "RRULE:FREQ=MONTHLY;INTERVAL=12;WKST=SU;BYDAY=-1TH;UNTIL=20230923T035959Z",
        ),
        (
            {
                "pattern": {
                    "type": "daily",
                    "interval": 1,
                    "month": 0,
                    "dayOfMonth": 0,
                    "firstDayOfWeek": "sunday",
                    "index": "first",
                },
                "range": {
                    "type": "numbered",
                    "startDate": "2022-09-22",
                    "endDate": "0000-01-01",
                    "recurrenceTimeZone": "Eastern Standard Time",
                    "numberOfOccurrences": 10,
                },
            },
            "RRULE:FREQ=DAILY;COUNT=10",
        ),
        (
            {
                "pattern": {
                    "type": "absoluteYearly",
                    "interval": 1,
                    "month": 9,
                    "dayOfMonth": 19,
                    "firstDayOfWeek": "sunday",
                    "index": "first",
                },
                "range": {
                    "type": "noEnd",
                    "startDate": "2022-09-19",
                    "endDate": "0001-01-01",
                    "recurrenceTimeZone": "Eastern Standard Time",
                    "numberOfOccurrences": 0,
                },
            },
            "RRULE:FREQ=YEARLY",
        ),
    ],
)
def test_convert_msgraph_patterned_recurrence_to_ical_rrule(recurrence, rrule):
    assert convert_msgraph_patterned_recurrence_to_ical_rrule(recurrence) == rrule


@pytest.mark.parametrize(
    "recurrence,inflated_dates",
    [
        (
            {
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
                    "endDate": "2022-09-23",
                    "recurrenceTimeZone": "Eastern Standard Time",
                    "numberOfOccurrences": 0,
                },
            },
            [
                datetime.date(2022, 9, 19),
                datetime.date(2022, 9, 20),
                datetime.date(2022, 9, 21),
                datetime.date(2022, 9, 22),
                datetime.date(2022, 9, 23),
            ],
        ),
        (
            {
                "pattern": {
                    "type": "daily",
                    "interval": 2,
                    "month": 0,
                    "dayOfMonth": 0,
                    "firstDayOfWeek": "sunday",
                    "index": "first",
                },
                "range": {
                    "type": "endDate",
                    "startDate": "2022-09-19",
                    "endDate": "2022-09-23",
                    "recurrenceTimeZone": "Eastern Standard Time",
                    "numberOfOccurrences": 0,
                },
            },
            [
                datetime.date(2022, 9, 19),
                datetime.date(2022, 9, 21),
                datetime.date(2022, 9, 23),
            ],
        ),
        (
            {
                "pattern": {
                    "type": "weekly",
                    "interval": 1,
                    "month": 0,
                    "dayOfMonth": 0,
                    "daysOfWeek": ["monday", "saturday"],
                    "firstDayOfWeek": "sunday",
                    "index": "first",
                },
                "range": {
                    "type": "endDate",
                    "startDate": "2022-09-19",
                    "endDate": "2022-10-03",
                    "recurrenceTimeZone": "Eastern Standard Time",
                    "numberOfOccurrences": 0,
                },
            },
            [
                datetime.date(2022, 9, 19),
                datetime.date(2022, 9, 24),
                datetime.date(2022, 9, 26),
                datetime.date(2022, 10, 1),
                datetime.date(2022, 10, 3),
            ],
        ),
        (
            {
                "pattern": {
                    "type": "weekly",
                    "interval": 2,
                    "month": 0,
                    "dayOfMonth": 0,
                    "daysOfWeek": ["monday", "tuesday", "thursday"],
                    "firstDayOfWeek": "sunday",
                    "index": "first",
                },
                "range": {
                    "type": "endDate",
                    "startDate": "2022-09-19",
                    "endDate": "2022-10-10",
                    "recurrenceTimeZone": "UTC",
                    "numberOfOccurrences": 0,
                },
            },
            [
                datetime.date(2022, 9, 19),
                datetime.date(2022, 9, 20),
                datetime.date(2022, 9, 22),
                datetime.date(2022, 10, 3),
                datetime.date(2022, 10, 4),
                datetime.date(2022, 10, 6),
            ],
        ),
        (
            {
                "pattern": {
                    "type": "absoluteMonthly",
                    "interval": 2,
                    "month": 0,
                    "dayOfMonth": 19,
                    "firstDayOfWeek": "sunday",
                    "index": "first",
                },
                "range": {
                    "type": "endDate",
                    "startDate": "2022-09-19",
                    "endDate": "2023-02-01",
                    "recurrenceTimeZone": "Pacific Standard Time",
                    "numberOfOccurrences": 0,
                },
            },
            [
                datetime.date(2022, 9, 19),
                datetime.date(2022, 11, 19),
                datetime.date(2023, 1, 19),
            ],
        ),
        (
            {
                "pattern": {
                    "type": "relativeMonthly",
                    "interval": 2,
                    "month": 0,
                    "dayOfMonth": 0,
                    "daysOfWeek": ["monday"],
                    "firstDayOfWeek": "sunday",
                    "index": "third",
                },
                "range": {
                    "type": "endDate",
                    "startDate": "2022-09-19",
                    "endDate": "2023-02-01",
                    "recurrenceTimeZone": "Pacific Standard Time",
                    "numberOfOccurrences": 0,
                },
            },
            [
                datetime.date(2022, 9, 19),
                datetime.date(2022, 11, 21),
                datetime.date(2023, 1, 16),
            ],
        ),
        (
            {
                "pattern": {
                    "type": "relativeYearly",
                    "interval": 1,
                    "month": 9,
                    "dayOfMonth": 0,
                    "daysOfWeek": ["monday"],
                    "firstDayOfWeek": "sunday",
                    "index": "third",
                },
                "range": {
                    "type": "endDate",
                    "startDate": "2022-09-19",
                    "endDate": "2023-12-01",
                    "recurrenceTimeZone": "Pacific Standard Time",
                    "numberOfOccurrences": 0,
                },
            },
            [datetime.date(2022, 9, 19), datetime.date(2023, 9, 18)],
        ),
        (
            {
                "pattern": {
                    "type": "daily",
                    "interval": 1,
                    "month": 0,
                    "dayOfMonth": 0,
                    "firstDayOfWeek": "sunday",
                    "index": "first",
                },
                "range": {
                    "type": "numbered",
                    "startDate": "2022-09-19",
                    "endDate": "0001-01-01",
                    "recurrenceTimeZone": "Eastern Standard Time",
                    "numberOfOccurrences": 3,
                },
            },
            [
                datetime.date(2022, 9, 19),
                datetime.date(2022, 9, 20),
                datetime.date(2022, 9, 21),
            ],
        ),
        (
            {
                "pattern": {
                    "type": "absoluteYearly",
                    "interval": 1,
                    "month": 9,
                    "dayOfMonth": 19,
                    "firstDayOfWeek": "sunday",
                    "index": "first",
                },
                "range": {
                    "type": "noEnd",
                    "startDate": "2022-09-19",
                    "endDate": "0001-01-01",
                    "recurrenceTimeZone": "Eastern Standard Time",
                    "numberOfOccurrences": 0,
                },
            },
            [
                datetime.date(2022, 9, 19),
                datetime.date(2023, 9, 19),
                datetime.date(2024, 9, 19),
            ],
        ),
    ],
)
def test_inflate_msgraph_patterned_recurrence(recurrence, inflated_dates):
    rrule = convert_msgraph_patterned_recurrence_to_ical_rrule(recurrence)
    start_datetime = datetime.datetime(2022, 9, 19, 12, tzinfo=pytz.UTC)
    parsed_rrule = dateutil.rrule.rrulestr(rrule, dtstart=start_datetime)
    # For infinite recurrences expand only first 3
    if not parsed_rrule._count and not parsed_rrule._until:
        parsed_rrule = parsed_rrule.replace(count=3)
    assert [dt.date() for dt in parsed_rrule] == inflated_dates


master_event = {
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
    "subject": "Expansion",
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
    "webLink": "https://outlook.office365.com/owa/?itemid=AAMkADdiYzg5OGRlLTY1MjktNDc2Ni05YmVkLWMxMzFlNTQ0MzU3YQBGAAAAAACi9RQWB%2FSNTZBuALM6KIOsBwBtf4g8yY%2BzTZgZh6x0X%2F50AAIM02sjAABtf4g8yY%2BzTZgZh6x0X%2F50AAIM0%2Bo4AAA%3D&exvsurl=1&path=/calendar/item",
    "onlineMeetingUrl": None,
    "isOnlineMeeting": False,
    "onlineMeetingProvider": "unknown",
    "allowNewTimeProposals": True,
    "isDraft": False,
    "hideAttendees": False,
    "onlineMeeting": None,
    "responseStatus": {"response": "organizer", "time": "0001-01-01T00:00:00Z"},
    "body": {"contentType": "html", "content": ""},
    "start": {"dateTime": "2022-09-19T15:00:00.0000000", "timeZone": "UTC"},
    "end": {"dateTime": "2022-09-19T15:30:00.0000000", "timeZone": "UTC"},
    "location": {
        "displayName": "",
        "locationType": "default",
        "uniqueIdType": "unknown",
        "address": {},
        "coordinates": {},
    },
    "locations": [],
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
    "attendees": [],
    "organizer": {
        "emailAddress": {
            "name": "Close Testing",
            "address": "closetesting@closetesting.onmicrosoft.com",
        }
    },
}

master_instances = [
    {
        "@odata.etag": 'W/"bX+IPMmPs02YGYesdF/+dAACDYEM6g=="',
        "id": "AAMkADdiYzg5OGRlLTY1MjktNDc2Ni05YmVkLWMxMzFlNTQ0MzU3YQFRAAgI2pnR5UQAAEYAAAAAovUUFgf0jU2QbgCzOiiDrAcAbX_IPMmPs02YGYesdF-_dAACDNNrIwAAbX_IPMmPs02YGYesdF-_dAACDNPqOAAAEA==",
        "createdDateTime": "2022-09-24T15:32:22.239054Z",
        "lastModifiedDateTime": "2022-09-27T14:41:23.1042764Z",
        "changeKey": "bX+IPMmPs02YGYesdF/+dAACDYEM6g==",
        "categories": [],
        "transactionId": "68faba75-324e-1e37-018d-b239fe0d3c8b",
        "originalStartTimeZone": "Pacific Standard Time",
        "originalEndTimeZone": "Pacific Standard Time",
        "iCalUId": "040000008200E00074C5B7101A82E00807E60913F8620CD72AD0D801000000000000000010000000EB99E61264138D46A203CC0931BB688A",
        "reminderMinutesBeforeStart": 15,
        "isReminderOn": True,
        "hasAttachments": False,
        "subject": "Expansion",
        "bodyPreview": "",
        "importance": "normal",
        "sensitivity": "normal",
        "isAllDay": False,
        "isCancelled": False,
        "isOrganizer": True,
        "responseRequested": True,
        "seriesMasterId": "AAMkADdiYzg5OGRlLTY1MjktNDc2Ni05YmVkLWMxMzFlNTQ0MzU3YQBGAAAAAACi9RQWB-SNTZBuALM6KIOsBwBtf4g8yY_zTZgZh6x0X-50AAIM02sjAABtf4g8yY_zTZgZh6x0X-50AAIM0_o4AAA=",
        "showAs": "busy",
        "type": "occurrence",
        "webLink": "https://outlook.office365.com/owa/?itemid=AAMkADdiYzg5OGRlLTY1MjktNDc2Ni05YmVkLWMxMzFlNTQ0MzU3YQFRAAgI2pnR5UQAAEYAAAAAovUUFgf0jU2QbgCzOiiDrAcAbX%2BIPMmPs02YGYesdF%2F%2BdAACDNNrIwAAbX%2BIPMmPs02YGYesdF%2F%2BdAACDNPqOAAAEA%3D%3D&exvsurl=1&path=/calendar/item",
        "onlineMeetingUrl": None,
        "isOnlineMeeting": False,
        "onlineMeetingProvider": "unknown",
        "allowNewTimeProposals": True,
        "isDraft": False,
        "hideAttendees": False,
        "recurrence": None,
        "onlineMeeting": None,
        "responseStatus": {"response": "organizer", "time": "0001-01-01T00:00:00Z"},
        "body": {"contentType": "html", "content": ""},
        "start": {"dateTime": "2022-09-19T15:00:00.0000000", "timeZone": "UTC"},
        "end": {"dateTime": "2022-09-19T15:30:00.0000000", "timeZone": "UTC"},
        "location": {
            "displayName": "",
            "locationType": "default",
            "uniqueIdType": "unknown",
            "address": {},
            "coordinates": {},
        },
        "locations": [],
        "attendees": [],
        "organizer": {
            "emailAddress": {
                "name": "Close Testing",
                "address": "closetesting@closetesting.onmicrosoft.com",
            }
        },
    },
    {
        "@odata.etag": 'W/"bX+IPMmPs02YGYesdF/+dAACDYEM6g=="',
        "id": "AAMkADdiYzg5OGRlLTY1MjktNDc2Ni05YmVkLWMxMzFlNTQ0MzU3YQFRAAgI2pqbD63AAEYAAAAAovUUFgf0jU2QbgCzOiiDrAcAbX_IPMmPs02YGYesdF-_dAACDNNrIwAAbX_IPMmPs02YGYesdF-_dAACDNPqOAAAEA==",
        "createdDateTime": "2022-09-24T15:32:22.239054Z",
        "lastModifiedDateTime": "2022-09-27T14:41:23.1042764Z",
        "changeKey": "bX+IPMmPs02YGYesdF/+dAACDYEM6g==",
        "categories": [],
        "transactionId": "68faba75-324e-1e37-018d-b239fe0d3c8b",
        "originalStartTimeZone": "Pacific Standard Time",
        "originalEndTimeZone": "Pacific Standard Time",
        "iCalUId": "040000008200E00074C5B7101A82E00807E60914F8620CD72AD0D801000000000000000010000000EB99E61264138D46A203CC0931BB688A",
        "reminderMinutesBeforeStart": 15,
        "isReminderOn": True,
        "hasAttachments": False,
        "subject": "Expansion",
        "bodyPreview": "",
        "importance": "normal",
        "sensitivity": "normal",
        "isAllDay": False,
        "isCancelled": False,
        "isOrganizer": True,
        "responseRequested": True,
        "seriesMasterId": "AAMkADdiYzg5OGRlLTY1MjktNDc2Ni05YmVkLWMxMzFlNTQ0MzU3YQBGAAAAAACi9RQWB-SNTZBuALM6KIOsBwBtf4g8yY_zTZgZh6x0X-50AAIM02sjAABtf4g8yY_zTZgZh6x0X-50AAIM0_o4AAA=",
        "showAs": "busy",
        "type": "occurrence",
        "webLink": "https://outlook.office365.com/owa/?itemid=AAMkADdiYzg5OGRlLTY1MjktNDc2Ni05YmVkLWMxMzFlNTQ0MzU3YQFRAAgI2pqbD63AAEYAAAAAovUUFgf0jU2QbgCzOiiDrAcAbX%2BIPMmPs02YGYesdF%2F%2BdAACDNNrIwAAbX%2BIPMmPs02YGYesdF%2F%2BdAACDNPqOAAAEA%3D%3D&exvsurl=1&path=/calendar/item",
        "onlineMeetingUrl": None,
        "isOnlineMeeting": False,
        "onlineMeetingProvider": "unknown",
        "allowNewTimeProposals": True,
        "isDraft": False,
        "hideAttendees": False,
        "recurrence": None,
        "onlineMeeting": None,
        "responseStatus": {"response": "organizer", "time": "0001-01-01T00:00:00Z"},
        "body": {"contentType": "html", "content": ""},
        "start": {"dateTime": "2022-09-20T15:00:00.0000000", "timeZone": "UTC"},
        "end": {"dateTime": "2022-09-20T15:30:00.0000000", "timeZone": "UTC"},
        "location": {
            "displayName": "",
            "locationType": "default",
            "uniqueIdType": "unknown",
            "address": {},
            "coordinates": {},
        },
        "locations": [],
        "attendees": [],
        "organizer": {
            "emailAddress": {
                "name": "Close Testing",
                "address": "closetesting@closetesting.onmicrosoft.com",
            }
        },
    },
    {
        "@odata.etag": 'W/"bX+IPMmPs02YGYesdF/+dAACDYEM6g=="',
        "id": "AAMkADdiYzg5OGRlLTY1MjktNDc2Ni05YmVkLWMxMzFlNTQ0MzU3YQFRAAgI2ptkOheAAEYAAAAAovUUFgf0jU2QbgCzOiiDrAcAbX_IPMmPs02YGYesdF-_dAACDNNrIwAAbX_IPMmPs02YGYesdF-_dAACDNPqOAAAEA==",
        "createdDateTime": "2022-09-24T15:32:22.239054Z",
        "lastModifiedDateTime": "2022-09-27T14:41:23.1042764Z",
        "changeKey": "bX+IPMmPs02YGYesdF/+dAACDYEM6g==",
        "categories": [],
        "transactionId": "68faba75-324e-1e37-018d-b239fe0d3c8b",
        "originalStartTimeZone": "Pacific Standard Time",
        "originalEndTimeZone": "Pacific Standard Time",
        "iCalUId": "040000008200E00074C5B7101A82E00807E60915F8620CD72AD0D801000000000000000010000000EB99E61264138D46A203CC0931BB688A",
        "reminderMinutesBeforeStart": 15,
        "isReminderOn": True,
        "hasAttachments": False,
        "subject": "Expansion",
        "bodyPreview": "",
        "importance": "normal",
        "sensitivity": "normal",
        "isAllDay": False,
        "isCancelled": False,
        "isOrganizer": True,
        "responseRequested": True,
        "seriesMasterId": "AAMkADdiYzg5OGRlLTY1MjktNDc2Ni05YmVkLWMxMzFlNTQ0MzU3YQBGAAAAAACi9RQWB-SNTZBuALM6KIOsBwBtf4g8yY_zTZgZh6x0X-50AAIM02sjAABtf4g8yY_zTZgZh6x0X-50AAIM0_o4AAA=",
        "showAs": "busy",
        "type": "occurrence",
        "webLink": "https://outlook.office365.com/owa/?itemid=AAMkADdiYzg5OGRlLTY1MjktNDc2Ni05YmVkLWMxMzFlNTQ0MzU3YQFRAAgI2ptkOheAAEYAAAAAovUUFgf0jU2QbgCzOiiDrAcAbX%2BIPMmPs02YGYesdF%2F%2BdAACDNNrIwAAbX%2BIPMmPs02YGYesdF%2F%2BdAACDNPqOAAAEA%3D%3D&exvsurl=1&path=/calendar/item",
        "onlineMeetingUrl": None,
        "isOnlineMeeting": False,
        "onlineMeetingProvider": "unknown",
        "allowNewTimeProposals": True,
        "isDraft": False,
        "hideAttendees": False,
        "recurrence": None,
        "onlineMeeting": None,
        "responseStatus": {"response": "organizer", "time": "0001-01-01T00:00:00Z"},
        "body": {"contentType": "html", "content": ""},
        "start": {"dateTime": "2022-09-21T15:00:00.0000000", "timeZone": "UTC"},
        "end": {"dateTime": "2022-09-21T15:30:00.0000000", "timeZone": "UTC"},
        "location": {
            "displayName": "",
            "locationType": "default",
            "uniqueIdType": "unknown",
            "address": {},
            "coordinates": {},
        },
        "locations": [],
        "attendees": [],
        "organizer": {
            "emailAddress": {
                "name": "Close Testing",
                "address": "closetesting@closetesting.onmicrosoft.com",
            }
        },
    },
]


def test_calculate_event_exceptions_and_cancellations_without_changes():
    assert calculate_event_exceptions_and_cancellations(
        master_event, master_instances
    ) == ([], [])


def test_calculate_event_exceptions_and_cancellations_with_deletion():
    ((), (cancellation,)) = calculate_event_exceptions_and_cancellations(
        master_event, [master_instances[0], master_instances[2]]
    )

    assert cancellation["type"] == "synthetizedCancellation"
    assert cancellation["isCancelled"] is True
    assert cancellation["start"] == master_instances[1]["start"]
    assert cancellation["end"] == master_instances[1]["end"]


master_with_exception = {
    "@odata.context": "https://graph.microsoft.com/v1.0/$metadata#users('0db5de84-a1b3-47bf-8342-44ab4f415fe4')/calendars('AAMkADdiYzg5OGRlLTY1MjktNDc2Ni05YmVkLWMxMzFlNTQ0MzU3YQBGAAAAAACi9RQWB-SNTZBuALM6KIOsBwBtf4g8yY_zTZgZh6x0X-50AAAAAAEGAABtf4g8yY_zTZgZh6x0X-50AAIM0_ZOAAA%3D')/events/$entity",
    "@odata.etag": 'W/"bX+IPMmPs02YGYesdF/+dAACD11wnA=="',
    "id": "AAMkADdiYzg5OGRlLTY1MjktNDc2Ni05YmVkLWMxMzFlNTQ0MzU3YQBGAAAAAACi9RQWB-SNTZBuALM6KIOsBwBtf4g8yY_zTZgZh6x0X-50AAIM02sjAABtf4g8yY_zTZgZh6x0X-50AAIQCYpPAAA=",
    "createdDateTime": "2022-09-28T09:14:29.9586734Z",
    "lastModifiedDateTime": "2022-09-28T09:17:09.8392061Z",
    "changeKey": "bX+IPMmPs02YGYesdF/+dAACD11wnA==",
    "categories": [],
    "transactionId": "fddd3bf6-35dd-5fe5-306d-d2be69d30186",
    "originalStartTimeZone": "Eastern Standard Time",
    "originalEndTimeZone": "Eastern Standard Time",
    "iCalUId": "040000008200E00074C5B7101A82E00800000000E2F2F7B61AD3D801000000000000000010000000B5F5B5854466DD429698723BB231B9EA",
    "reminderMinutesBeforeStart": 15,
    "isReminderOn": True,
    "hasAttachments": False,
    "subject": "Expansion",
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
    "webLink": "https://outlook.office365.com/owa/?itemid=AAMkADdiYzg5OGRlLTY1MjktNDc2Ni05YmVkLWMxMzFlNTQ0MzU3YQBGAAAAAACi9RQWB%2FSNTZBuALM6KIOsBwBtf4g8yY%2BzTZgZh6x0X%2F50AAIM02sjAABtf4g8yY%2BzTZgZh6x0X%2F50AAIQCYpPAAA%3D&exvsurl=1&path=/calendar/item",
    "onlineMeetingUrl": None,
    "isOnlineMeeting": False,
    "onlineMeetingProvider": "unknown",
    "allowNewTimeProposals": True,
    "isDraft": False,
    "hideAttendees": False,
    "onlineMeeting": None,
    "responseStatus": {"response": "organizer", "time": "0001-01-01T00:00:00Z"},
    "body": {"contentType": "html", "content": ""},
    "start": {"dateTime": "2022-09-26T12:00:00.0000000", "timeZone": "UTC"},
    "end": {"dateTime": "2022-09-26T12:30:00.0000000", "timeZone": "UTC"},
    "location": {
        "displayName": "",
        "locationType": "default",
        "uniqueIdType": "unknown",
        "address": {},
        "coordinates": {},
    },
    "locations": [],
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
            "startDate": "2022-09-26",
            "endDate": "2022-09-28",
            "recurrenceTimeZone": "Eastern Standard Time",
            "numberOfOccurrences": 0,
        },
    },
    "attendees": [],
    "organizer": {
        "emailAddress": {
            "name": "Close Testing",
            "address": "closetesting@closetesting.onmicrosoft.com",
        }
    },
}

master_with_exception_instances = [
    {
        "@odata.etag": 'W/"bX+IPMmPs02YGYesdF/+dAACD11wnA=="',
        "id": "AAMkADdiYzg5OGRlLTY1MjktNDc2Ni05YmVkLWMxMzFlNTQ0MzU3YQFRAAgI2qAbOJIAAEYAAAAAovUUFgf0jU2QbgCzOiiDrAcAbX_IPMmPs02YGYesdF-_dAACDNNrIwAAbX_IPMmPs02YGYesdF-_dAACEAmKTwAAEA==",
        "createdDateTime": "2022-09-28T09:14:58.3087319Z",
        "lastModifiedDateTime": "2022-09-28T09:14:58.449338Z",
        "changeKey": "bX+IPMmPs02YGYesdF/+dAACD11wnA==",
        "categories": [],
        "transactionId": "fddd3bf6-35dd-5fe5-306d-d2be69d30186",
        "originalStartTimeZone": "Eastern Standard Time",
        "originalEndTimeZone": "Eastern Standard Time",
        "iCalUId": "040000008200E00074C5B7101A82E00807E6091BE2F2F7B61AD3D801000000000000000010000000B5F5B5854466DD429698723BB231B9EA",
        "reminderMinutesBeforeStart": 15,
        "isReminderOn": True,
        "hasAttachments": False,
        "subject": "Expansion",
        "bodyPreview": "",
        "importance": "normal",
        "sensitivity": "normal",
        "isAllDay": False,
        "isCancelled": False,
        "isOrganizer": True,
        "responseRequested": True,
        "seriesMasterId": "AAMkADdiYzg5OGRlLTY1MjktNDc2Ni05YmVkLWMxMzFlNTQ0MzU3YQBGAAAAAACi9RQWB-SNTZBuALM6KIOsBwBtf4g8yY_zTZgZh6x0X-50AAIM02sjAABtf4g8yY_zTZgZh6x0X-50AAIQCYpPAAA=",
        "showAs": "busy",
        "type": "exception",
        "webLink": "https://outlook.office365.com/owa/?itemid=AAMkADdiYzg5OGRlLTY1MjktNDc2Ni05YmVkLWMxMzFlNTQ0MzU3YQFRAAgI2qAbOJIAAEYAAAAAovUUFgf0jU2QbgCzOiiDrAcAbX%2BIPMmPs02YGYesdF%2F%2BdAACDNNrIwAAbX%2BIPMmPs02YGYesdF%2F%2BdAACEAmKTwAAEA%3D%3D&exvsurl=1&path=/calendar/item",
        "onlineMeetingUrl": None,
        "isOnlineMeeting": False,
        "onlineMeetingProvider": "unknown",
        "allowNewTimeProposals": True,
        "isDraft": False,
        "hideAttendees": False,
        "recurrence": None,
        "onlineMeeting": None,
        "responseStatus": {"response": "organizer", "time": "0001-01-01T00:00:00Z"},
        "body": {"contentType": "html", "content": ""},
        "start": {"dateTime": "2022-09-27T13:30:00.0000000", "timeZone": "UTC"},
        "end": {"dateTime": "2022-09-27T14:00:00.0000000", "timeZone": "UTC"},
        "location": {
            "displayName": "",
            "locationType": "default",
            "uniqueIdType": "unknown",
            "address": {},
            "coordinates": {},
        },
        "locations": [],
        "attendees": [],
        "organizer": {
            "emailAddress": {
                "name": "Close Testing",
                "address": "closetesting@closetesting.onmicrosoft.com",
            }
        },
    },
    {
        "@odata.etag": 'W/"bX+IPMmPs02YGYesdF/+dAACD11wnA=="',
        "id": "AAMkADdiYzg5OGRlLTY1MjktNDc2Ni05YmVkLWMxMzFlNTQ0MzU3YQFRAAgI2p9SDihAAEYAAAAAovUUFgf0jU2QbgCzOiiDrAcAbX_IPMmPs02YGYesdF-_dAACDNNrIwAAbX_IPMmPs02YGYesdF-_dAACEAmKTwAAEA==",
        "createdDateTime": "2022-09-28T09:14:29.9586734Z",
        "lastModifiedDateTime": "2022-09-28T09:17:09.8392061Z",
        "changeKey": "bX+IPMmPs02YGYesdF/+dAACD11wnA==",
        "categories": [],
        "transactionId": "fddd3bf6-35dd-5fe5-306d-d2be69d30186",
        "originalStartTimeZone": "Eastern Standard Time",
        "originalEndTimeZone": "Eastern Standard Time",
        "iCalUId": "040000008200E00074C5B7101A82E00807E6091AE2F2F7B61AD3D801000000000000000010000000B5F5B5854466DD429698723BB231B9EA",
        "reminderMinutesBeforeStart": 15,
        "isReminderOn": True,
        "hasAttachments": False,
        "subject": "Expansion",
        "bodyPreview": "",
        "importance": "normal",
        "sensitivity": "normal",
        "isAllDay": False,
        "isCancelled": False,
        "isOrganizer": True,
        "responseRequested": True,
        "seriesMasterId": "AAMkADdiYzg5OGRlLTY1MjktNDc2Ni05YmVkLWMxMzFlNTQ0MzU3YQBGAAAAAACi9RQWB-SNTZBuALM6KIOsBwBtf4g8yY_zTZgZh6x0X-50AAIM02sjAABtf4g8yY_zTZgZh6x0X-50AAIQCYpPAAA=",
        "showAs": "busy",
        "type": "occurrence",
        "webLink": "https://outlook.office365.com/owa/?itemid=AAMkADdiYzg5OGRlLTY1MjktNDc2Ni05YmVkLWMxMzFlNTQ0MzU3YQFRAAgI2p9SDihAAEYAAAAAovUUFgf0jU2QbgCzOiiDrAcAbX%2BIPMmPs02YGYesdF%2F%2BdAACDNNrIwAAbX%2BIPMmPs02YGYesdF%2F%2BdAACEAmKTwAAEA%3D%3D&exvsurl=1&path=/calendar/item",
        "onlineMeetingUrl": None,
        "isOnlineMeeting": False,
        "onlineMeetingProvider": "unknown",
        "allowNewTimeProposals": True,
        "isDraft": False,
        "hideAttendees": False,
        "recurrence": None,
        "onlineMeeting": None,
        "responseStatus": {"response": "organizer", "time": "0001-01-01T00:00:00Z"},
        "body": {"contentType": "html", "content": ""},
        "start": {"dateTime": "2022-09-26T12:00:00.0000000", "timeZone": "UTC"},
        "end": {"dateTime": "2022-09-26T12:30:00.0000000", "timeZone": "UTC"},
        "location": {
            "displayName": "",
            "locationType": "default",
            "uniqueIdType": "unknown",
            "address": {},
            "coordinates": {},
        },
        "locations": [],
        "attendees": [],
        "organizer": {
            "emailAddress": {
                "name": "Close Testing",
                "address": "closetesting@closetesting.onmicrosoft.com",
            }
        },
    },
    {
        "@odata.etag": 'W/"bX+IPMmPs02YGYesdF/+dAACD11wnA=="',
        "id": "AAMkADdiYzg5OGRlLTY1MjktNDc2Ni05YmVkLWMxMzFlNTQ0MzU3YQFRAAgI2qDkYvvAAEYAAAAAovUUFgf0jU2QbgCzOiiDrAcAbX_IPMmPs02YGYesdF-_dAACDNNrIwAAbX_IPMmPs02YGYesdF-_dAACEAmKTwAAEA==",
        "createdDateTime": "2022-09-28T09:14:29.9586734Z",
        "lastModifiedDateTime": "2022-09-28T09:17:09.8392061Z",
        "changeKey": "bX+IPMmPs02YGYesdF/+dAACD11wnA==",
        "categories": [],
        "transactionId": "fddd3bf6-35dd-5fe5-306d-d2be69d30186",
        "originalStartTimeZone": "Eastern Standard Time",
        "originalEndTimeZone": "Eastern Standard Time",
        "iCalUId": "040000008200E00074C5B7101A82E00807E6091CE2F2F7B61AD3D801000000000000000010000000B5F5B5854466DD429698723BB231B9EA",
        "reminderMinutesBeforeStart": 15,
        "isReminderOn": True,
        "hasAttachments": False,
        "subject": "Expansion",
        "bodyPreview": "",
        "importance": "normal",
        "sensitivity": "normal",
        "isAllDay": False,
        "isCancelled": False,
        "isOrganizer": True,
        "responseRequested": True,
        "seriesMasterId": "AAMkADdiYzg5OGRlLTY1MjktNDc2Ni05YmVkLWMxMzFlNTQ0MzU3YQBGAAAAAACi9RQWB-SNTZBuALM6KIOsBwBtf4g8yY_zTZgZh6x0X-50AAIM02sjAABtf4g8yY_zTZgZh6x0X-50AAIQCYpPAAA=",
        "showAs": "busy",
        "type": "occurrence",
        "webLink": "https://outlook.office365.com/owa/?itemid=AAMkADdiYzg5OGRlLTY1MjktNDc2Ni05YmVkLWMxMzFlNTQ0MzU3YQFRAAgI2qDkYvvAAEYAAAAAovUUFgf0jU2QbgCzOiiDrAcAbX%2BIPMmPs02YGYesdF%2F%2BdAACDNNrIwAAbX%2BIPMmPs02YGYesdF%2F%2BdAACEAmKTwAAEA%3D%3D&exvsurl=1&path=/calendar/item",
        "onlineMeetingUrl": None,
        "isOnlineMeeting": False,
        "onlineMeetingProvider": "unknown",
        "allowNewTimeProposals": True,
        "isDraft": False,
        "hideAttendees": False,
        "recurrence": None,
        "onlineMeeting": None,
        "responseStatus": {"response": "organizer", "time": "0001-01-01T00:00:00Z"},
        "body": {"contentType": "html", "content": ""},
        "start": {"dateTime": "2022-09-28T12:00:00.0000000", "timeZone": "UTC"},
        "end": {"dateTime": "2022-09-28T12:30:00.0000000", "timeZone": "UTC"},
        "location": {
            "displayName": "",
            "locationType": "default",
            "uniqueIdType": "unknown",
            "address": {},
            "coordinates": {},
        },
        "locations": [],
        "attendees": [],
        "organizer": {
            "emailAddress": {
                "name": "Close Testing",
                "address": "closetesting@closetesting.onmicrosoft.com",
            }
        },
    },
]


def test_calculate_event_exceptions_and_cancellations_with_exception():
    ((exception,), ()) = calculate_event_exceptions_and_cancellations(
        master_with_exception, master_with_exception_instances
    )
    assert master_with_exception_instances[0] == exception
    assert exception["start"] == {
        "dateTime": "2022-09-27T13:30:00.0000000",
        "timeZone": "UTC",
    }
    assert exception["originalStart"] == {
        "dateTime": "2022-09-27T12:00:00.0000000",
        "timeZone": "UTC",
    }
