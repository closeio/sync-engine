from typing import List, Literal, Optional, TypedDict

from typing_extensions import NotRequired


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

ICalFreq = Literal["DAILY", "WEEKLY", "MONTHLY", "YEARLY"]


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


class MsGraphEmailAddress(TypedDict):
    address: NotRequired[str]
    name: str


MsGraphResponse = Literal[
    "none", "organizer", "tentativelyAccepted", "accepted", "declined", "notResponded"
]


class MsGraphResponseStatus(TypedDict):
    """
    The response status of an attendee or organizer for a meeting request.

    https://learn.microsoft.com/en-us/graph/api/resources/responsestatus
    """

    response: MsGraphResponse


class MsGraphAttendee(TypedDict):
    """
    An event attendee.

    https://learn.microsoft.com/en-us/graph/api/resources/attendee
    """

    emailAddress: MsGraphEmailAddress
    status: MsGraphResponseStatus


class MsGraphOnelineMeetingInfo(TypedDict):
    """
    Details for an attendee to join the meeting online.

    https://learn.microsoft.com/en-us/graph/api/resources/onlinemeetinginfo
    """

    joinUrl: Optional[str]


class MsGraphPhysicalAddress(TypedDict):
    """
    Represents the street address of a resource such as a contact or event.

    https://learn.microsoft.com/en-us/graph/api/resources/physicaladdress
    """

    city: str
    countryOrRegion: str
    postalCode: str
    state: str
    street: str


class MsGraphLocation(TypedDict):
    """
    Represents location information of an event.

    https://learn.microsoft.com/en-us/graph/api/resources/location
    """

    displayName: str
    address: MsGraphPhysicalAddress


MsGraphContentType = Literal["text", "html"]


class MsGraphItemBody(TypedDict):
    """
    Represents properties of the body of an item, such as a message, event or group post.

    https://learn.microsoft.com/en-us/graph/api/resources/itembody
    """

    content: str
    contentType: MsGraphContentType


MsGraphShowAs = Literal[
    "free", "tentative", "busy", "oof", "workingElsewhere", "unknown"
]


class MsGraphRecipient(TypedDict):
    """
    Represents information about a user in the sending or receiving end of an event.

    https://learn.microsoft.com/en-us/graph/api/resources/recipient
    """

    emailAddress: MsGraphEmailAddress


MsGraphSensitivity = Literal["private", "normal", "personal", "confidential"]


class MsGraphEvent(TypedDict):
    """
    An event in a user calendar, or the default calendar.

    https://learn.microsoft.com/en-us/graph/api/resources/event
    """

    id: str
    type: MsGraphEventType
    start: MsGraphDateTimeTimeZone
    originalStart: str
    end: MsGraphDateTimeTimeZone
    lastModifiedDateTime: str
    showAs: MsGraphShowAs
    organizer: Optional[MsGraphRecipient]
    sensitivity: MsGraphSensitivity
    subject: Optional[str]
    isAllDay: bool
    isCancelled: bool
    isOrganizer: bool
    recurrence: Optional[MsGraphPatternedRecurrence]
    attendees: List[MsGraphAttendee]
    onlineMeeting: Optional[MsGraphOnelineMeetingInfo]
    locations: List[MsGraphLocation]
    body: Optional[MsGraphItemBody]
    originalStartTimeZone: str
    originalEndTimeZone: str


class MsGraphCalendar(TypedDict):
    """
    Represents a container for event resources.

    https://learn.microsoft.com/en-us/graph/api/resources/calendar
    """

    id: str
    name: str
    canEdit: bool
    isDefaultCalendar: bool


MsGraphType = Literal["#Microsoft.Graph.Event", "#Microsoft.Graph.Calendar"]

MsGraphResourceData = TypedDict(
    "MsGraphResourceData", {"@odata.type": MsGraphType, "id": str}
)

MsGraphChangeType = Literal["created", "updated", "deleted"]


class MsGraphChangeNotification(TypedDict):
    """
    Represents the notification sent to the subscriber.

    https://learn.microsoft.com/en-us/graph/api/resources/changenotification
    """

    changeType: NotRequired[MsGraphChangeType]
    clientState: str
    resource: str
    resourceData: MsGraphResourceData


class MsGraphChangeNotificationCollection(TypedDict):
    """
    Represents a collection of resource change notifications sent to the subscriber.

    https://learn.microsoft.com/en-us/graph/api/resources/changenotificationcollection
    """

    value: List[MsGraphChangeNotification]


class MsGraphSubscription(TypedDict):
    """
    A subscription allows a client app to receive change notifications
    about changes to data in Microsoft Graph.

    https://learn.microsoft.com/en-us/graph/api/resources/subscription
    """

    id: str
    expirationDateTime: str
