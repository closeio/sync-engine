from typing import List, Literal, Optional, TypedDict


class MsGraphDateTimeTimeZone(TypedDict):
    """
    Describes the date, time, and time zone of a point in time.

    https://learn.microsoft.com/en-us/graph/api/resources/datetimetimezone
    """

    dateTime: str
    timeZone: str


MsGraphDayOfWeek = Literal[
    "sunday", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday"
]

ICalDayOfWeek = Literal["SU", "MO", "TU", "WE", "TH", "FR", "SA"]


MsGraphWeekIndex = Literal["first", "second", "third", "fourth", "last"]


MsGraphRecurrencePatternType = Literal[
    "daily",
    "weekly",
    "absoluteMonthly",
    "relativeMonthly",
    "absoluteYearly",
    "relativeYearly",
]

ICalFreq = Literal[
    "DAILY", "WEEKLY", "MONTHLY", "YEARLY",
]


class MsGraphRecurrencePattern(TypedDict):
    """
    Describes the frequency by which a recurring event repeats.

    https://learn.microsoft.com/en-us/graph/api/resources/recurrencepattern
    """

    dayOfMonth: int
    daysOfWeek: List[MsGraphDayOfWeek]
    firstDayOfWeek: MsGraphDayOfWeek
    index: MsGraphWeekIndex
    interval: int
    month: int
    type: MsGraphRecurrencePatternType


MsGraphRecurrenceRangeType = Literal["endDate", "noEnd", "numbered"]


class MsGraphRecurrenceRange(TypedDict):
    """
    Describes a date range over which a recurring event.

    https://learn.microsoft.com/en-us/graph/api/resources/recurrencerange
    """

    startDate: str
    endDate: str
    recurrenceTimeZone: str
    type: MsGraphRecurrenceRangeType
    numberOfOccurrences: int


class MsGraphPatternedRecurrence(TypedDict):
    """
    The recurrence pattern and range.

    https://learn.microsoft.com/en-us/graph/api/resources/patternedrecurrence
    """

    pattern: MsGraphRecurrencePattern
    range: MsGraphRecurrenceRange


MsGraphEventType = Literal["singleInstance", "occurrence", "exception", "seriesMaster"]


class MsGraphEvent(TypedDict):
    """
    An event in a user calendar, or the default calendar.

    https://learn.microsoft.com/en-us/graph/api/resources/event
    """

    id: str
    type: MsGraphEventType
    start: MsGraphDateTimeTimeZone
    end: MsGraphDateTimeTimeZone
    subject: str
    isAllDay: bool
    isCancelled: bool
    isOrganizer: bool
    recurrence: Optional[MsGraphPatternedRecurrence]
    originalStart: MsGraphDateTimeTimeZone
