import datetime
import email.utils
import enum
import itertools
import json
from typing import Any, Dict, List, Optional, Tuple, cast

import ciso8601
import dateutil.rrule
import pytz
import pytz.tzinfo

from inbox.events import util
from inbox.events.microsoft.graph_types import (
    ICalDayOfWeek,
    ICalFreq,
    MsGraphAttendee,
    MsGraphCalendar,
    MsGraphDateTimeTimeZone,
    MsGraphDayOfWeek,
    MsGraphEvent,
    MsGraphRecurrencePatternType,
    MsGraphResponse,
    MsGraphSensitivity,
    MsGraphShowAs,
    MsGraphWeekIndex,
)
from inbox.events.timezones import windows_timezones
from inbox.models.calendar import Calendar
from inbox.models.event import Event
from inbox.util.html import strip_tags


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
    return windows_timezones.get(timezone_id, timezone_id)


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
    occurrence inside a recurring event. Google keeps those deletions
    around and in Microsoft Outlook they just disappear. The whole
    system is built around Google model so we need to synthesize
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
    event: MsGraphEvent,
) -> Tuple[datetime.datetime, Optional[datetime.datetime]]:
    """
    Parse Microsoft Graph Recurrence Range start and end dates.

    It also combines start date with 0:00 time and end date with 23:59:59
    time because recurrence processing always uses datetimes.

    Arguments:
        event: Microsoft Graph Event

    Returns:
        Tuple of timezone-aware UTC datetimes
    """
    assert event["recurrence"]
    range = event["recurrence"]["range"]
    tzinfo = get_microsoft_tzinfo(
        range["recurrenceTimeZone"] or event["originalStartTimeZone"]
    )

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


def convert_msgraph_patterned_recurrence_to_ical_rrule(event: MsGraphEvent,) -> str:
    """
    Convert Microsoft Graph PatternedRecurrence to iCal RRULE.

    This was reverse-engineered by looking at recurrence occurances
    in Outlook UI, corresponding API results and then coming up with
    iCal RRULEs. See tests for examples. Note that even though
    Microsoft Graph PatternedRecurence contains start date iCal RRULE
    does not because one can use series master event start date when
    expanding.

    Arguments:
        event: Microsoft Graph Event

    Returns:
        iCal RRULE string
    """
    assert event["recurrence"]
    patterned_recurrence = event["recurrence"]
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
        _, until = parse_msgraph_range_start_and_until(event)
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


def synthesize_canceled_occurrence(
    master_event: MsGraphEvent, start_datetime: datetime.datetime
) -> MsGraphEvent:
    """
    Create gap in series occurences i.e. what happens
    if one deletes an occurrence that is part of recurring event.

    This does not respresent occurrences that
    were retrieved from API since Microsoft does not return deleted
    occurrences. Those phantom occurrences are created on the
    fly by expanding series master recurrence rule and seeing which
    ones are missing. The reason we are doing this is to mock how Google works
    i.e. you can still retrieve deleted occurrences in Google APIs, and the
    whole system is built around this assumption.

    Arguments:
        master_event: The master event this cancellation belongs to
        start_datetime: The gap date

    Returns:
        Canceled ocurrence
    """
    assert master_event["type"] == "seriesMaster"
    assert start_datetime.tzinfo == pytz.UTC

    cancellation_id = (
        master_event["id"]
        + "-synthesizedCancellation-"
        + start_datetime.date().isoformat()
    )
    cancellation_start = dump_datetime_as_msgraph_datetime_tz(start_datetime)
    duration = parse_msgraph_datetime_tz_as_utc(
        master_event["end"]
    ) - parse_msgraph_datetime_tz_as_utc(master_event["start"])
    cancellation_end = dump_datetime_as_msgraph_datetime_tz(start_datetime + duration)

    result = {
        **master_event,
        "id": cancellation_id,
        "type": "synthesizedCancellation",
        "isCancelled": True,
        "recurrence": None,
        "start": cancellation_start,
        "originalStart": cancellation_start,
        "end": cancellation_end,
    }

    return cast(MsGraphEvent, result)


def populate_original_start_in_exception_occurrence(
    exception_instance: MsGraphEvent, original_start: datetime.datetime
) -> MsGraphEvent:
    """
    Populate originalStart on exception occurences.

    The reason we need this is to mimick how Google works i.e.
    you can know the original time if an occurrence was rescheduled.

    Arguments:
        exception_instance: The exception instance
        original_start: Original start time as retrieved by
            expanding master event
    """
    assert exception_instance["type"] == "exception"
    assert original_start.tzinfo == pytz.UTC

    exception_instance["originalStart"] = dump_datetime_as_msgraph_datetime_tz(
        original_start
    )

    return exception_instance


def calculate_exception_and_canceled_occurrences(
    master_event: MsGraphEvent,
    event_occurrences: List[MsGraphEvent],
    end: datetime.datetime,
) -> Tuple[List[MsGraphEvent], List[MsGraphEvent]]:
    """
    Given master event recurrence rule and occurences find exception occurrences
    and synthesize canceled occurences.

    Arguments:
        master_event: The master event
        event_occurrences: occurrences correspoing to the master event
        end: The maximum date of recurrence expansion, this is important
            to prevent infinite or very long running recurrences.

    Returns:
        Tuple containing exception occurrences and canceled occurrences
    """
    assert master_event["type"] == "seriesMaster"
    assert master_event["recurrence"]
    assert end.tzinfo == pytz.UTC

    master_rrule = convert_msgraph_patterned_recurrence_to_ical_rrule(master_event)
    master_start_datetime = parse_msgraph_datetime_tz_as_utc(master_event["start"])
    master_parsed_rrule = dateutil.rrule.rrulestr(
        master_rrule, dtstart=master_start_datetime
    )
    master_datetimes = {
        dt.date(): dt
        for dt in itertools.takewhile(lambda dt: dt <= end, master_parsed_rrule)
    }

    exception_occurrences = [
        occurrence
        for occurrence in event_occurrences
        if occurrence["type"] == "exception"
    ]
    exception_datetimes = {
        parse_msgraph_datetime_tz_as_utc(occurrence["start"])
        for occurrence in exception_occurrences
    }
    original_exception_datetimes = {
        master_datetimes[dt.date()] for dt in exception_datetimes
    }
    for exception_instance, original_exception_datetime in zip(
        exception_occurrences, original_exception_datetimes
    ):
        populate_original_start_in_exception_occurrence(
            exception_instance, original_exception_datetime
        )

    occurrence_datetimes = {
        parse_msgraph_datetime_tz_as_utc(instance["start"])
        for instance in event_occurrences
    }
    canceled_dates = set(master_datetimes) - {dt.date() for dt in occurrence_datetimes}
    canceled_occurrences = [
        synthesize_canceled_occurrence(master_event, master_datetimes[date])
        for date in canceled_dates
    ]

    return exception_occurrences, canceled_occurrences


MS_GRAPH_TO_SYNC_ENGINE_STATUS_MAP: Dict[MsGraphResponse, str] = {
    "none": "noreply",
    "notResponded": "noreply",
    "declined": "no",
    "accepted": "yes",
    "organizer": "yes",
    "tentativelyAccepted": "maybe",
}


def get_event_participant(attendee: MsGraphAttendee) -> Dict[str, Any]:
    """
    Convert Microsoft Graph attendee into sync-engine participant.

    Arguments:
        attendee: The attendee as returned by Microsoft Graph API

    Returns:
        Sync-engine participant dictionary
    """
    return {
        "email": attendee["emailAddress"].get("address"),
        "name": attendee["emailAddress"]["name"],
        "status": MS_GRAPH_TO_SYNC_ENGINE_STATUS_MAP[attendee["status"]["response"]],
        "notes": None,
    }


def get_event_location(event: MsGraphEvent) -> Optional[str]:
    """
    Figure out event location.

    Most meetings happen online these days so we always prefer
    meeting URL. Microsoft unlike Google supports multiple physical locations,
    the order of locations field corresponds to the one in the UI. For the
    time being we use the first physical location by looking at its address and
    finally falling back to display name.

    Arguments:
        event: The event

    Returns:
        String representing event location
    """
    online_meeting = event.get("onlineMeeting")
    join_url = online_meeting.get("joinUrl") if online_meeting else None
    if join_url:
        return join_url

    locations = event["locations"]
    if locations:
        location, *_ = locations
        address = location.get("address")
        if address:
            address_list = [
                address.get("street"),
                address.get("city"),
                address.get("state"),
                address.get("postalCode"),
                address.get("countryOrRegion"),
            ]
            return ", ".join(part for part in address_list if part) or None
        return location.get("displayName") or None

    return None


def get_event_description(event: MsGraphEvent) -> Optional[str]:
    """
    Get event description as plain text.

    Note that I was only able to get HTML bodies using
    Outlook UI but Microsoft also documents plain text
    so we handle that as well.

    Arguments:
        event: The event

    Returns:
        Plain text string with all the HTML removed
    """
    content_type = event["body"]["contentType"]

    assert content_type in ["text", "html"]
    content = event["body"]["content"]

    if content_type == "html":
        content = strip_tags(content)

    return content.strip() or None


MS_GRAPH_SENSITIVITY_TO_VISIBILITY_MAP: Dict[MsGraphSensitivity, Optional[str]] = {
    "private": "private",
    "normal": None,
    "personal": "private",
    "confidential": "private",
}

MS_GRAPH_SHOW_AS_TO_BUSY_MAP: Dict[MsGraphShowAs, bool] = {
    "free": False,
    "tentative": True,
    "busy": True,
    "oof": True,
    "workingElsewhere": True,
    "unknown": False,
}


def parse_event(
    event: MsGraphEvent, *, read_only: bool, master_event_uid: Optional[str] = None,
) -> Event:
    """
    Parse event coming from Microsoft Graph API as ORM object.

    Arguments:
        event: The event as returned by Microsoft
        read_only: If the event is read-only i.e. comes from a calendar
            user cannot edit
        master_event_uid: Links exceptions and cancellations with their
            master event

    Returns:
        ORM event
    """
    assert event["type"] != "occurrence"

    if not master_event_uid or event["type"] in ["singleInstance", "seriesMaster"]:
        assert not master_event_uid and event["type"] in [
            "singleInstance",
            "seriesMaster",
        ]

    if master_event_uid or event["type"] in ["exception", "synthesizedCancellation"]:
        assert master_event_uid and event["type"] in [
            "exception",
            "synthesizedCancellation",
        ]

    uid = event["id"]
    raw_data = json.dumps(event)
    title = event["subject"]
    start = parse_msgraph_datetime_tz_as_utc(event["start"])
    end = parse_msgraph_datetime_tz_as_utc(event["end"])
    all_day = event["isAllDay"]
    last_modified = ciso8601.parse_datetime(event["lastModifiedDateTime"]).replace(
        microsecond=0
    )
    description = get_event_description(event)
    location = get_event_location(event)
    busy = MS_GRAPH_SHOW_AS_TO_BUSY_MAP[event["showAs"]]
    sequence_number = 0
    status = "cancelled" if event["isCancelled"] else "confirmed"
    organizer = event["organizer"]
    if organizer:
        organizer_email_address = organizer.get("emailAddress", {})
        owner = email.utils.formataddr(
            (
                organizer_email_address.get("name", ""),
                organizer_email_address.get("address", ""),
            )
        )
    else:
        owner = ""
    attendees = event.get("attendees", [])
    participants = [get_event_participant(attendee) for attendee in attendees]
    is_owner = event["isOrganizer"]
    cancelled = status == "cancelled"
    visibility = MS_GRAPH_SENSITIVITY_TO_VISIBILITY_MAP[event["sensitivity"]]
    if event["type"] == "seriesMaster":
        assert event["recurrence"]
        recurrence = [convert_msgraph_patterned_recurrence_to_ical_rrule(event)]
        start_tz = convert_microsoft_timezone_to_olson(
            event["recurrence"]["range"]["recurrenceTimeZone"]
            or event["originalStartTimeZone"]
        )
    else:
        recurrence = None
        start_tz = None

    if event["type"] in ["exception", "synthesizedCancellation"]:
        original_start = parse_msgraph_datetime_tz_as_utc(event["originalStart"])
    else:
        original_start = None

    return Event.create(
        uid=uid,
        raw_data=raw_data,
        title=title,
        description=description,
        location=location,
        busy=busy,
        start=start,
        end=end,
        all_day=all_day,
        owner=owner,
        is_owner=is_owner,
        read_only=read_only,
        participants=participants,
        recurrence=recurrence,
        last_modified=last_modified,
        original_start_tz=start_tz,
        original_start_time=original_start,
        master_event_uid=master_event_uid,
        cancelled=cancelled,
        status=status,
        sequence_number=sequence_number,
        source="local",
        visibility=visibility,
    )


def parse_calendar(calendar: MsGraphCalendar) -> Calendar:
    """
    Parse calendar coming from Microsoft Graph API as ORM object.

    Arguments:
        calendar: The calendar as returned by Microsoft

    Returns:
        ORM calendar
    """
    uid = calendar["id"]
    name = calendar["name"]
    read_only = not calendar["canEdit"]
    default = calendar["isDefaultCalendar"]

    return Calendar(uid=uid, name=name, read_only=read_only, default=default)
