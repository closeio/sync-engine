import ast
import contextlib
import json
from datetime import datetime
from email.utils import parseaddr
from typing import Never

import arrow  # type: ignore[import-untyped]
from dateutil.parser import parse as date_parse
from sqlalchemy import (  # type: ignore[import-untyped]
    Boolean,
    Column,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    event,
)
from sqlalchemy.dialects.mysql import LONGTEXT  # type: ignore[import-untyped]
from sqlalchemy.orm import (  # type: ignore[import-untyped]
    backref,
    reconstructor,
    relationship,
    validates,
)
from sqlalchemy.types import TypeDecorator  # type: ignore[import-untyped]

from inbox.logging import get_logger
from inbox.models.base import MailSyncBase
from inbox.models.calendar import Calendar
from inbox.models.message import Message
from inbox.models.mixins import (
    DeletedAtMixin,
    HasPublicID,
    HasRevisions,
    UpdatedAtMixin,
)
from inbox.models.namespace import Namespace
from inbox.models.when import Date, DateSpan, Time, TimeSpan
from inbox.sqlalchemy_ext.util import MAX_TEXT_CHARS, BigJSON, MutableList
from inbox.util.addr import extract_emails_from_text
from inbox.util.encoding import unicode_safe_truncate

log = get_logger()

EVENT_STATUSES = ["confirmed", "tentative", "cancelled"]

TITLE_MAX_LEN = 1024
LOCATION_MAX_LEN = 255
RECURRENCE_MAX_LEN = 255
REMINDER_MAX_LEN = 255
OWNER_MAX_LEN = 1024
# UIDs MUST be "less than 255 octets" according to RFC 7986, but some events
# have more (seen up to 1034). We truncate at 767, which is InnoDB's index key
# prefix length.
UID_MAX_LEN = 767
MAX_LENS = {
    "location": LOCATION_MAX_LEN,
    "owner": OWNER_MAX_LEN,
    "recurrence": MAX_TEXT_CHARS,
    "reminders": REMINDER_MAX_LEN,
    "title": TITLE_MAX_LEN,
    "raw_data": MAX_TEXT_CHARS,
    "uid": UID_MAX_LEN,
    "master_event_uid": UID_MAX_LEN,
}


# Used to protect programmers from calling wrong constructor
# to create events
_EVENT_CREATED_SANELY_SENTINEL = object()


def time_parse(x: float | int | str | arrow.Arrow) -> arrow.Arrow:
    with contextlib.suppress(ValueError, TypeError):
        x = float(x)

    return arrow.get(x).to("utc").naive


class FlexibleDateTime(TypeDecorator):
    """Coerce arrow times to naive datetimes before handing to the database."""

    cache_ok = True

    impl = DateTime

    def process_bind_param(  # type: ignore[no-untyped-def]  # noqa: ANN201
        self, value, dialect
    ):
        if isinstance(value, arrow.arrow.Arrow):
            value = value.to("utc").naive
        if isinstance(value, datetime):
            value = arrow.get(value).to("utc").naive
        return value

    def process_result_value(  # type: ignore[no-untyped-def]  # noqa: ANN201
        self, value, dialect
    ):
        if value is None:
            return value
        else:
            return arrow.get(value).to("utc")

    def compare_values(self, x, y):  # type: ignore[no-untyped-def]  # noqa: ANN201
        if isinstance(x, datetime | int):
            x = arrow.get(x)
        if isinstance(y, datetime) or isinstance(x, int):
            y = arrow.get(y)

        return x == y


class Event(
    MailSyncBase, HasRevisions, HasPublicID, UpdatedAtMixin, DeletedAtMixin
):
    """Data for events."""

    API_OBJECT_NAME = "event"  # type: ignore[assignment]
    API_MODIFIABLE_FIELDS = [
        "title",
        "description",
        "location",
        "when",
        "participants",
        "busy",
    ]

    namespace_id = Column(
        ForeignKey(Namespace.id, ondelete="CASCADE"), nullable=False
    )

    namespace = relationship(Namespace, load_on_pending=True)

    calendar_id = Column(
        ForeignKey(Calendar.id, ondelete="CASCADE"), nullable=False
    )
    # Note that we configure a delete cascade, rather than
    # passive_deletes=True, in order to ensure that delete revisions are
    # created for events if their parent calendar is deleted.
    calendar = relationship(
        Calendar,
        backref=backref("events", cascade="delete"),
        load_on_pending=True,
    )

    # A server-provided unique ID.
    uid = Column(
        String(UID_MAX_LEN, collation="ascii_general_ci"), nullable=False
    )

    # DEPRECATED
    # TODO(emfree): remove
    provider_name = Column(String(64), nullable=False, default="DEPRECATED")
    source = Column("source", Enum("local", "remote"), default="local")

    raw_data = Column(Text, nullable=False)

    title = Column(String(TITLE_MAX_LEN), nullable=True)
    # The database column is named differently for legacy reasons.
    owner = Column("owner2", String(OWNER_MAX_LEN), nullable=True)

    description = Column("_description", LONGTEXT, nullable=True)
    location = Column(String(LOCATION_MAX_LEN), nullable=True)
    conference_data = Column(BigJSON, nullable=True)
    busy = Column(Boolean, nullable=False, default=True)
    read_only = Column(Boolean, nullable=False)
    reminders = Column(String(REMINDER_MAX_LEN), nullable=True)
    recurrence = Column(Text, nullable=True)
    start = Column(FlexibleDateTime, nullable=False)
    end = Column(FlexibleDateTime, nullable=True)
    all_day = Column(Boolean, nullable=False)
    is_owner = Column(Boolean, nullable=False, default=True)
    last_modified = Column(FlexibleDateTime, nullable=True)
    status = Column(
        "status", Enum(*EVENT_STATUSES), server_default="confirmed"
    )

    # This column is only used for events that are synced from iCalendar
    # files.
    message_id = Column(
        ForeignKey(Message.id, ondelete="CASCADE"), nullable=True
    )

    message = relationship(
        Message,
        backref=backref(
            "events",
            order_by="Event.last_modified",
            cascade="all, delete-orphan",
        ),
    )

    __table_args__ = (
        Index(
            "ix_event_ns_uid_calendar_id", "namespace_id", "uid", "calendar_id"
        ),
    )

    participants = Column(
        MutableList.as_mutable(BigJSON), default=[], nullable=True
    )

    # This is only used by the iCalendar invite code. The sequence number
    # stores the version number of the invite.
    sequence_number = Column(Integer, nullable=True)

    visibility = Column(Enum("private", "public"), nullable=True)

    discriminator = Column("type", String(30))
    __mapper_args__ = {
        "polymorphic_on": discriminator,
        "polymorphic_identity": "event",
    }

    @validates(
        "reminders",
        "recurrence",
        "owner",
        "location",
        "title",
        "uid",
        "raw_data",
    )
    def validate_length(self, key, value):  # type: ignore[no-untyped-def]  # noqa: ANN201
        if value is None:
            return None
        return unicode_safe_truncate(value, MAX_LENS[key])

    @property
    def when(self):  # type: ignore[no-untyped-def]  # noqa: ANN201
        if self.all_day:
            # Dates are stored as DateTimes so transform to dates here.
            start = arrow.get(self.start).to("utc").date()
            end = arrow.get(self.end).to("utc").date()
            return Date(start) if start == end else DateSpan(start, end)
        else:
            start = self.start
            end = self.end
            return Time(start) if start == end else TimeSpan(start, end)

    @when.setter
    def when(self, when) -> None:  # type: ignore[no-untyped-def]
        if "time" in when:
            self.start = self.end = time_parse(when["time"])
            self.all_day = False
        elif "start_time" in when:
            self.start = time_parse(when["start_time"])
            self.end = time_parse(when["end_time"])
            self.all_day = False
        elif "date" in when:
            self.start = self.end = date_parse(when["date"])
            self.all_day = True
        elif "start_date" in when:
            self.start = date_parse(when["start_date"])
            self.end = date_parse(when["end_date"])
            self.all_day = True

    def _merge_participant_attributes(  # type: ignore[no-untyped-def]
        self, left, right
    ):
        """Merge right into left. Right takes precedence unless it's null."""
        for attribute in right.keys():
            # Special cases:
            if (
                right[attribute] is None
                or right[attribute] == ""
                or right["status"] == "noreply"
            ):
                continue

            left[attribute] = right[attribute]

        return left

    def _partial_participants_merge(  # type: ignore[no-untyped-def]
        self, event
    ):
        """
        Merge the participants from event into self.participants.
        event always takes precedence over self, except if
        a participant in self isn't in event.

        This method is only called by the ical merging code because
        iCalendar attendance updates are partial: an RSVP reply often
        only contains the status of the person that RSVPs.
        It would be very wrong to call this method to merge, say, Google
        Events participants because they handle the merging themselves.
        """
        # We have to jump through some hoops because a participant may
        # not have an email or may not have a name, so we build a hash
        # where we can find both. Also note that we store names in the
        # hash only if the email is None.
        self_hash = {}
        for participant in self.participants:
            email = participant.get("email")
            name = participant.get("name")
            if email is not None:
                participant["email"] = participant["email"].lower()
                self_hash[email] = participant
            elif name is not None:
                # We have a name without an email.
                self_hash[name] = participant

        for participant in event.participants:
            email = participant.get("email")
            name = participant.get("name")

            # This is the tricky part --- we only want to store one entry per
            # participant --- we check if there's an email we already know, if
            # not we create it. Otherwise we use the name. This sorta works
            # because we're merging updates to an event and ical updates
            # always have an email address.
            # - karim
            if email is not None:
                participant["email"] = participant["email"].lower()
                if email in self_hash:
                    self_hash[email] = self._merge_participant_attributes(
                        self_hash[email], participant
                    )
                else:
                    self_hash[email] = participant
            elif name is not None:
                if name in self_hash:
                    self_hash[name] = self._merge_participant_attributes(
                        self_hash[name], participant
                    )
                else:
                    self_hash[name] = participant

        return list(self_hash.values())

    def update(self, event: "Event") -> None:
        if event.namespace is not None and event.namespace.id is not None:
            self.namespace_id = event.namespace.id

        if event.calendar is not None and event.calendar.id is not None:
            self.calendar_id = event.calendar.id

        if event.provider_name is not None:
            self.provider_name = event.provider_name

        self.uid = event.uid
        self.raw_data = event.raw_data
        self.title = event.title
        self.description = event.description
        self.location = event.location
        self.start = event.start
        self.end = event.end
        self.all_day = event.all_day
        self.owner = event.owner
        self.is_owner = event.is_owner
        self.read_only = event.read_only
        self.participants = event.participants
        self.busy = event.busy
        self.reminders = event.reminders
        self.recurrence = event.recurrence
        self.last_modified = event.last_modified
        self.message = event.message
        self.status = event.status
        self.visibility = event.visibility
        self.conference_data = event.conference_data

        if event.sequence_number is not None:
            self.sequence_number = event.sequence_number

    @property
    def recurring(self):  # type: ignore[no-untyped-def]  # noqa: ANN201
        if self.recurrence and self.recurrence != "":
            try:
                r = ast.literal_eval(self.recurrence)
                if isinstance(r, str):
                    r = [r]
                return r
            except (ValueError, SyntaxError):
                log.warning(
                    "Invalid RRULE entry for event",
                    event_id=self.id,
                    raw_rrule=self.recurrence,
                )
                return []
        return []

    @property
    def organizer_email(self):  # type: ignore[no-untyped-def]  # noqa: ANN201
        # For historical reasons, the event organizer field is stored as
        # "Owner Name <owner@email.com>".

        parsed_owner = parseaddr(self.owner)
        if len(parsed_owner) == 0:
            return None  # type: ignore[unreachable]

        if parsed_owner[1] == "":
            return None

        return parsed_owner[1]

    @property
    def organizer_name(self):  # type: ignore[no-untyped-def]  # noqa: ANN201
        parsed_owner = parseaddr(self.owner)

        if len(parsed_owner) == 0:
            return None  # type: ignore[unreachable]

        if parsed_owner[0] == "":
            return None

        return parsed_owner[0]

    @property
    def is_recurring(self) -> bool:
        return self.recurrence is not None

    @property
    def length(self):  # type: ignore[no-untyped-def]  # noqa: ANN201
        return self.when.delta

    @property
    def cancelled(self):  # type: ignore[no-untyped-def]  # noqa: ANN201
        return self.status == "cancelled"

    @cancelled.setter
    def cancelled(self, is_cancelled) -> None:  # type: ignore[no-untyped-def]
        if is_cancelled:
            self.status = "cancelled"
        else:
            self.status = "confirmed"

    @property
    def calendar_event_link(self):  # type: ignore[no-untyped-def]  # noqa: ANN201
        try:
            return json.loads(self.raw_data)["htmlLink"]
        except (ValueError, KeyError):
            return None

    @property
    def emails_from_description(self):  # type: ignore[no-untyped-def]  # noqa: ANN201
        if self.description:
            return extract_emails_from_text(self.description)
        else:
            return []

    @property
    def emails_from_title(self):  # type: ignore[no-untyped-def]  # noqa: ANN201
        if self.title:
            return extract_emails_from_text(self.title)
        else:
            return []

    @classmethod
    def create(cls, **kwargs):  # type: ignore[no-untyped-def]  # noqa: ANN206
        # Decide whether or not to instantiate a RecurringEvent/Override
        # based on the kwargs we get.
        cls_ = cls
        kwargs["__event_created_sanely"] = _EVENT_CREATED_SANELY_SENTINEL
        recurrence = kwargs.get("recurrence")
        master_event_uid = kwargs.get("master_event_uid")
        if recurrence and master_event_uid:
            raise ValueError("Event can't have both recurrence and master UID")
        if recurrence and recurrence != "":
            cls_ = RecurringEvent
        if master_event_uid:
            cls_ = RecurringEventOverride
        return cls_(**kwargs)

    def __init__(self, **kwargs) -> None:  # type: ignore[no-untyped-def]
        if (
            kwargs.pop("__event_created_sanely", None)
            is not _EVENT_CREATED_SANELY_SENTINEL
        ):
            raise AssertionError(
                "Use Event.create with appropriate keyword args "
                "instead of constructing Event, RecurringEvent or RecurringEventOverride "
                "directly"
            )

        # Allow arguments for all subclasses to be passed to main constructor
        for k in list(kwargs):
            if not hasattr(type(self), k):
                del kwargs[k]
        super().__init__(**kwargs)


# For API querying performance - default sort order is event.start ASC
Index("idx_namespace_id_started", Event.namespace_id, Event.start)


class RecurringEvent(Event):
    """
    Represents an individual one-off instance of a recurring event,
    including cancelled events.
    """

    __mapper_args__ = {"polymorphic_identity": "recurringevent"}
    __table_args__ = None  # type: ignore[assignment]

    id = Column(ForeignKey("event.id", ondelete="CASCADE"), primary_key=True)
    rrule = Column(String(RECURRENCE_MAX_LEN))
    exdate = Column(Text)  # There can be a lot of exception dates
    until = Column(FlexibleDateTime, nullable=True)
    start_timezone = Column(String(35))

    def __init__(self, **kwargs) -> None:  # type: ignore[no-untyped-def]
        self.start_timezone = kwargs.pop("original_start_tz", None)
        kwargs["recurrence"] = repr(kwargs["recurrence"])
        super().__init__(**kwargs)
        try:
            self.unwrap_rrule()
        except Exception as e:
            log.exception("Error parsing RRULE entry", event_id=self.id)

    # FIXME @karim: use an overrided property instead of a reconstructor.
    @reconstructor
    def reconstruct(self) -> None:
        try:
            self.unwrap_rrule()
        except Exception as e:
            log.exception("Error parsing stored RRULE entry", event_id=self.id)

    def inflate(self, start=None, end=None):  # type: ignore[no-untyped-def]  # noqa: ANN201
        # Convert a RecurringEvent into a series of InflatedEvents
        # by expanding its RRULE into a series of start times.
        from inbox.events.recurring import get_start_times

        occurrences = get_start_times(self, start, end)
        return [InflatedEvent(self, o) for o in occurrences]

    def unwrap_rrule(self) -> None:
        from inbox.events.util import parse_rrule_datetime

        # Unwraps the RRULE list of strings into RecurringEvent properties.
        for item in self.recurring:
            if item.startswith("RRULE"):
                self.rrule = item
                if "UNTIL" in item:
                    for p in item.split(";"):
                        if p.startswith("UNTIL"):
                            self.until = parse_rrule_datetime(p[6:])
            elif item.startswith("EXDATE"):
                self.exdate = item

    def all_events(self, start=None, end=None):  # type: ignore[no-untyped-def]  # noqa: ANN201
        # Returns all inflated events along with overrides that match the
        # provided time range.
        overrides = self.overrides  # type: ignore[attr-defined]
        if start:
            overrides = overrides.filter(RecurringEventOverride.start > start)
        if end:
            overrides = overrides.filter(RecurringEventOverride.end < end)

        # Google calendar events have the same uid __globally_. This means
        # that if I created an event, shared it with you and that I also
        # shared my calendar with you, override to this events for calendar B
        # may show up in a query for calendar A.
        # (https://phab.nylas.com/T3420)
        overrides = overrides.filter(
            RecurringEventOverride.calendar_id == self.calendar_id
        )

        events = list(overrides)
        overridden_starts = [e.original_start_time for e in events]
        # Remove cancellations from the override set
        events = [e for e in events if not e.cancelled]
        # If an override has not changed the start time for an event, including
        # if the override is a cancellation, the RRULE doesn't include an
        # exception for it. Filter out unnecessary inflated events
        # to cover this case by checking the start time.
        for e in self.inflate(start, end):
            if e.start not in overridden_starts:
                events.append(e)
        return sorted(events, key=lambda e: e.start)

    def update(self, event) -> None:  # type: ignore[no-untyped-def]
        super().update(event)
        if isinstance(event, type(self)):
            self.rrule = event.rrule
            self.exdate = event.exdate
            self.until = event.until
            self.start_timezone = event.start_timezone


class RecurringEventOverride(Event):
    """
    Represents an individual one-off instance of a recurring event,
    including cancelled events.
    """

    id = Column(ForeignKey("event.id", ondelete="CASCADE"), primary_key=True)
    __mapper_args__ = {
        "polymorphic_identity": "recurringeventoverride",
        "inherit_condition": (id == Event.id),
    }
    __table_args__ = None  # type: ignore[assignment]

    master_event_id = Column(ForeignKey("event.id", ondelete="CASCADE"))
    master_event_uid = Column(
        String(UID_MAX_LEN, collation="ascii_general_ci"), index=True
    )
    original_start_time = Column(FlexibleDateTime)
    master = relationship(
        RecurringEvent,
        foreign_keys=[master_event_id],
        backref=backref(
            "overrides", lazy="dynamic", cascade="all, delete-orphan"
        ),
    )

    @validates("master_event_uid")
    def validate_master_event_uid_length(  # type: ignore[no-untyped-def]  # noqa: ANN201
        self, key, value
    ):
        if value is None:
            return None
        return unicode_safe_truncate(value, MAX_LENS[key])

    def update(self, event) -> None:  # type: ignore[no-untyped-def]
        super().update(event)
        if isinstance(event, type(self)):
            self.master_event_uid = event.master_event_uid
            self.original_start_time = event.original_start_time
        self.recurrence = None  # These single instances don't recur


class InflatedEvent(Event):
    """
    This represents an individual instance of a recurring event, generated
    on the fly when a recurring event is expanded.
    These are transient objects that should never be committed to the
    database.
    """  # noqa: D404

    __mapper_args__ = {"polymorphic_identity": "inflatedevent"}
    __tablename__ = "event"
    __table_args__ = {"extend_existing": True}  # type: ignore[assignment]

    def __init__(  # type: ignore[no-untyped-def]
        self, event, instance_start
    ) -> None:
        self.master = event
        self.update(self.master)
        self.read_only = True  # Until we support modifying inflated events
        # Give inflated events a UID consisting of the master UID and the
        # original UTC start time of the inflation.
        ts_id = instance_start.strftime("%Y%m%dT%H%M%SZ")
        self.uid = f"{self.master.uid}_{ts_id}"
        self.public_id = f"{self.master.public_id}_{ts_id}"
        self.set_start_end(instance_start)

    def set_start_end(self, start) -> None:  # type: ignore[no-untyped-def]
        # get the length from the master event
        length = self.master.length
        self.start = start.to("utc")
        self.end = self.start + length

    def update(self, master) -> None:  # type: ignore[no-untyped-def]
        super().update(master)
        self.namespace_id = master.namespace_id
        self.calendar_id = master.calendar_id

        # Our calendar autoimport code sometimes creates recurring events.
        # When expanding those events, their inflated events are associated
        # with an existing message. Because of this, SQLAlchemy tries
        # to flush them, which we forbid.
        # There's no real good way to prevent this, so we set to None
        # the reference to message. API users can still look up
        # the master event if they want to know the message associated with
        # this recurring event.
        self.message_id = None
        self.message = None


def insert_warning(  # type: ignore[no-untyped-def]
    mapper, connection, target
) -> Never:
    log.warning(f"InflatedEvent {target} shouldn't be committed")
    raise Exception("InflatedEvent should not be committed")


event.listen(InflatedEvent, "before_insert", insert_warning)
