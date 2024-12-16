"""Provide Google Calendar events."""

import datetime
import email.utils
import json
import random
import time
import urllib.parse
import uuid
from typing import Any

import arrow  # type: ignore[import-untyped]
import attrs
import attrs.validators
import requests

from inbox.auth.oauth import OAuthRequestsWrapper
from inbox.config import config
from inbox.events.abstract import AbstractEventsProvider, CalendarGoneException
from inbox.events.util import (
    CalendarSyncResponse,
    google_to_event_time,
    parse_datetime,
    parse_google_time,
)
from inbox.exceptions import AccessNotEnabledError, OAuthError
from inbox.models import Account, Calendar
from inbox.models.backends.oauth import token_manager
from inbox.models.event import EVENT_STATUSES, Event
from inbox.util.concurrency import iterate_and_periodically_check_interrupted

CALENDARS_URL = "https://www.googleapis.com/calendar/v3/users/me/calendarList"
STATUS_MAP = {
    "accepted": "yes",
    "needsAction": "noreply",
    "declined": "no",
    "tentative": "maybe",
}

URL_PREFIX = config.get("API_URL", "")

WEBHOOK_ENABLED_CLIENT_IDS = config.get("WEBHOOK_ENABLED_CLIENT_IDS", [])

CALENDAR_LIST_WEBHOOK_URL = URL_PREFIX + "/w/calendar_list_update/{}"
EVENTS_LIST_WEBHOOK_URL = URL_PREFIX + "/w/calendar_update/{}"

WATCH_CALENDARS_URL = CALENDARS_URL + "/watch"
WATCH_EVENTS_URL = (
    "https://www.googleapis.com/calendar/v3/calendars/{}/events/watch"
)


class GoogleEventsProvider(AbstractEventsProvider):
    """
    A utility class to fetch and parse Google calendar data for the
    specified account using the Google Calendar API.
    """

    def sync_calendars(self) -> CalendarSyncResponse:
        """
        Fetch data for the user's calendars.
        """
        deletes = []
        updates = []
        items = self._get_raw_calendars()
        for item in items:
            if item.get("deleted"):
                deletes.append(item["id"])
            else:
                cal = parse_calendar_response(item)
                self.calendars_table[item["id"]] = cal.read_only
                updates.append(cal)

        return CalendarSyncResponse(deletes, updates)

    def sync_events(
        self,
        calendar_uid: str,
        sync_from_time: datetime.datetime | None = None,
    ) -> list[Event]:
        """
        Fetch event data for an individual calendar.

        Arguments:
            calendar_uid: the google identifier for the calendar.
                Usually username@gmail.com for the primary calendar, otherwise
                random-alphanumeric-address@*.google.com
            sync_from_time: datetime
                Only sync events which have been added or changed since this time.
                Note that if this is too far in the past, the Google calendar API
                may return an HTTP 410 error, in which case we transparently fetch
                all event data.

        Returns:
            A list of uncommited Event instances

        """
        updates = []
        raw_events = self._get_raw_events(calendar_uid, sync_from_time)
        read_only_calendar = self.calendars_table.get(calendar_uid, True)
        for raw_event in iterate_and_periodically_check_interrupted(
            raw_events
        ):
            try:
                parsed = parse_event_response(raw_event, read_only_calendar)
                updates.append(parsed)
            except (arrow.parser.ParserError, ValueError):
                self.log.warning(
                    "Skipping unparseable event", exc_info=True, raw=raw_event
                )

        return updates

    def _get_raw_calendars(self) -> list[dict[str, Any]]:
        """Gets raw data for the user's calendars."""  # noqa: D401
        return self._get_resource_list(CALENDARS_URL)

    def _get_raw_events(
        self,
        calendar_uid: str,
        sync_from_time: datetime.datetime | None = None,
    ) -> list[dict[str, Any]]:
        """
        Gets raw event data for the given calendar.

        Parameters
        ----------
        calendar_uid: string
            Google's ID for the calendar we're getting events on.
        sync_from_time: datetime, optional
            If given, only fetch data for events that have changed since this
            time.

        Returns
        -------
        list of dictionaries representing JSON.

        """  # noqa: D401
        if sync_from_time is not None:
            # Note explicit offset is required by Google calendar API.
            sync_from_time_str = (
                datetime.datetime.isoformat(sync_from_time) + "Z"
            )
        else:
            sync_from_time_str = None

        url = "https://www.googleapis.com/calendar/v3/calendars/{}/events".format(
            urllib.parse.quote(calendar_uid)
        )
        try:
            return self._get_resource_list(
                url, updatedMin=sync_from_time_str, eventTypes="default"
            )
        except requests.exceptions.HTTPError as exc:
            assert exc.response is not None  # noqa: PT017
            if exc.response.status_code == 410:
                # The calendar API may return 410 if you pass a value for
                # updatedMin that's too far in the past. In that case, refetch
                # all events.
                return self._get_resource_list(url)
            else:
                raise

    def _get_resource_list(  # type: ignore[no-untyped-def]
        self, url: str, **params
    ) -> list[dict[str, Any]]:
        """Handles response pagination."""  # noqa: D401
        token = self._get_access_token()
        items = []
        next_page_token: str | None = None
        params["showDeleted"] = True
        while True:
            if next_page_token is not None:
                params["pageToken"] = next_page_token
            try:
                r = requests.get(
                    url, params=params, auth=OAuthRequestsWrapper(token)
                )
                r.raise_for_status()
                data = r.json()
                items += data["items"]
                next_page_token = data.get("nextPageToken")
                if next_page_token is None:
                    return items

            except requests.exceptions.SSLError:
                self.log.warning(
                    "SSLError making Google Calendar API request, retrying.",
                    url=url,
                    exc_info=True,
                )
                time.sleep(30 + random.randrange(0, 60))
                continue
            except requests.HTTPError as e:
                self.log.warning(
                    "HTTP error making Google Calendar API request",
                    url=r.url,  # type: ignore[possibly-undefined]
                    response=r.content,  # type: ignore[possibly-undefined]
                    status=r.status_code,  # type: ignore[possibly-undefined]
                )
                if r.status_code == 401:  # type: ignore[possibly-undefined]
                    self.log.warning(
                        "Invalid access token; refreshing and retrying",
                        url=r.url,  # type: ignore[possibly-undefined]
                        response=r.content,  # type: ignore[possibly-undefined]
                        status=r.status_code,  # type: ignore[possibly-undefined]
                    )
                    token = self._get_access_token(force_refresh=True)
                    continue
                elif r.status_code in (  # type: ignore[possibly-undefined]
                    500,
                    503,
                ):
                    self.log.warning("Backend error in calendar API; retrying")
                    time.sleep(30 + random.randrange(0, 60))
                    continue
                elif r.status_code == 403:  # type: ignore[possibly-undefined]
                    try:
                        reason = r.json()[  # type: ignore[possibly-undefined]
                            "error"
                        ]["errors"][0]["reason"]
                    except (KeyError, ValueError):
                        self.log.error(
                            "Couldn't parse API error response",
                            response=r.content,  # type: ignore[possibly-undefined]
                            status=r.status_code,  # type: ignore[possibly-undefined]
                        )
                        r.raise_for_status()  # type: ignore[possibly-undefined]
                    if (
                        reason  # type: ignore[possibly-undefined]
                        == "userRateLimitExceeded"
                    ):
                        self.log.warning(
                            "API request was rate-limited; retrying"
                        )
                        time.sleep(30 + random.randrange(0, 60))
                        continue
                    elif reason in ["accessNotConfigured", "notACalendarUser"]:
                        self.log.warning(
                            f"API not enabled with reason {reason}; returning empty result"
                        )
                        raise AccessNotEnabledError() from e
                # Unexpected error; raise.
                raise

    def _make_event_request(  # type: ignore[no-untyped-def]
        self,
        method: str,
        calendar_uid: str,
        event_uid: str | None = None,
        **kwargs,
    ) -> requests.Response:
        """Makes a POST/PUT/DELETE request for a particular event."""  # noqa: D401
        event_uid = event_uid or ""
        url = "https://www.googleapis.com/calendar/v3/calendars/{}/events/{}".format(
            urllib.parse.quote(calendar_uid), urllib.parse.quote(event_uid)
        )
        token = self._get_access_token()
        response = requests.request(
            method, url, auth=OAuthRequestsWrapper(token), **kwargs
        )
        return response

    def create_remote_event(  # type: ignore[no-untyped-def]  # noqa: ANN201
        self, event, **kwargs
    ):
        data = _dump_event(event)
        params = {}

        if kwargs.get("notify_participants") is True:
            params["sendNotifications"] = "true"
        else:
            params["sendNotifications"] = "false"

        response = self._make_event_request(
            "post", event.calendar.uid, json=data, params=params
        )

        # All non-200 statuses are considered errors
        response.raise_for_status()
        return response.json()

    def update_remote_event(  # type: ignore[no-untyped-def]
        self, event, **kwargs
    ) -> None:
        data = _dump_event(event)
        params = {}

        if kwargs.get("notify_participants") is True:
            params["sendNotifications"] = "true"
        else:
            params["sendNotifications"] = "false"

        response = self._make_event_request(
            "put", event.calendar.uid, event.uid, json=data, params=params
        )

        # All non-200 statuses are considered errors
        response.raise_for_status()

    def delete_remote_event(  # type: ignore[no-untyped-def]
        self, calendar_uid, event_uid, **kwargs
    ) -> None:
        params = {}

        if kwargs.get("notify_participants") is True:
            params["sendNotifications"] = "true"
        else:
            params["sendNotifications"] = "false"

        response = self._make_event_request(
            "delete", calendar_uid, event_uid, params=params
        )

        if response.status_code == 410:
            # The Google API returns an 'HTTPError: 410 Client Error: Gone'
            # for an event that no longer exists on the remote
            self.log.warning(
                "Event no longer exists on remote",
                calendar_uid=calendar_uid,
                event_uid=event_uid,
            )
        else:
            # All other non-200 statuses are considered errors
            response.raise_for_status()

    # -------- logic for push notification subscriptions -------- #

    def _get_access_token_for_push_notifications(  # type: ignore[no-untyped-def]
        self, account, force_refresh: bool = False
    ):
        if not self.webhook_notifications_enabled(account):
            raise OAuthError("Account not enabled for push notifications.")
        return token_manager.get_token(account, force_refresh)

    def webhook_notifications_enabled(self, account: Account) -> bool:
        return (
            account.get_client_info()[0]  # type: ignore[attr-defined]
            in WEBHOOK_ENABLED_CLIENT_IDS
        )

    def watch_calendar_list(
        self, account: Account
    ) -> datetime.datetime | None:
        """
        Subscribe to google push notifications for the calendar list.


        Raises an OAuthError if no credentials are authorized to
        set up push notifications for this account.

        Raises an AccessNotEnabled error if calendar sync is not enabled

        Returns:
            The expiration of the notification channel

        """
        token = self._get_access_token_for_push_notifications(account)
        receiving_url = CALENDAR_LIST_WEBHOOK_URL.format(
            urllib.parse.quote(account.public_id)
        )

        one_week = datetime.timedelta(weeks=1)
        in_a_week = datetime.datetime.utcnow() + one_week

        # * 1000 because Google uses Unix timestamps with an ms precision.
        expiration_date = int(in_a_week.timestamp()) * 1000

        data = {
            "id": uuid.uuid4().hex,
            "type": "web_hook",
            "address": receiving_url,
            "expiration": expiration_date,
        }
        headers = {"content-type": "application/json"}
        r = requests.post(
            WATCH_CALENDARS_URL,
            data=json.dumps(data),
            headers=headers,
            auth=OAuthRequestsWrapper(token),
        )

        if r.status_code == 200:
            data = r.json()
            expiration = data.get("expiration")
            if not expiration:
                return None

            # / 1000 because Google uses Unix timestamps with an ms precision.
            return datetime.datetime.fromtimestamp(int(expiration) / 1000)
        else:
            # Handle error and return None
            self._handle_watch_errors(r)
            return None

    def watch_calendar(
        self, account: Account, calendar: Calendar
    ) -> datetime.datetime | None:
        """
        Subscribe to google push notifications for a calendar.

        Raises an OAuthError if no credentials are authorized to
        set up push notifications for this account.

        Raises an AccessNotEnabled error if calendar sync is not enabled

        Raises an HTTPError if google gives us a 404 (which implies the
        calendar was deleted)

        Returns:
            The expiration of the notification channel

        """
        token = self._get_access_token_for_push_notifications(account)
        watch_url = WATCH_EVENTS_URL.format(urllib.parse.quote(calendar.uid))
        receiving_url = EVENTS_LIST_WEBHOOK_URL.format(
            urllib.parse.quote(calendar.public_id)
        )

        one_week = datetime.timedelta(weeks=1)
        in_a_week = datetime.datetime.utcnow() + one_week

        # * 1000 because Google uses Unix timestamps with an ms precision.
        expiration_date = int(in_a_week.timestamp()) * 1000

        data = {
            "id": uuid.uuid4().hex,
            "type": "web_hook",
            "address": receiving_url,
            "expiration": expiration_date,
        }
        headers = {"content-type": "application/json"}
        try:
            r = requests.post(
                watch_url,
                data=json.dumps(data),
                headers=headers,
                auth=OAuthRequestsWrapper(token),
            )
        except requests.exceptions.SSLError:
            self.log.warning(
                "SSLError subscribing to Google push notifications",
                url=watch_url,
                exc_info=True,
            )
            return None

        if r.status_code == 200:
            data = r.json()
            expiration = data.get("expiration")
            if not expiration:
                return None

            # / 1000 because Google uses Unix timestamps with an ms precision.
            return datetime.datetime.fromtimestamp(int(expiration) / 1000)
        else:
            try:
                # Handle error and return None
                self._handle_watch_errors(r)
            except requests.exceptions.HTTPError as e:
                assert e.response is not None  # noqa: PT017
                if e.response.status_code == 404:
                    raise CalendarGoneException(calendar.uid) from e

                raise
            return None

    def _handle_watch_errors(self, r: requests.Response) -> None:
        self.log.warning(
            "Error subscribing to Google push notifications",
            url=r.url,
            response=r.content,
            status=r.status_code,
        )

        if r.status_code == 400:
            reason = r.json()["error"]["errors"][0]["reason"]
            self.log.warning(
                "Invalid request", status=r.status_code, reason=reason
            )
            if reason == "pushNotSupportedForRequestedResource":
                raise AccessNotEnabledError()
        elif r.status_code == 401:
            self.log.warning(
                "Invalid: could be invalid auth credentials",
                url=r.url,
                response=r.content,
                status=r.status_code,
            )
        elif r.status_code in (500, 503):
            self.log.warning(
                "Backend error in calendar API", status=r.status_code
            )
        elif r.status_code == 403:
            try:
                reason = r.json()["error"]["errors"][0]["reason"]
            except (KeyError, ValueError):
                self.log.error(
                    "Couldn't parse API error response",
                    response=r.content,
                    status=r.status_code,
                )
            if (
                reason  # type: ignore[possibly-undefined]
                == "userRateLimitExceeded"
            ):
                # Sleep before proceeding (naive backoff)
                time.sleep(30 + random.randrange(0, 60))
                self.log.warning("API request was rate-limited")
            elif reason == "accessNotConfigured":
                self.log.warning("API not enabled.")
                raise AccessNotEnabledError()
        elif r.status_code == 404:
            # Resource deleted!
            self.log.warning(
                "Raising exception for status",
                status_code=r.status_code,
                response=r.content,
            )
            r.raise_for_status()
        else:
            self.log.warning(
                "Unexpected error", response=r.content, status=r.status_code
            )


def parse_calendar_response(  # noqa: D417
    calendar: dict[str, Any]
) -> Calendar:
    """
    Constructs a Calendar object from a Google calendarList resource (a
    dictionary).  See
    http://developers.google.com/google-apps/calendar/v3/reference/calendarList

    Parameters
    ----------
    calendar: dict

    Returns
    -------
    A corresponding Calendar instance.

    """  # noqa: D401
    uid = calendar["id"]
    name = calendar["summary"]

    role = calendar["accessRole"]
    read_only = True
    if role in ["owner", "writer"]:
        read_only = False

    description = calendar.get("description")
    return Calendar(  # type: ignore[call-arg]
        uid=uid, name=name, read_only=read_only, description=description
    )


MAX_STRING_LENGTH = 8096
MAX_LIST_LENGTH = 10
STRING_VALIDATORS = [
    attrs.validators.instance_of(str),
    attrs.validators.max_len(MAX_STRING_LENGTH),
]


@attrs.frozen(kw_only=True)
class EntryPoint:
    uri: str = attrs.field(
        validator=STRING_VALIDATORS  # type: ignore[arg-type]
    )


@attrs.frozen(kw_only=True)
class ConferenceSolution:
    name: str = attrs.field(
        validator=STRING_VALIDATORS  # type: ignore[arg-type]
    )


@attrs.frozen(kw_only=True)
class ConferenceData:
    entry_points: list[EntryPoint] = attrs.field(
        validator=[
            attrs.validators.deep_iterable(
                attrs.validators.instance_of(EntryPoint)
            ),
            attrs.validators.max_len(MAX_LIST_LENGTH),
        ]
    )
    conference_solution: ConferenceSolution = attrs.field(
        validator=attrs.validators.instance_of(ConferenceSolution)
    )


def sanitize_conference_data(
    conference_data: dict[str, Any] | None
) -> ConferenceData | None:
    if not conference_data:
        return None

    raw_entry_points = conference_data.get("entryPoints", [])
    raw_conference_solution = conference_data.get("conferenceSolution", {})

    return ConferenceData(
        entry_points=[
            EntryPoint(uri=entry_point["uri"][:MAX_STRING_LENGTH])
            for entry_point in raw_entry_points
            if entry_point.get("uri")
        ][:MAX_LIST_LENGTH],
        conference_solution=ConferenceSolution(
            name=raw_conference_solution.get("name", "")[:MAX_STRING_LENGTH]
        ),
    )


def parse_event_response(  # noqa: D417
    event: dict[str, Any], read_only_calendar: bool
) -> Event:
    """
    Constructs an Event object from a Google event resource (a dictionary).
    See https://developers.google.com/google-apps/calendar/v3/reference/events

    Parameters
    ----------
    event: dict

    Returns
    -------
    A corresponding Event instance. This instance is not committed or added to
    a session.

    """  # noqa: D401
    uid = str(event["id"])
    # The entirety of the raw event data in json representation.
    raw_data = json.dumps(event)
    title = event.get("summary", "")

    # Timing data
    _start = event["start"]
    _end = event["end"]
    _original = event.get("originalStartTime", {})

    event_time = google_to_event_time(_start, _end)
    original_start = parse_google_time(_original)
    start_tz = _start.get("timeZone")

    last_modified = parse_datetime(event.get("updated"))

    description = event.get("description")
    conference_data = sanitize_conference_data(event.get("conferenceData"))
    location = event.get("location")
    busy = event.get("transparency") != "transparent"
    sequence = event.get("sequence", 0)

    # We're lucky because event statuses follow the icalendar
    # spec.
    event_status = event.get("status", "confirmed")
    assert event_status in EVENT_STATUSES

    # Ownership, read_only information
    creator = event.get("creator")
    organizer = event.get("organizer")
    is_owner = bool(organizer and organizer.get("self"))

    owner = ""
    if organizer:
        owner = email.utils.formataddr(
            (organizer.get("displayName", ""), organizer.get("email", ""))
        )
    elif creator:
        owner = email.utils.formataddr(
            (creator.get("displayName", ""), creator.get("email", ""))
        )

    participants = []
    attendees = event.get("attendees", [])
    for attendee in attendees:
        status = STATUS_MAP[attendee.get("responseStatus")]
        participants.append(
            {
                "email": attendee.get("email"),
                "name": attendee.get("displayName"),
                "status": status,
                "notes": attendee.get("comment"),
            }
        )

    # FIXME @karim: The right thing here would be to use Google's ACL API.
    # There's some obscure cases, like an autoimported event which guests can
    # edit that can't be modified.
    read_only = True
    if not read_only_calendar:
        read_only = False

    # Recurring master or override info
    recurrence = event.get("recurrence")
    master_uid = event.get("recurringEventId")
    cancelled = event.get("status") == "cancelled"

    visibility = event.get("visibility")

    # Rewrite some values documented in
    # https://developers.google.com/calendar/v3/reference/events
    if visibility == "default":
        visibility = None
    elif visibility == "confidential":
        visibility = "private"

    return Event.create(
        uid=uid,
        raw_data=raw_data,
        title=title,
        description=description,
        location=location,
        conference_data=(
            attrs.asdict(conference_data) if conference_data else None
        ),
        busy=busy,
        start=event_time.start,
        end=event_time.end,
        all_day=event_time.all_day,
        owner=owner,
        is_owner=is_owner,
        read_only=read_only,
        participants=participants,
        recurrence=recurrence,
        last_modified=last_modified,
        original_start_tz=start_tz,
        original_start_time=original_start,
        master_event_uid=master_uid,
        cancelled=cancelled,
        status=event_status,
        sequence_number=sequence,
        source="local",
        visibility=visibility,
    )


def _dump_event(event):  # type: ignore[no-untyped-def]
    """Convert an event db object to the Google API JSON format."""
    dump = {
        "summary": event.title,
        "description": event.description,
        "location": event.location,
        # Whether the event blocks time on the calendar.
        "transparency": ("opaque" if event.busy else "transparent"),
    }

    if event.all_day:
        dump["start"] = {"date": event.start.strftime("%Y-%m-%d")}
        dump["end"] = {"date": event.end.strftime("%Y-%m-%d")}
    else:
        dump["start"] = {
            "dateTime": event.start.isoformat("T"),
            "timeZone": "UTC",
        }
        dump["end"] = {"dateTime": event.end.isoformat("T"), "timeZone": "UTC"}

    if event.participants:
        dump["attendees"] = []
        inverse_status_map = {value: key for key, value in STATUS_MAP.items()}
        for participant in event.participants:
            attendee = {}
            if "name" in participant:
                attendee["displayName"] = participant["name"]
            if "status" in participant:
                attendee["responseStatus"] = inverse_status_map[
                    participant["status"]
                ]
            if "email" in participant:
                attendee["email"] = participant["email"]
            if "guests" in participant:
                attendee["additionalGuests"] = participant["guests"]
            if attendee:
                dump["attendees"].append(attendee)

    return dump
