import datetime

import ciso8601
import pytz
import pytz.tzinfo

from inbox.events.microsoft.graph_types import MsGraphDateTimeTimeZone
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
