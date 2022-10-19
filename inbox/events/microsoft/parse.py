import datetime
from typing import Union

import ciso8601
import pytz
import pytz.tzinfo

from inbox.events.microsoft.graph_types import MsGraphDateTimeTimeZone
from inbox.events.timezones import windows_timezones

TzInfo = Union["pytz._UTCclass", pytz.tzinfo.StaticTzInfo, pytz.tzinfo.DstTzInfo]


def convert_microsoft_timezone_to_olson(timezone_id: str) -> str:
    if timezone_id in windows_timezones:
        timezone_id = windows_timezones[timezone_id]

    return timezone_id


def get_microsoft_tzinfo(timezone_id: str) -> TzInfo:
    timezone_id = convert_microsoft_timezone_to_olson(timezone_id)

    return pytz.timezone(timezone_id)


def parse_msgraph_datetime_tz_as_utc(datetime_tz: MsGraphDateTimeTimeZone):
    """"""
    tzinfo = get_microsoft_tzinfo(datetime_tz["timeZone"])
    dt = ciso8601.parse_datetime(datetime_tz["dateTime"])

    return tzinfo.localize(dt).astimezone(pytz.UTC)


def dump_datetime_as_msgraph_datetime_tz(
    dt: datetime.datetime,
) -> MsGraphDateTimeTimeZone:
    assert dt.tzinfo == pytz.UTC
    return {
        "dateTime": dt.replace(tzinfo=None, microsecond=0).isoformat() + ".0000000",
        "timeZone": "UTC",
    }
