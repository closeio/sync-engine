import copy
import datetime
import enum
from typing import Any, Dict, List, Optional, Tuple

import ciso8601
import dateutil.rrule
import pytz
import pytz.tzinfo

from inbox.events import util
from inbox.events.microsoft.graph_types import (
    ICalDayOfWeek,
    ICalFreq,
    MsGraphDateTimeTimeZone,
    MsGraphDayOfWeek,
    MsGraphPatternedRecurrence,
    MsGraphRecurrencePatternType,
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


class CombineMode(enum.Enum):
    START = datetime.time(0, 0, 0)
    END = datetime.time(23, 59, 59)


def combine_msgraph_recurrence_date_with_time(
    date: str, tzinfo: pytz.tzinfo.BaseTzInfo, mode: CombineMode
) -> datetime.datetime:
    """
    Combine date with time according to mode and localize it as UTC.

    Arguments:
        date: ISO date string
        tzinfo: timezone the date should be in after combining with time
        mode: Either append 0:00 or 23:59:59 as time

    Returns:
        Timezone-aware UTC datetime
    """
    parsed_date = datetime.date.fromisoformat(date)
    extended_datetime = datetime.datetime.combine(parsed_date, mode.value)
    return tzinfo.localize(extended_datetime).astimezone(pytz.UTC)


def parse_msgraph_range_start_and_until(
    range: MsGraphRecurrenceRange,
) -> Tuple[datetime.datetime, Optional[datetime.datetime]]:
    """
    Parse Microsoft Graph Recurrence Range start and end dates.

    It also combines start date with 0:00 time and end date with 23:59:59
    time because recurrence processing always uses datetimes.

    Arguments:
        range: Microsoft Graph RecurranceRange

    Returns:
        Tuple of timezone-aware UTC datetimes
    """
    tzinfo = get_microsoft_tzinfo(range["recurrenceTimeZone"])

    start_datetime = combine_msgraph_recurrence_date_with_time(
        range["startDate"], tzinfo, CombineMode.START
    )

    if range["type"] == "endDate":
        until_datetime = combine_msgraph_recurrence_date_with_time(
            range["endDate"], tzinfo, CombineMode.END
        )
    elif range["type"] == "noEnd":
        until_datetime = None
    else:
        raise NotImplementedError()

    return start_datetime, until_datetime


MS_GRAPH_PATTERN_TYPE_TO_ICAL_FREQ_INTERVAL_MULTIPLIER: Dict[
    MsGraphRecurrencePatternType, Tuple[ICalFreq, int]
] = {
    "daily": ("DAILY", 1),
    "weekly": ("WEEKLY", 1),
    "absoluteMonthly": ("MONTHLY", 1),
    "relativeMonthly": ("MONTHLY", 1),
    "absoluteYearly": ("YEARLY", 1),
    # although this is yearly in Outlook,
    # for iCalendar RRULE to work like Outlook we need every 12 months.
    "relativeYearly": ("MONTHLY", 12),
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

# It's not strictly necessary to serialize RRULEs this way but it's common
# to do it in this order and it also makes testing easier.
RRULE_SERIALIZATION_ORDER = ["FREQ", "INTERVAL", "WKST", "BYDAY", "UNTIL", "COUNT"]


def convert_msgraph_patterned_recurrence_to_ical_rrule(
    patterned_recurrence: MsGraphPatternedRecurrence,
) -> str:
    """
    Convert Microsoft Graph PatternedRecurrence to iCal RRULE.

    This was reverse-engineered by looking at recurrence occurances
    in Outlook UI, corresponding API results and then coming up with
    iCal RRULEs. See tests for examples. Note that even though
    Microsoft Graph PatternedRecurence contains start date iCal RRULE
    does not because one can use series master event start date when
    expanding.

    Arguments:
        patterned_recurrence: Microsoft Graph PatternedRecurrence

    Returns:
        iCal RRULE string
    """
    pattern, range = patterned_recurrence["pattern"], patterned_recurrence["range"]

    # first handle FREQ (Frequency), INTERVAL and BYDAY
    freq, multiplier = MS_GRAPH_PATTERN_TYPE_TO_ICAL_FREQ_INTERVAL_MULTIPLIER[
        pattern["type"]
    ]
    interval = pattern["interval"] * multiplier

    rrule: Dict[str, str] = {
        "FREQ": freq,
    }
    if interval != 1:
        rrule["INTERVAL"] = str(interval)

    if pattern["type"] in ["daily", "absoluteMonthly", "absoluteYearly"]:
        pass  # only FREQ and INTERVAL
    elif pattern["type"] == "weekly":
        rrule["BYDAY"] = ",".join(
            MS_GRAPH_TO_ICAL_DAY[day_of_week] for day_of_week in pattern["daysOfWeek"]
        )
    elif pattern["type"] in ["relativeMonthly", "relativeYearly"]:
        (day_of_week,) = pattern["daysOfWeek"]
        rrule["BYDAY"] = (
            str(MS_GRAPH_TO_ICAL_INDEX[pattern["index"]])
            + MS_GRAPH_TO_ICAL_DAY[day_of_week]
        )
    else:
        # Should be unreachable
        raise ValueError(f"Unexpected value {pattern['type']!r} for pattern type")

    # WKST (Week start) is only significant when BYDAY is also present.
    # See WKST Rule Notes in
    # https://ewsoftware.github.io/PDI/html/3f7e1bcc-3afe-4978-95e7-e6515eae45df.htm
    if "BYDAY" in rrule:
        rrule["WKST"] = MS_GRAPH_TO_ICAL_DAY[pattern["firstDayOfWeek"]]

    # Recurrences can be either limited by end date (`endDate`),
    # infinite (`noEnd`) or have limited number of occurences (`numbered`).
    # In practice I only saw `endDate` and `noEnd` in Outlook UI but according
    # to docs it's also possible to have `numbered`.
    count = None
    until = None
    if range["type"] in ["endDate", "noEnd"]:
        _, until = parse_msgraph_range_start_and_until(range)
    elif range["type"] == "numbered":
        count = range["numberOfOccurrences"]
        assert count > 0
    else:
        # Shoud be unreachable
        raise ValueError(f"Unexpected value {range['type']!r} for range type")

    if until:
        rrule["UNTIL"] = util.serialize_datetime(until)
    if count:
        rrule["COUNT"] = str(count)

    return "RRULE:" + ";".join(
        f"{key}={rrule[key]}" for key in RRULE_SERIALIZATION_ORDER if key in rrule
    )


def synthetize_cancellation(
    master_event: Dict[str, Any], start_datetime: datetime.datetime
) -> Dict[str, Any]:
    assert master_event["type"] == "seriesMaster"
    assert start_datetime.tzinfo == pytz.UTC

    duration = parse_msgraph_datetime_tz_as_utc(
        master_event["end"]
    ) - parse_msgraph_datetime_tz_as_utc(master_event["start"])
    synthetized_event = copy.deepcopy(master_event)
    synthetized_event["id"] += "-synthetizedCancellation-" + start_datetime.isoformat()
    synthetized_event["type"] = "synthetizedCancellation"
    synthetized_event["isCancelled"] = True
    synthetized_event["start"] = dump_datetime_as_msgraph_datetime_tz(start_datetime)
    synthetized_event["end"] = dump_datetime_as_msgraph_datetime_tz(
        start_datetime + duration
    )
    synthetized_event["recurrence"] = None

    return synthetized_event


def enrich_exception(
    exception_instance: Dict[str, Any], original_start_time: datetime.datetime
) -> Dict[str, Any]:
    assert exception_instance["type"] == "exception"
    assert original_start_time.tzinfo == pytz.UTC

    exception_instance["originalStart"] = dump_datetime_as_msgraph_datetime_tz(
        original_start_time
    )

    return exception_instance


def calculate_event_exceptions_and_cancellations(
    master_event: Dict[str, Any], event_instances: List[Dict[str, Any]]
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    assert master_event["type"] == "seriesMaster"

    rrule = convert_msgraph_patterned_recurrence_to_ical_rrule(
        master_event["recurrence"]
    )
    start_datetime = parse_msgraph_datetime_tz_as_utc(master_event["start"])
    parsed_rrule = dateutil.rrule.rrulestr(rrule, dtstart=start_datetime)
    inflated_datetimes = {dt.date(): dt for dt in parsed_rrule}

    exception_instances = [
        instance for instance in event_instances if instance["type"] == "exception"
    ]
    exception_datetimes = {
        parse_msgraph_datetime_tz_as_utc(instance["start"])
        for instance in exception_instances
    }
    original_datetimes = {inflated_datetimes[dt.date()] for dt in exception_datetimes}
    for exception_instance, original_exception_datetime in zip(
        exception_instances, original_datetimes
    ):
        enrich_exception(exception_instance, original_exception_datetime)

    instance_datetimes = {
        parse_msgraph_datetime_tz_as_utc(instance["start"])
        for instance in event_instances
    }
    cancelled_dates = set(inflated_datetimes) - {dt.date() for dt in instance_datetimes}
    cancelled_instances = [
        synthetize_cancellation(master_event, inflated_datetimes[date])
        for date in cancelled_dates
    ]

    return exception_instances, cancelled_instances
