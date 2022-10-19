from typing import TypedDict


class MsGraphDateTimeTimeZone(TypedDict):
    """
    Describes the date, time, and time zone of a point in time.

    https://learn.microsoft.com/en-us/graph/api/resources/datetimetimezone
    """

    dateTime: str
    timeZone: str
