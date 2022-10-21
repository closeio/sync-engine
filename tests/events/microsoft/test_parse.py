import datetime

import dateutil
import pytest
import pytz

from inbox.events.microsoft.parse import (
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
