import datetime
from typing import Iterable, List, Optional, Tuple, cast

import ciso8601
import pytz

from inbox.config import config
from inbox.events.abstract import AbstractEventsProvider
from inbox.events.microsoft.graph_client import MicrosoftGraphClient
from inbox.events.microsoft.graph_types import (
    MsGraphCalendar,
    MsGraphEvent,
    MsGraphSubscription,
)
from inbox.events.microsoft.parse import (
    calculate_exception_and_canceled_occurrences,
    parse_calendar,
    parse_event,
    validate_event,
)
from inbox.events.util import CalendarSyncResponse
from inbox.models.account import Account
from inbox.models.backends.outlook import MICROSOFT_CALENDAR_SCOPES
from inbox.models.calendar import Calendar
from inbox.models.event import Event, RecurringEvent
from inbox.models.session import session_scope

URL_PREFIX = config.get("API_URL", "")

CALENDAR_LIST_WEBHOOK_URL = URL_PREFIX + "/w/microsoft/calendar_list_update/{}"
EVENTS_LIST_WEBHOOK_URL = URL_PREFIX + "/w/microsoft/calendar_update/{}"


# Microsoft Graph supports infinite and finite recursions.
# By default recurring events are created 6 months into the
# future but you can override it of course in the UI.
# To prevent infinite or very long looping when searching
# for exceptions and cancellations we need to establish a limit.
MAX_RECURRING_EVENT_WINDOW = datetime.timedelta(days=365)


class MicrosoftEventsProvider(AbstractEventsProvider):
    def __init__(self, account_id: int, namespace_id: int):
        super().__init__(account_id, namespace_id)

        self.client = MicrosoftGraphClient(
            lambda: self._get_access_token(scopes=MICROSOFT_CALENDAR_SCOPES)
        )

    def sync_calendars(self) -> CalendarSyncResponse:
        """
        Fetch data for the user's calendars.
        """
        updates = []

        raw_calendars = list(
            cast(Iterable[MsGraphCalendar], self.client.iter_calendars())
        )
        for raw_calendar in raw_calendars:
            calendar = parse_calendar(raw_calendar)
            self.calendars_table[calendar.uid] = calendar.read_only
            updates.append(calendar)

        # Microsfot Graph API does not support fetching deleted calendars, so
        # instead we compare the calendar uids we have in the database with
        # the ones we fetched remotely
        remote_uids = [update.uid for update in updates]
        with session_scope(self.namespace_id) as db_session:
            # We need to exclude "Emailed events" calendar i.e. the one that
            # stores events parsed from email message attachements
            deleted_uids = [
                uid
                for uid, in db_session.query(Calendar.uid).filter(
                    Calendar.namespace_id == self.namespace_id,
                    Calendar.uid.not_in(remote_uids),
                )
                if uid != "inbox"
            ]

        return CalendarSyncResponse(deleted_uids, updates)

    def sync_events(
        self, calendar_uid: str, sync_from_time: Optional[datetime.datetime] = None
    ) -> List[Event]:
        """
        Fetch event data for an individual calendar.

        Arguments:
                calendar_uid: the calendar identifier
                sync_from_time: Only sync events which have been added or
                    changed since this time.

        Returns:
            A list of uncommited Event instances
        """
        if sync_from_time:
            # this got here from the database, we store them as naive
            # UTC in the database. The code downstream is timezone aware so
            # we attach timezone here.
            sync_from_time = sync_from_time.replace(tzinfo=pytz.UTC)

        updates = []
        raw_events = cast(
            Iterable[MsGraphEvent],
            self.client.iter_events(calendar_uid, modified_after=sync_from_time),
        )
        read_only = self.calendars_table.get(calendar_uid, True)
        for raw_event in raw_events:
            if not validate_event(raw_event):
                self.log.warning("Invalid event", raw_event=raw_event)
                continue

            event = parse_event(raw_event, read_only=read_only)
            updates.append(event)

            if isinstance(event, RecurringEvent):
                exceptions, cancellations = self._get_event_overrides(
                    raw_event, event, read_only=read_only
                )
                updates.extend(exceptions)
                updates.extend(cancellations)

        return updates

    def _get_event_overrides(
        self, raw_master_event: MsGraphEvent, master_event: RecurringEvent, *, read_only
    ) -> Tuple[List[MsGraphEvent], List[MsGraphEvent]]:
        """
        Fetch recurring event instances and determine exceptions and cancellations.

        Arguments:
            raw_master_event: Recurring master event as retruend by the API
            master_event: Parsed recurring master event as ORM object
            read_only: Does master event come from read-only calendar

        Returns:
            Tuple of exceptions and cancellations
        """
        assert raw_master_event["type"] == "seriesMaster"

        start = master_event.start
        end = start + MAX_RECURRING_EVENT_WINDOW

        raw_occurrences = cast(
            List[MsGraphEvent],
            list(
                self.client.iter_event_instances(master_event.uid, start=start, end=end)
            ),
        )
        (
            raw_exceptions,
            raw_cancellations,
        ) = calculate_exception_and_canceled_occurrences(
            raw_master_event, raw_occurrences, end
        )

        exceptions = [
            parse_event(
                exception, read_only=read_only, master_event_uid=master_event.uid
            )
            for exception in raw_exceptions
        ]
        cancellations = [
            parse_event(
                cancellation, read_only=read_only, master_event_uid=master_event.uid
            )
            for cancellation in raw_cancellations
        ]

        return exceptions, cancellations

    def webhook_notifications_enabled(self, account: Account) -> bool:
        """
        Return True if webhook notifications are enabled for a given account.
        """
        return True

    def watch_calendar_list(self, account: Account) -> Optional[datetime.datetime]:
        """
        Subscribe to webhook notifications for changes to calendar list.

        Arguments:
            account: The account

        Returns:
            The expiration of the notification channel
        """
        response = self.client.subscribe_to_calendar_changes(
            webhook_url=CALENDAR_LIST_WEBHOOK_URL.format(account.public_id),
            secret=config["MICROSOFT_SUBSCRIPTION_SECRET"],
        )

        expiration = cast(MsGraphSubscription, response)["expirationDateTime"]

        return ciso8601.parse_datetime(expiration).replace(microsecond=0)

    def watch_calendar(
        self, account: Account, calendar: Calendar
    ) -> Optional[datetime.datetime]:
        """
        Subscribe to webhook notifications for changes to events in a calendar.

        Arguments:
            account: The account
            calendar: The calendar

        Returns:
            The expiration of the notification channel
        """
        response = self.client.subscribe_to_event_changes(
            calendar.uid,
            webhook_url=EVENTS_LIST_WEBHOOK_URL.format(calendar.public_id),
            secret=config["MICROSOFT_SUBSCRIPTION_SECRET"],
        )

        expiration = cast(MsGraphSubscription, response)["expirationDateTime"]

        return ciso8601.parse_datetime(expiration).replace(microsecond=0)
