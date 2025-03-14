import string
from collections import namedtuple
from typing import NamedTuple

import arrow  # type: ignore[import-untyped]
from dateutil.parser import parse

from inbox.models.calendar import Calendar
from inbox.models.when import parse_as_when


# TODO(emfree) remove (currently used in other repos)
class MalformedEventError(Exception):
    pass


def parse_datetime(datetime):  # type: ignore[no-untyped-def]  # noqa: ANN201
    # returns a UTC-aware datetime as an Arrow object.
    # to access the `datetime` object: `obj.datetime`
    # to convert to a naive datetime: `obj.naive`
    # http://crsmithdev.com/arrow/
    if datetime is not None:
        if isinstance(datetime, int):
            return arrow.get(datetime).to("utc")
        return arrow.get(parse(datetime)).to("utc")
    return None


def parse_rrule_datetime(  # type: ignore[no-untyped-def]  # noqa: ANN201
    datetime, tzinfo=None
):
    # format: 20140904T133000Z (datetimes) or 20140904 (dates)
    if datetime[-1] == "Z":
        tzinfo = "UTC"
        datetime = datetime[:-1]
    if len(datetime) == 8:
        dt = arrow.get(datetime, "YYYYMMDD").to("utc")
    else:
        dt = arrow.get(datetime, "YYYYMMDDTHHmmss")
    if tzinfo and tzinfo != "UTC":
        dt = arrow.get(dt.datetime, tzinfo)
    return dt


def serialize_datetime(d):  # type: ignore[no-untyped-def]  # noqa: ANN201
    return d.strftime("%Y%m%dT%H%M%SZ")


EventTime = namedtuple("EventTime", ["start", "end", "all_day"])


def when_to_event_time(raw):  # type: ignore[no-untyped-def]  # noqa: ANN201
    when = parse_as_when(raw)
    return EventTime(when.start, when.end, when.all_day)


def parse_google_time(d):  # type: ignore[no-untyped-def]  # noqa: ANN201
    # google dictionaries contain either 'date' or 'dateTime' & 'timeZone'
    # 'dateTime' is in ISO format so is UTC-aware, 'date' is just a date
    for key, dt in d.items():
        if key != "timeZone":
            return arrow.get(dt)
    return None


def google_to_event_time(start_raw: str, end_raw: str) -> EventTime:
    start: arrow.Arrow = parse_google_time(start_raw)
    end: arrow.Arrow = parse_google_time(end_raw)
    if start > end:
        start, end = (end, start)

    if "date" in start_raw:
        # Google all-day events normally end a 'day' later than they should,
        # but not always if they were created by a third-party client.
        end = max(start, end.shift(days=-1))
        d = {"start_date": start, "end_date": end}
    else:
        d = {"start_time": start, "end_time": end}

    event_time = when_to_event_time(d)

    return event_time


def valid_base36(uid):  # type: ignore[no-untyped-def]  # noqa: ANN201
    # Check that an uid is a base36 element.
    return all(c in (string.ascii_lowercase + string.digits) for c in uid)


def removed_participants(  # type: ignore[no-untyped-def]  # noqa: ANN201
    original_participants, update_participants
):
    """
    Returns the name and addresses of the participants which have been
    removed.
    """  # noqa: D401
    original_table = {
        part["email"].lower(): part.get("name")
        for part in original_participants
        if "email" in part
    }
    update_table = {
        part["email"].lower(): part.get("name")
        for part in update_participants
        if "email" in part
    }

    ret = []
    for email in original_table:  # noqa: PLC0206
        if email not in update_table:
            ret.append(dict(email=email, name=original_table[email]))

    return ret


class CalendarSyncResponse(NamedTuple):
    """
    Container for a parsed API response.

    API calls return adds/updates/deletes
    all together, but we want to handle deletions separately in our persistence
    logic. deleted_uids should be a list of uids, and updated_objects should be a
    list of (un-added, uncommitted) model instances.
    """

    deleted_uids: list[str]
    updated_objects: list[Calendar]
