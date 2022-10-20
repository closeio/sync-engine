import datetime
import enum
from typing import Dict, Optional, Tuple

import ciso8601
import pytz
import pytz.tzinfo

from inbox.events import util
from inbox.events.microsoft.graph_types import (
    ICalDayOfWeek,
    MsGraphDateTimeTimeZone,
    MsGraphDayOfWeek,
    MsGraphPatternedRecurrence,
    MsGraphRecurrenceRange,
    MsGraphWeekIndex,
)
from inbox.events.timezones import windows_timezones


def convert_microsoft_timezone_to_olson(timezone_id: str) -> str:
    """
    Lookup Windows timezone id in conversion table.

    Microsoft prefers Windows timezone ids over Olson but they also
    support a small subset of Olson. We need to convert Microsoft
    timezone ids to Olson before attempting to construct TzInfo objects.

    https://learn.microsoft.com/en-us/windows-hardware/manufacture/desktop/default-time-zones#time-zones
    https://learn.microsoft.com/en-us/graph/api/resources/datetimetimezone#additional-time-zones

    Arguments:
        timezone_id: Windows or Olson timezone id

    Returns:
        Olson timezone id
    """
    if timezone_id in windows_timezones:
        timezone_id = windows_timezones[timezone_id]

    return timezone_id


def get_microsoft_tzinfo(timezone_id: str) -> pytz.tzinfo.BaseTzInfo:
    """
    Get TzInfo object given Windows timzone id

    Arguments:
        timezone_id: Windows or Olson timezone id

    Returns:
        TzInfo object
    """
    timezone_id = convert_microsoft_timezone_to_olson(timezone_id)

    return pytz.timezone(timezone_id)


def parse_msgraph_datetime_tz_as_utc(datetime_tz: MsGraphDateTimeTimeZone):
    """
    Parse Microsoft Graph DateTimeTimeZone and return UTC datetime.

    Arguments:
        datetime_tz: Microsoft Graph DateTimeTimeZone value

    Returns:
        timezone-aware UTC datetime
    """
    tzinfo = get_microsoft_tzinfo(datetime_tz["timeZone"])

    # Note that Microsoft always returns seconds with 7 fractional digits
    # so we need to use ciso8601 because Python stdlib only supports up to 6.
    dt = ciso8601.parse_datetime(datetime_tz["dateTime"])

    return tzinfo.localize(dt).astimezone(pytz.UTC)


def dump_datetime_as_msgraph_datetime_tz(
    dt: datetime.datetime,
) -> MsGraphDateTimeTimeZone:
    """
    Dump UTC datetime as Microsoft Graph DateTimeTimeZone.

    The only reason we need this is to create phantom exception
    events for gaps in recurring events i.e when a user deletes
    ocurrence inside a recurring event. Google keeps those deletions
    around and in Microsoft Outlook they just disappear. The whole
    system is built around Google model so we need to synthetize
    them.

    Arguments:
        dt: timezone-aware UTC datetime

    Returns:
        Microsoft Graph DateTimeTimeZone value
    """
    assert dt.tzinfo == pytz.UTC

    # Mimick Microsoft and always return 7 fractional digits
    return {
        "dateTime": dt.replace(tzinfo=None, microsecond=0).isoformat() + ".0000000",
        "timeZone": "UTC",
    }


MS_GRAPH_TO_ICAL_DAY: Dict[MsGraphDayOfWeek, ICalDayOfWeek] = {
    "sunday": "SU",
    "monday": "MO",
    "tuesday": "TU",
    "wednesday": "WE",
    "thursday": "TH",
    "friday": "FR",
    "saturday": "SA",
}

MS_GRAPH_TO_ICAL_INDEX: Dict[MsGraphWeekIndex, int] = {
    "first": 1,
    "second": 2,
    "third": 3,
    "fourth": 4,
    "last": -1,
}


class CombineMode(enum.Enum):
    START = datetime.time(0, 0, 0)
    END = datetime.time(23, 59, 59)


def combine_msgraph_recurrence_date_with_time(
    date: str, tzinfo: pytz.tzinfo.BaseTzInfo, mode: CombineMode
) -> datetime.datetime:
    parsed_date = datetime.date.fromisoformat(date)
    extended_datetime = datetime.datetime.combine(parsed_date, mode.value)
    return tzinfo.localize(extended_datetime).astimezone(pytz.UTC)


def parse_msgraph_range_start_and_until(
    range: MsGraphRecurrenceRange,
) -> Tuple[datetime.datetime, Optional[datetime.datetime]]:
    tzinfo = get_microsoft_tzinfo(range["recurrenceTimeZone"])

    start_datetime = combine_msgraph_recurrence_date_with_time(
        range["startDate"], tzinfo, CombineMode.START
    )

    until_datetime = None
    if range["type"] == "endDate":
        until_datetime = combine_msgraph_recurrence_date_with_time(
            range["endDate"], tzinfo, CombineMode.END
        )
    elif range["type"] == "noEnd":
        pass
    else:
        raise NotImplementedError()

    return start_datetime, until_datetime


def convert_msgraph_patterned_recurrence_to_ical_rrule(
    patterned_recurrence: MsGraphPatternedRecurrence,
) -> str:
    pattern, range = patterned_recurrence["pattern"], patterned_recurrence["range"]

    _, until = parse_msgraph_range_start_and_until(range)

    rrule = {}

    interval = pattern["interval"]
    wkst = MS_GRAPH_TO_ICAL_DAY[pattern["firstDayOfWeek"]]

    if pattern["type"] == "daily":
        rrule["FREQ"] = "DAILY"
        if interval != 1:
            rrule["INTERVAL"] = str(interval)
    elif pattern["type"] == "weekly":
        rrule["FREQ"] = "WEEKLY"
        rrule["WKST"] = wkst
        if interval != 1:
            rrule["INTERVAL"] = str(interval)
        rrule["BYDAY"] = ",".join(
            MS_GRAPH_TO_ICAL_DAY[day_of_week] for day_of_week in pattern["daysOfWeek"]
        )
    elif pattern["type"] == "absoluteMonthly":
        rrule["FREQ"] = "MONTHLY"
        rrule["WKST"] = wkst
        if interval != 1:
            rrule["INTERVAL"] = str(interval)
    elif pattern["type"] == "relativeMonthly":
        rrule["FREQ"] = "MONTHLY"
        rrule["WKST"] = wkst
        (day_of_week,) = pattern["daysOfWeek"]
        rrule["BYDAY"] = (
            str(MS_GRAPH_TO_ICAL_INDEX[pattern["index"]])
            + MS_GRAPH_TO_ICAL_DAY[day_of_week]
        )
        if interval != 1:
            rrule["INTERVAL"] = str(interval)
    elif pattern["type"] == "absoluteYearly":
        rrule["FREQ"] = "YEARLY"
        if interval != 1:
            rrule["INTERVAL"] = str(interval)
    elif pattern["type"] == "relativeYearly":
        # although this is yearly in Outlook,
        # for iCalendar RRULE to work like Outlook we need every 12 months.
        rrule["FREQ"] = "MONTHLY"
        rrule["WKST"] = wkst
        (day_of_week,) = pattern["daysOfWeek"]
        rrule["BYDAY"] = (
            str(MS_GRAPH_TO_ICAL_INDEX[pattern["index"]])
            + MS_GRAPH_TO_ICAL_DAY[day_of_week]
        )
        rrule["INTERVAL"] = str(interval * 12)
    else:
        raise NotImplementedError()

    if until:
        rrule["UNTIL"] = util.serialize_datetime(until)

    return "RRULE:" + ";".join(f"{key}={value}" for key, value in rrule.items())
