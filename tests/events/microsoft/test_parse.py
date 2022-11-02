import datetime

import dateutil
import pytest
import pytz

from inbox.events.microsoft.parse import (
    CombineMode,
    calculate_exception_and_canceled_occurrences,
    combine_msgraph_recurrence_date_with_time,
    convert_msgraph_patterned_recurrence_to_ical_rrule,
    dump_datetime_as_msgraph_datetime_tz,
    get_event_description,
    get_event_location,
    get_event_participant,
    get_microsoft_tzinfo,
    parse_calendar,
    parse_event,
    parse_msgraph_datetime_tz_as_utc,
)
from inbox.models.event import Event, RecurringEvent


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
    "id": "AAMkADdiYzg5OGRlLTY1MjktNDc2Ni05YmVkLWMxMzFlNTQ0MzU3YQBGAAAAAACi9RQWB-SNTZBuALM6KIOsBwBtf4g8yY_zTZgZh6x0X-50AAIM02sjAABtf4g8yY_zTZgZh6x0X-50AAIM0_o4AAA=",
    "subject": "Expansion",
    "importance": "normal",
    "sensitivity": "normal",
    "isAllDay": False,
    "isCancelled": False,
    "isOrganizer": True,
    "seriesMasterId": None,
    "showAs": "busy",
    "type": "seriesMaster",
    "body": {"contentType": "html", "content": ""},
    "start": {"dateTime": "2022-09-19T15:00:00.0000000", "timeZone": "UTC"},
    "end": {"dateTime": "2022-09-19T15:30:00.0000000", "timeZone": "UTC"},
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
}

event_occurrences = [
    {
        "id": "AAMkADdiYzg5OGRlLTY1MjktNDc2Ni05YmVkLWMxMzFlNTQ0MzU3YQFRAAgI2pnR5UQAAEYAAAAAovUUFgf0jU2QbgCzOiiDrAcAbX_IPMmPs02YGYesdF-_dAACDNNrIwAAbX_IPMmPs02YGYesdF-_dAACDNPqOAAAEA==",
        "subject": "Expansion",
        "importance": "normal",
        "sensitivity": "normal",
        "isAllDay": False,
        "isCancelled": False,
        "isOrganizer": True,
        "seriesMasterId": "AAMkADdiYzg5OGRlLTY1MjktNDc2Ni05YmVkLWMxMzFlNTQ0MzU3YQBGAAAAAACi9RQWB-SNTZBuALM6KIOsBwBtf4g8yY_zTZgZh6x0X-50AAIM02sjAABtf4g8yY_zTZgZh6x0X-50AAIM0_o4AAA=",
        "showAs": "busy",
        "type": "occurrence",
        "recurrence": None,
        "body": {"contentType": "html", "content": ""},
        "start": {"dateTime": "2022-09-19T15:00:00.0000000", "timeZone": "UTC"},
        "end": {"dateTime": "2022-09-19T15:30:00.0000000", "timeZone": "UTC"},
    },
    {
        "id": "AAMkADdiYzg5OGRlLTY1MjktNDc2Ni05YmVkLWMxMzFlNTQ0MzU3YQFRAAgI2pqbD63AAEYAAAAAovUUFgf0jU2QbgCzOiiDrAcAbX_IPMmPs02YGYesdF-_dAACDNNrIwAAbX_IPMmPs02YGYesdF-_dAACDNPqOAAAEA==",
        "subject": "Expansion",
        "importance": "normal",
        "sensitivity": "normal",
        "isAllDay": False,
        "isCancelled": False,
        "isOrganizer": True,
        "seriesMasterId": "AAMkADdiYzg5OGRlLTY1MjktNDc2Ni05YmVkLWMxMzFlNTQ0MzU3YQBGAAAAAACi9RQWB-SNTZBuALM6KIOsBwBtf4g8yY_zTZgZh6x0X-50AAIM02sjAABtf4g8yY_zTZgZh6x0X-50AAIM0_o4AAA=",
        "showAs": "busy",
        "type": "occurrence",
        "recurrence": None,
        "body": {"contentType": "html", "content": ""},
        "start": {"dateTime": "2022-09-20T15:00:00.0000000", "timeZone": "UTC"},
        "end": {"dateTime": "2022-09-20T15:30:00.0000000", "timeZone": "UTC"},
    },
    {
        "id": "AAMkADdiYzg5OGRlLTY1MjktNDc2Ni05YmVkLWMxMzFlNTQ0MzU3YQFRAAgI2ptkOheAAEYAAAAAovUUFgf0jU2QbgCzOiiDrAcAbX_IPMmPs02YGYesdF-_dAACDNNrIwAAbX_IPMmPs02YGYesdF-_dAACDNPqOAAAEA==",
        "subject": "Expansion",
        "importance": "normal",
        "sensitivity": "normal",
        "isAllDay": False,
        "isCancelled": False,
        "isOrganizer": True,
        "seriesMasterId": "AAMkADdiYzg5OGRlLTY1MjktNDc2Ni05YmVkLWMxMzFlNTQ0MzU3YQBGAAAAAACi9RQWB-SNTZBuALM6KIOsBwBtf4g8yY_zTZgZh6x0X-50AAIM02sjAABtf4g8yY_zTZgZh6x0X-50AAIM0_o4AAA=",
        "showAs": "busy",
        "type": "occurrence",
        "recurrence": None,
        "body": {"contentType": "html", "content": ""},
        "start": {"dateTime": "2022-09-21T15:00:00.0000000", "timeZone": "UTC"},
        "end": {"dateTime": "2022-09-21T15:30:00.0000000", "timeZone": "UTC"},
    },
]


def test_calculate_exception_and_canceled_occurrences_without_changes():
    assert calculate_exception_and_canceled_occurrences(
        master_event, event_occurrences
    ) == ([], [])


def test_calculate_exception_and_canceled_occurrences_with_deletion():
    ((), (cancellation,)) = calculate_exception_and_canceled_occurrences(
        master_event, [event_occurrences[0], event_occurrences[2]]
    )

    assert cancellation["type"] == "synthetizedCancellation"
    assert cancellation["isCancelled"] is True
    assert cancellation["start"] == event_occurrences[1]["start"]
    assert cancellation["end"] == event_occurrences[1]["end"]
    assert cancellation["recurrence"] is None
    assert cancellation["subject"] == master_event["subject"]
    with pytest.raises(KeyError):
        cancellation["wrong"]


master_with_exception = {
    "id": "AAMkADdiYzg5OGRlLTY1MjktNDc2Ni05YmVkLWMxMzFlNTQ0MzU3YQBGAAAAAACi9RQWB-SNTZBuALM6KIOsBwBtf4g8yY_zTZgZh6x0X-50AAIM02sjAABtf4g8yY_zTZgZh6x0X-50AAIQCYpPAAA=",
    "subject": "Expansion",
    "importance": "normal",
    "sensitivity": "normal",
    "isAllDay": False,
    "isCancelled": False,
    "isOrganizer": True,
    "seriesMasterId": None,
    "showAs": "busy",
    "type": "seriesMaster",
    "body": {"contentType": "html", "content": ""},
    "start": {"dateTime": "2022-09-26T12:00:00.0000000", "timeZone": "UTC"},
    "end": {"dateTime": "2022-09-26T12:30:00.0000000", "timeZone": "UTC"},
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
}

master_with_exception_occurrences = [
    {
        "id": "AAMkADdiYzg5OGRlLTY1MjktNDc2Ni05YmVkLWMxMzFlNTQ0MzU3YQFRAAgI2qAbOJIAAEYAAAAAovUUFgf0jU2QbgCzOiiDrAcAbX_IPMmPs02YGYesdF-_dAACDNNrIwAAbX_IPMmPs02YGYesdF-_dAACEAmKTwAAEA==",
        "subject": "Expansion",
        "importance": "normal",
        "sensitivity": "normal",
        "isAllDay": False,
        "isCancelled": False,
        "isOrganizer": True,
        "seriesMasterId": "AAMkADdiYzg5OGRlLTY1MjktNDc2Ni05YmVkLWMxMzFlNTQ0MzU3YQBGAAAAAACi9RQWB-SNTZBuALM6KIOsBwBtf4g8yY_zTZgZh6x0X-50AAIM02sjAABtf4g8yY_zTZgZh6x0X-50AAIQCYpPAAA=",
        "showAs": "busy",
        "type": "exception",
        "recurrence": None,
        "body": {"contentType": "html", "content": ""},
        "start": {"dateTime": "2022-09-27T13:30:00.0000000", "timeZone": "UTC"},
        "end": {"dateTime": "2022-09-27T14:00:00.0000000", "timeZone": "UTC"},
    },
    {
        "id": "AAMkADdiYzg5OGRlLTY1MjktNDc2Ni05YmVkLWMxMzFlNTQ0MzU3YQFRAAgI2p9SDihAAEYAAAAAovUUFgf0jU2QbgCzOiiDrAcAbX_IPMmPs02YGYesdF-_dAACDNNrIwAAbX_IPMmPs02YGYesdF-_dAACEAmKTwAAEA==",
        "subject": "Expansion",
        "importance": "normal",
        "sensitivity": "normal",
        "isAllDay": False,
        "isCancelled": False,
        "isOrganizer": True,
        "seriesMasterId": "AAMkADdiYzg5OGRlLTY1MjktNDc2Ni05YmVkLWMxMzFlNTQ0MzU3YQBGAAAAAACi9RQWB-SNTZBuALM6KIOsBwBtf4g8yY_zTZgZh6x0X-50AAIM02sjAABtf4g8yY_zTZgZh6x0X-50AAIQCYpPAAA=",
        "showAs": "busy",
        "type": "occurrence",
        "recurrence": None,
        "body": {"contentType": "html", "content": ""},
        "start": {"dateTime": "2022-09-26T12:00:00.0000000", "timeZone": "UTC"},
        "end": {"dateTime": "2022-09-26T12:30:00.0000000", "timeZone": "UTC"},
    },
    {
        "id": "AAMkADdiYzg5OGRlLTY1MjktNDc2Ni05YmVkLWMxMzFlNTQ0MzU3YQFRAAgI2qDkYvvAAEYAAAAAovUUFgf0jU2QbgCzOiiDrAcAbX_IPMmPs02YGYesdF-_dAACDNNrIwAAbX_IPMmPs02YGYesdF-_dAACEAmKTwAAEA==",
        "subject": "Expansion",
        "bodyPreview": "",
        "importance": "normal",
        "sensitivity": "normal",
        "isAllDay": False,
        "isCancelled": False,
        "isOrganizer": True,
        "seriesMasterId": "AAMkADdiYzg5OGRlLTY1MjktNDc2Ni05YmVkLWMxMzFlNTQ0MzU3YQBGAAAAAACi9RQWB-SNTZBuALM6KIOsBwBtf4g8yY_zTZgZh6x0X-50AAIM02sjAABtf4g8yY_zTZgZh6x0X-50AAIQCYpPAAA=",
        "showAs": "busy",
        "type": "occurrence",
        "recurrence": None,
        "body": {"contentType": "html", "content": ""},
        "start": {"dateTime": "2022-09-28T12:00:00.0000000", "timeZone": "UTC"},
        "end": {"dateTime": "2022-09-28T12:30:00.0000000", "timeZone": "UTC"},
    },
]


def test_calculate_exception_and_canceled_occurrences_with_exception():
    ((exception,), ()) = calculate_exception_and_canceled_occurrences(
        master_with_exception, master_with_exception_occurrences
    )
    assert master_with_exception_occurrences[0] == exception
    assert exception["start"] == {
        "dateTime": "2022-09-27T13:30:00.0000000",
        "timeZone": "UTC",
    }
    assert exception["originalStart"] == {
        "dateTime": "2022-09-27T12:00:00.0000000",
        "timeZone": "UTC",
    }


@pytest.mark.parametrize(
    "event,location",
    [
        ({"locations": []}, None),
        (
            {
                "locations": [
                    {
                        "displayName": "Test Valley",
                        "address": {
                            "street": "Test Valley Golf Club Micheldever Road Overton",
                            "city": "Basingstoke",
                            "state": "Hampshire",
                            "countryOrRegion": "United Kingdom",
                            "postalCode": "RG25 3DS",
                        },
                    }
                ]
            },
            "Test Valley Golf Club Micheldever Road Overton, Basingstoke, Hampshire, RG25 3DS, United Kingdom",
        ),
        ({"locations": [{"displayName": "Kitchen",}]}, "Kitchen",),
        (
            {
                "onlineMeeting": {
                    "joinUrl": "https://teams.microsoft.com/l/meetup-join/xxx"
                }
            },
            "https://teams.microsoft.com/l/meetup-join/xxx",
        ),
    ],
)
def test_get_event_location(event, location):
    assert get_event_location(event) == location


@pytest.mark.parametrize(
    "attendee,participant",
    [
        (
            {
                "status": {"response": "none", "time": "0001-01-01T00:00:00Z"},
                "emailAddress": {
                    "name": "ralij86905@d3ff.com",
                    "address": "ralij86905@d3ff.com",
                },
            },
            {
                "email": "ralij86905@d3ff.com",
                "name": "ralij86905@d3ff.com",
                "status": "noreply",
                "notes": None,
            },
        ),
        (
            {
                "status": {"response": "declined", "time": "2022-09-08T15:40:17Z"},
                "emailAddress": {"name": "Somebody", "address": "somebody@close.com"},
            },
            {
                "email": "somebody@close.com",
                "name": "Somebody",
                "status": "no",
                "notes": None,
            },
        ),
        (
            {
                "status": {"response": "accepted", "time": "2022-09-08T15:45:09Z"},
                "emailAddress": {"name": "Test User", "address": "testing@gmail.com"},
            },
            {
                "email": "testing@gmail.com",
                "name": "Test User",
                "status": "yes",
                "notes": None,
            },
        ),
        (
            {
                "status": {
                    "response": "tentativelyAccepted",
                    "time": "2022-09-08T15:47:46Z",
                },
                "emailAddress": {"name": "Test User", "address": "testing@gmail.com"},
            },
            {
                "email": "testing@gmail.com",
                "name": "Test User",
                "status": "maybe",
                "notes": None,
            },
        ),
    ],
)
def test_get_event_participant(attendee, participant):
    assert get_event_participant(attendee) == participant


@pytest.mark.parametrize(
    "event,description",
    [
        (
            {
                "body": {
                    "contentType": "html",
                    "content": """
                        <html>
                            <head>
                                <meta http-equiv="Content-Type" content="text/html; charset=utf-8">
                            </head>
                            <body>
                                <div class="cal_a3fe">Test description</div>
                            </body>
                        </html>
                    """,
                }
            },
            "Test description",
        ),
        ({"body": {"contentType": "text", "content": "Text\n"}}, "Text"),
    ],
)
def test_get_event_description(event, description):
    assert get_event_description(event) == description


recurring_event = {
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
}


def test_parse_event_recurrence():
    event = parse_event(recurring_event, read_only=False)

    assert isinstance(event, RecurringEvent)
    assert event.uid == recurring_event["id"]
    assert event.title == "Expansion"
    assert event.start == datetime.datetime(2022, 9, 19, 15, tzinfo=pytz.UTC)
    assert event.end == datetime.datetime(2022, 9, 19, 15, 30, tzinfo=pytz.UTC)
    assert event.all_day is False
    assert event.last_modified == datetime.datetime(
        2022, 9, 27, 14, 41, 23, tzinfo=pytz.UTC
    )
    assert event.description == "Hello world!"
    assert event.location == "Parking"
    assert event.busy is True
    assert event.status == "confirmed"
    assert event.owner == "Example <example@example.com>"
    assert event.participants == [
        {
            "email": "attendee@example.com",
            "name": "attendee@example.com",
            "notes": None,
            "status": "no",
        }
    ]
    assert event.is_owner is True
    assert event.rrule == "RRULE:FREQ=DAILY;UNTIL=20220922T065959Z"
    assert event.cancelled is False
    assert event.visibility is None


single_instance_event = {
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
    "subject": "Test event 2",
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
    "onlineMeeting": {"joinUrl": "https://teams.microsoft.com/l/meetup-join/xyz"},
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
        "emailAddress": {"name": "Example <>", "address": "example_2@example.com"}
    },
}


def test_parse_event_recurrence():
    event = parse_event(single_instance_event, read_only=False)

    assert isinstance(event, Event)
    assert event.uid == single_instance_event["id"]
    assert event.title == "Test event 2"
    assert event.start == datetime.datetime(2022, 9, 15, 12, tzinfo=pytz.UTC)
    assert event.end == datetime.datetime(2022, 9, 15, 12, 30, tzinfo=pytz.UTC)
    assert event.all_day is False
    assert event.last_modified == datetime.datetime(
        2022, 9, 7, 8, 41, 36, tzinfo=pytz.UTC
    )
    assert event.description == "Singular"
    assert event.location == "https://teams.microsoft.com/l/meetup-join/xyz"
    assert event.busy is True
    assert event.status == "confirmed"
    assert event.owner == '"Example <>" <example_2@example.com>'
    assert event.participants == []
    assert event.is_owner is True
    assert event.cancelled is False
    assert event.visibility is None


outlook_calendar = {
    "id": "AAMkADdiYzg5OGRlLTY1MjktNDc2Ni05YmVkLWMxMzFlNTQ0MzU3YQBGAAAAAACi9RQWB-SNTZBuALM6KIOsBwBtf4g8yY_zTZgZh6x0X-50AAAAAAEGAABtf4g8yY_zTZgZh6x0X-50AAAAADafAAA=",
    "name": "Calendar",
    "color": "auto",
    "hexColor": "",
    "isDefaultCalendar": True,
    "changeKey": "bX+IPMmPs02YGYesdF/+dAAAAAACtA==",
    "canShare": True,
    "canViewPrivateItems": True,
    "canEdit": True,
    "allowedOnlineMeetingProviders": ["teamsForBusiness"],
    "defaultOnlineMeetingProvider": "teamsForBusiness",
    "isTallyingResponses": True,
    "isRemovable": True,
    "owner": {"name": "Example", "address": "example@example.com",},
}


def test_parse_calendar():
    calendar = parse_calendar(outlook_calendar)
    assert calendar.uid == outlook_calendar["id"]
    assert calendar.name == "Calendar"
    assert calendar.read_only is False
