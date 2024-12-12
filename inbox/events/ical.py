import quopri
import sys
import traceback
from datetime import date, datetime
from email.utils import formataddr
from typing import TYPE_CHECKING, Literal

import arrow
import icalendar
import pytz
import requests
from flanker import mime
from html2text import html2text
from icalendar import Calendar as iCalendar
from sqlalchemy.orm import Session

from inbox.config import config
from inbox.contacts.processing import update_contacts_from_event
from inbox.events.util import MalformedEventError
from inbox.logging import get_logger
from inbox.models.action_log import schedule_action
from inbox.models.event import EVENT_STATUSES, Event
from inbox.util.addr import canonicalize_address

from .timezones import timezones_table
from .util import serialize_datetime, valid_base36

if TYPE_CHECKING:
    from inbox.models.account import Account
    from inbox.models.message import Message

log = get_logger()


STATUS_MAP = {
    "NEEDS-ACTION": "noreply",
    "ACCEPTED": "yes",
    "DECLINED": "no",
    "TENTATIVE": "maybe",
}
INVERTED_STATUS_MAP = {value: key for key, value in STATUS_MAP.items()}


def normalize_repeated_component(
    component: list[str] | str | None,
) -> str | None:
    """
    Some software can repeat components several times.
    We can safely recover from it if all of them have the same value.
    """
    if component is None:
        return None
    elif isinstance(component, str):
        return component
    elif isinstance(component, list) and set(component) == {component[0]}:
        return component[0]
    else:
        raise MalformedEventError("Cannot normalize component", component)


def events_from_ics(namespace, calendar, ics_str):
    try:
        cal = iCalendar.from_ical(ics_str)
    except (ValueError, IndexError, KeyError, TypeError) as e:
        raise MalformedEventError("Error while parsing ICS file") from e

    events: dict[Literal["invites", "rsvps"], list[Event]] = dict(
        invites=[], rsvps=[]
    )

    # See: https://tools.ietf.org/html/rfc5546#section-3.2
    calendar_method = None

    for component in cal.walk():
        if component.name == "VCALENDAR":
            calendar_method = normalize_repeated_component(
                component.get("method")
            )
            # Return early if we see calendar_method we don't care about
            if calendar_method not in ["REQUEST", "CANCEL", "REPLY"]:
                return events

        if component.name == "VTIMEZONE":
            tzname = component.get("TZID")
            if tzname not in timezones_table:
                raise MalformedEventError(
                    "Non-UTC timezone should be in table", tzname
                )

        if component.name == "VEVENT":
            # Make sure the times are in UTC.
            try:
                original_start = component.get("dtstart").dt
            except AttributeError:
                raise MalformedEventError("Event lacks one of DTSTART")

            if component.get("dtend"):
                original_end = component["dtend"].dt
            elif component.get("duration"):
                original_end = original_start + component["duration"].dt
            else:
                original_end = original_start

            start = original_start
            end = original_end
            original_start_tz = None

            all_day = False
            if isinstance(start, datetime) and isinstance(end, datetime):
                tzid = str(original_start.tzinfo)
                original_start_tz = timezones_table.get(tzid)

                if original_start.tzinfo is None:
                    tzid = component.get("dtstart").params.get("TZID", None)
                    if tzid not in timezones_table:
                        raise MalformedEventError(
                            "Non-UTC timezone should be in table", tzid
                        )

                    corresponding_tz = timezones_table[tzid]
                    original_start_tz = corresponding_tz

                    local_timezone = pytz.timezone(corresponding_tz)
                    original_start = local_timezone.localize(original_start)

                if original_end.tzinfo is None:
                    tzid = component.get("dtend").params.get("TZID", None)
                    if tzid not in timezones_table:
                        raise MalformedEventError(
                            "Non-UTC timezone should be in table", tzid
                        )

                    corresponding_tz = timezones_table[tzid]
                    local_timezone = pytz.timezone(corresponding_tz)
                    original_end = local_timezone.localize(original_end)

                # Now that we have tz-aware datetimes, convert them to UTC
                start = original_start.astimezone(pytz.UTC)
                end = original_end.astimezone(pytz.UTC)

            elif isinstance(start, date) and isinstance(end, date):
                all_day = True
                start = arrow.get(start)
                end = arrow.get(end)

            assert isinstance(
                start, type(end)
            ), "Start and end should be of the same type"

            # Get the last modification date.
            # Exchange uses DtStamp, iCloud and Gmail LAST-MODIFIED.
            component_dtstamp = component.get("dtstamp")
            component_last_modified = component.get("last-modified")
            last_modified = None

            if component_dtstamp is not None:
                # This is one surprising instance of Exchange doing
                # the right thing by giving us an UTC timestamp. Also note that
                # Google calendar also include the DtStamp field, probably to
                # be a good citizen.
                last_modified = component_dtstamp.dt
                # Acording to https://www.rfc-editor.org/rfc/rfc5545#section-3.8.7.2
                # DTSTAMP value MUST be specified in the UTC time format
                # so it should always go with 'Z' at the end. There is software
                # in the wild that interprets it as not needing 'Z' and implicitely
                # UTC
                if last_modified.tzinfo is None:
                    last_modified = pytz.UTC.localize(last_modified)
            elif component_last_modified is not None:
                # Try to look for a LAST-MODIFIED element instead.
                # Note: LAST-MODIFIED is always in UTC.
                # http://www.kanzaki.com/docs/ical/lastModified.html
                last_modified = component_last_modified.dt

            title = None
            summaries = component.get("summary", [])
            if not isinstance(summaries, list):
                summaries = [summaries]

            if summaries != []:
                title = " - ".join(summaries)

            description = component.get("description")
            if description is not None:
                description = str(description)

            event_status = normalize_repeated_component(
                component.get("status")
            )
            if event_status is not None:
                event_status = event_status.lower()
            else:
                # Some providers (e.g: iCloud) don't use the status field.
                # Instead they use the METHOD field to signal cancellations.
                method = component.get("method")
                if method and method.lower() == "cancel":
                    event_status = "cancelled"
                elif calendar_method and calendar_method.lower() == "cancel":
                    # So, this particular event was not cancelled. Maybe the
                    # whole calendar was.
                    event_status = "cancelled"
                else:
                    # Otherwise assume the event has been confirmed.
                    event_status = "confirmed"

            if event_status not in EVENT_STATUSES:
                raise MalformedEventError("Bad event status", event_status)

            recur = component.get("rrule")
            if recur:
                recur = f"RRULE:{recur.to_ical().decode()}"

            participants = []

            organizer = component.get("organizer")
            organizer_name = None
            organizer_email = None
            if organizer:
                organizer_email = str(organizer)
                if organizer_email.lower().startswith("mailto:"):
                    organizer_email = organizer_email[7:]

                organizer_name = organizer.params.get("CN", organizer_name)
                if isinstance(organizer_name, list):
                    organizer_name = ",".join(organizer_name)

                owner = formataddr((organizer_name, organizer_email.lower()))
            else:
                owner = None

            is_owner = False
            if owner is not None and (
                namespace.account.email_address
                == canonicalize_address(organizer_email)
            ):
                is_owner = True

            attendees = component.get("attendee", [])

            # the iCalendar python module doesn't return a list when
            # there's only one attendee. Go figure.
            if not isinstance(attendees, list):
                attendees = [attendees]

            for attendee in attendees:
                email = str(attendee)
                # strip mailto: if it exists
                if email.lower().startswith("mailto:"):
                    email = email[7:]
                try:
                    name = attendee.params["CN"]
                except KeyError:
                    name = None

                status_map = {
                    "NEEDS-ACTION": "noreply",
                    "ACCEPTED": "yes",
                    "DECLINED": "no",
                    "TENTATIVE": "maybe",
                }
                status = "noreply"
                try:
                    a_status = attendee.params["PARTSTAT"]
                    status = status_map[a_status]
                except KeyError:
                    pass

                notes = None
                try:
                    guests = attendee.params["X-NUM-GUESTS"]
                    notes = f"Guests: {guests}"
                except KeyError:
                    pass

                participants.append(
                    {
                        "email": email.lower(),
                        "name": name,
                        "status": status,
                        "notes": notes,
                        "guests": [],
                    }
                )

            location = component.get("location")
            uid = str(component.get("uid"))
            # It seems some ICS files don't follow common sense and
            # use non-ASCII characters in uids. We are gonna use
            # quoted-printable to ensure those are always ASCII.
            # Note that quoted-printable also splits long strings
            # into several lines with `"=\n"` and we don't want that
            uid = (
                quopri.encodestring(uid.encode("utf-8"))
                .decode("ascii")
                .replace("=\n", "")
            )

            sequence_number = int(component.get("sequence", 0))

            # Some services (I'm looking at you, http://www.foogi.me/)
            # don't follow the spec and generate icalendar files with
            # ridiculously big sequence numbers. Truncate them to fit in
            # our db.
            sequence_number = min(sequence_number, 2147483647)

            event = Event.create(
                namespace=namespace,
                calendar=calendar,
                uid=uid,
                provider_name="ics",
                raw_data=component.to_ical(),
                title=title,
                description=description,
                location=location,
                reminders=str([]),
                recurrence=recur,
                start=start,
                end=end,
                busy=True,
                all_day=all_day,
                read_only=True,
                owner=owner,
                is_owner=is_owner,
                last_modified=last_modified,
                original_start_tz=original_start_tz,
                source="local",
                status=event_status,
                sequence_number=sequence_number,
                participants=participants,
            )

            # We need to distinguish between invites/updates/cancellations
            # and RSVPs.
            if calendar_method in ["REQUEST", "CANCEL"]:
                events["invites"].append(event)
            elif calendar_method == "REPLY":
                events["rsvps"].append(event)

    return events


def process_invites(db_session, message, account, invites):
    new_uids = [event.uid for event in invites]

    # Get the list of events which share a uid with those we received.
    # Note that we're limiting this query to events in the 'emailed events'
    # calendar, because that's where all the invites go.
    existing_events = (
        db_session.query(Event)
        .filter(
            Event.calendar_id == account.emailed_events_calendar_id,
            Event.namespace_id == account.namespace.id,
            Event.uid.in_(new_uids),
        )
        .all()
    )

    existing_events_table = {event.uid: event for event in existing_events}

    for event in invites:
        if event.uid not in existing_events_table:
            # This is some SQLAlchemy trickery -- the events returned
            # by events_from_ics aren't bound to a session yet. Because of
            # this, we don't care if they get garbage-collected. This is
            # important because we only want to keep events we haven't seen
            # yet --- updates are merged with the existing events and are
            # dropped immediately afterwards.
            # By associating the event to the message we make sure it
            # will be flushed to the db.
            event.calendar = account.emailed_events_calendar
            event.message = message

            db_session.flush()
            update_contacts_from_event(db_session, event, account.namespace.id)
        else:
            # This is an event we already have in the db.
            # Let's see if the version we have is older or newer.
            existing_event = existing_events_table[event.uid]

            if existing_event.sequence_number <= event.sequence_number:
                merged_participants = (
                    existing_event._partial_participants_merge(event)
                )

                existing_event.update(event)
                existing_event.message = message

                # We have to do this mumbo-jumbo because MutableList does
                # not register changes to nested elements.
                # We could probably change MutableList to handle it (see:
                # https://groups.google.com/d/msg/sqlalchemy/i2SIkLwVYRA/mp2WJFaQxnQJ)
                # but this sounds very brittle.
                existing_event.participants = []
                for participant in merged_participants:
                    existing_event.participants.append(participant)

                db_session.flush()
                update_contacts_from_event(
                    db_session, existing_event, account.namespace.id
                )


def _cleanup_nylas_uid(uid):
    uid = uid.lower()
    if "@nylas.com" in uid:
        return uid[:-10]

    return uid


def process_nylas_rsvps(db_session, message, account, rsvps):
    # The invite sending code generates invites with uids of the form
    # `public_id@nylas.com`. We couldn't use Event.uid for this because
    # it wouldn't work with Exchange (Exchange uids are of the form
    # 1:2323 and aren't guaranteed to be unique).
    new_uids = [
        _cleanup_nylas_uid(event.uid)
        for event in rsvps
        if "@nylas.com" in event.uid
    ]

    # Drop uids which aren't base36 uids.
    new_uids = [uid for uid in new_uids if valid_base36(uid)]

    # Get the list of events which share a uid with those we received.
    # Note that we're excluding events from "Emailed events" because
    # we don't want to process RSVPs to invites we received.
    existing_events = (
        db_session.query(Event)
        .filter(
            Event.namespace_id == account.namespace.id,
            Event.calendar_id != account.emailed_events_calendar_id,
            Event.public_id.in_(new_uids),
        )
        .all()
    )

    existing_events_table = {
        event.public_id: event for event in existing_events
    }

    for event in rsvps:
        event_uid = _cleanup_nylas_uid(event.uid)
        if event_uid not in existing_events_table:
            # We've received an RSVP to an event we never heard about. Save it,
            # maybe we'll sync the invite later.
            event.message = message
        else:
            # This is an event we already have in the db.
            existing_event = existing_events_table[event_uid]

            # Is the current event an update?
            if existing_event.sequence_number == event.sequence_number:
                merged_participants = (
                    existing_event._partial_participants_merge(event)
                )

                # We have to do this mumbo-jumbo because MutableList does
                # not register changes to nested elements.
                # We could probably change MutableList to handle it (see:
                # https://groups.google.com/d/msg/sqlalchemy/i2SIkLwVYRA/mp2WJFaQxnQJ)
                # but it seems very brittle.
                existing_event.participants = []
                for participant in merged_participants:
                    existing_event.participants.append(participant)

                # We need to sync back changes to the event manually
                if existing_event.calendar != account.emailed_events_calendar:
                    schedule_action(
                        "update_event",
                        existing_event,
                        existing_event.namespace.id,
                        db_session,
                        calendar_uid=existing_event.calendar.uid,
                    )

                db_session.flush()


def import_attached_events(
    db_session: Session, account: "Account", message: "Message"
) -> None:
    """Import events from a file into the 'Emailed events' calendar."""
    assert account is not None

    for part in message.attached_event_files:
        part_data = ""
        try:
            part_data = part.block.data
            if part_data == "":
                continue

            new_events = events_from_ics(
                account.namespace, account.emailed_events_calendar, part_data
            )
        except MalformedEventError:
            log.error(
                "Attached event parsing error",
                account_id=account.id,
                message_id=message.id,
                logstash_tag="icalendar_autoimport",
                event_part_id=part.id,
            )
            continue
        except (
            AssertionError,
            TypeError,
            RuntimeError,
            AttributeError,
            ValueError,
            LookupError,
            ImportError,
            NameError,
        ):
            # Kind of ugly but we don't want to derail message
            # creation because of an error in the attached calendar.
            log.error(
                "Unhandled exception during message parsing",
                message_id=message.id,
                event_part_id=part.id,
                logstash_tag="icalendar_autoimport",
                traceback=traceback.format_exception(
                    sys.exc_info()[0], sys.exc_info()[1], sys.exc_info()[2]
                ),
            )
            continue

        process_invites(db_session, message, account, new_events["invites"])

        # Gmail has a very very annoying feature: it doesn't use email to RSVP
        # to an invite sent by another gmail account. This makes it impossible
        # for us to update the event correctly. To work around this we let the
        # Gmail API handle invite sending. For other providers we process this
        # ourselves.
        # - karim
        if account.provider != "gmail":
            process_nylas_rsvps(
                db_session, message, account, new_events["rsvps"]
            )


def generate_icalendar_invite(event, invite_type="request"):
    # Generates an iCalendar invite from an event.
    assert invite_type in ["request", "cancel"]

    cal = iCalendar()
    cal.add("PRODID", "-//Nylas sync engine//nylas.com//")

    if invite_type in ["request", "update"]:
        cal.add("METHOD", "REQUEST")
    elif invite_type == "cancel":
        cal.add("METHOD", "CANCEL")

    cal.add("VERSION", "2.0")
    cal.add("CALSCALE", "GREGORIAN")

    icalendar_event = icalendar.Event()

    account = event.namespace.account
    organizer = icalendar.vCalAddress(f"MAILTO:{account.email_address}")
    if account.name is not None and account.name != "":
        organizer.params["CN"] = account.name

    icalendar_event["organizer"] = organizer
    icalendar_event["sequence"] = str(event.sequence_number)
    icalendar_event["X-MICROSOFT-CDO-APPT-SEQUENCE"] = icalendar_event[
        "sequence"
    ]

    if invite_type == "cancel":
        icalendar_event["status"] = "CANCELLED"
    else:
        icalendar_event["status"] = "CONFIRMED"

    icalendar_event["uid"] = f"{event.public_id}@nylas.com"
    icalendar_event["description"] = event.description or ""
    icalendar_event["summary"] = event.title or ""
    icalendar_event["last-modified"] = serialize_datetime(event.updated_at)
    icalendar_event["dtstamp"] = icalendar_event["last-modified"]
    icalendar_event["created"] = serialize_datetime(event.created_at)
    icalendar_event["dtstart"] = serialize_datetime(event.start)
    icalendar_event["dtend"] = serialize_datetime(event.end)
    icalendar_event["transp"] = "OPAQUE" if event.busy else "TRANSPARENT"
    icalendar_event["location"] = event.location or ""

    attendees = []
    for participant in event.participants:
        email = participant.get("email", None)

        # FIXME @karim: handle the case where a participant has no address.
        # We may have to patch the iCalendar module for this.
        assert email is not None and email != ""

        attendee = icalendar.vCalAddress(f"MAILTO:{email}")
        name = participant.get("name", None)
        if name is not None:
            attendee.params["CN"] = name

        attendee.params["RSVP"] = "TRUE"
        attendee.params["ROLE"] = "REQ-PARTICIPANT"
        attendee.params["CUTYPE"] = "INDIVIDUAL"

        status = participant.get("status", "noreply")
        attendee.params["PARTSTAT"] = INVERTED_STATUS_MAP.get(status)
        attendees.append(attendee)

    if attendees != []:
        icalendar_event.add("ATTENDEE", attendees)

    cal.add_component(icalendar_event)
    return cal


def generate_invite_message(ical_txt, event, account, invite_type="request"):
    assert invite_type in ["request", "update", "cancel"]
    html_body = event.description or ""

    text_body = html2text(html_body)
    msg = mime.create.multipart("mixed")

    body = mime.create.multipart("alternative")

    if invite_type in ["request", "update"]:
        body.append(
            mime.create.text("plain", text_body),
            mime.create.text("html", html_body),
            mime.create.text(
                "calendar; method=REQUEST", ical_txt, charset="utf8"
            ),
        )
        msg.append(body)
    elif invite_type == "cancel":
        body.append(
            mime.create.text("plain", text_body),
            mime.create.text("html", html_body),
            mime.create.text(
                "calendar; method=CANCEL", ical_txt, charset="utf8"
            ),
        )
        msg.append(body)

    # From should match our mailsend provider (mailgun) so it doesn't confuse
    # spam filters
    msg.headers["From"] = "automated@notifications.nylas.com"
    msg.headers["Reply-To"] = account.email_address

    if invite_type == "request":
        msg.headers["Subject"] = f"Invitation: {event.title}"
    elif invite_type == "update":
        msg.headers["Subject"] = f"Updated Invitation: {event.title}"
    elif invite_type == "cancel":
        msg.headers["Subject"] = f"Cancelled: {event.title}"

    return msg


def send_invite(ical_txt, event, account, invite_type="request"):
    # We send those transactional emails through a separate domain.
    MAILGUN_API_KEY = config.get("NOTIFICATIONS_MAILGUN_API_KEY")
    MAILGUN_DOMAIN = config.get("NOTIFICATIONS_MAILGUN_DOMAIN")
    assert MAILGUN_DOMAIN is not None and MAILGUN_API_KEY is not None

    for participant in event.participants:
        email = participant.get("email", None)
        if email is None:
            continue

        if email == account.email_address:
            # If the organizer is among the participants, don't send
            # a second email. They already have the event on their
            # calendar.
            continue

        msg = generate_invite_message(ical_txt, event, account, invite_type)
        msg.headers["To"] = email
        final_message = msg.to_string()

        mg_url = f"https://api.mailgun.net/v3/{MAILGUN_DOMAIN}/messages.mime"
        r = requests.post(
            mg_url,
            auth=("api", MAILGUN_API_KEY),
            data={"to": email},
            files={"message": final_message},
        )

        if r.status_code != 200:
            log.error(
                "Couldnt send invite email for",
                email_address=email,
                event_id=event.id,
                account_id=account.id,
                logstash_tag="invite_sending",
                status_code=r.status_code,
            )


def _generate_rsvp(status, account, event):
    # It seems that Google Calendar requires us to copy a number of fields
    # in the RVSP reply. I suppose it's for reconciling the reply with the
    # invite. - karim
    cal = iCalendar()
    cal.add("PRODID", "-//Nylas sync engine//nylas.com//")
    cal.add("METHOD", "REPLY")
    cal.add("VERSION", "2.0")
    cal.add("CALSCALE", "GREGORIAN")

    icalevent = icalendar.Event()
    icalevent["uid"] = event.uid

    if event.organizer_email is not None:
        icalevent["organizer"] = event.organizer_email

    icalevent["sequence"] = event.sequence_number
    icalevent["X-MICROSOFT-CDO-APPT-SEQUENCE"] = icalevent["sequence"]

    if event.status == "confirmed":
        icalevent["status"] = "CONFIRMED"

    icalevent["dtstamp"] = serialize_datetime(datetime.utcnow())

    if event.start is not None:
        icalevent["dtstart"] = serialize_datetime(event.start)

    if event.end is not None:
        icalevent["dtend"] = serialize_datetime(event.end)

    if event.description is not None:
        icalevent["description"] = event.description

    if event.location is not None:
        icalevent["location"] = event.location

    if event.title is not None:
        icalevent["summary"] = event.title

    attendee = icalendar.vCalAddress(f"MAILTO:{account.email_address}")
    attendee.params["cn"] = account.name
    attendee.params["partstat"] = status
    icalevent.add("attendee", attendee, encode=0)
    cal.add_component(icalevent)

    return {"cal": cal}


def generate_rsvp(event, participant, account):
    # Generates an iCalendar file to RSVP to an invite.
    status = INVERTED_STATUS_MAP.get(participant["status"])
    return _generate_rsvp(status, account, event)


# Get the email address we should be RSVPing to.
# We try to find the organizer address from the iCal file.
# If it's not defined, we try to return the invite sender's
# email address.
def rsvp_recipient(event):
    if event is None:
        return None

    # A stupid bug made us create some db entries of the
    # form "None <None>".
    if event.organizer_email not in [None, "None"]:
        return event.organizer_email

    if (
        event.message is not None
        and event.message.from_addr is not None
        and len(event.message.from_addr) == 1
    ):
        from_addr = event.message.from_addr[0][1]
        if from_addr is not None and from_addr != "":
            return from_addr

    return None


def send_rsvp(ical_data, event, body_text, status, account):
    from inbox.sendmail.base import SendMailException, get_sendmail_client

    ical_file = ical_data["cal"]
    ical_txt = ical_file.to_ical()
    rsvp_to = rsvp_recipient(event)

    if rsvp_to is None:
        raise SendMailException("Couldn't find an organizer to RSVP to.", None)

    sendmail_client = get_sendmail_client(account)

    msg = mime.create.multipart("mixed")

    body = mime.create.multipart("alternative")
    body.append(
        mime.create.text("plain", ""),
        mime.create.text("calendar;method=REPLY", ical_txt),
    )

    msg.append(body)

    msg.headers["Reply-To"] = account.email_address
    msg.headers["From"] = account.email_address
    msg.headers["To"] = rsvp_to

    assert status in ["yes", "no", "maybe"]

    if status == "yes":
        msg.headers["Subject"] = f"Accepted: {event.message.subject}"
    elif status == "maybe":
        msg.headers["Subject"] = (
            f"Tentatively accepted: {event.message.subject}"
        )
    elif status == "no":
        msg.headers["Subject"] = f"Declined: {event.message.subject}"

    final_message = msg.to_string()

    sendmail_client = get_sendmail_client(account)
    sendmail_client.send_generated_email([rsvp_to], final_message)
