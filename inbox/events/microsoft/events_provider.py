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
)
from inbox.events.util import CalendarSyncResponse
from inbox.models.account import Account
from inbox.models.backends.outlook import MICROSOFT_CALENDAR_SCOPES
from inbox.models.calendar import Calendar
from inbox.models.event import Event, RecurringEvent

URL_PREFIX = config.get("API_URL", "")

CALENDAR_LIST_WEBHOOK_URL = URL_PREFIX + "/w/microsoft/calendar_list_update/{}"
EVENTS_LIST_WEBHOOK_URL = URL_PREFIX + "/w/microsoft/calendar_update/{}"


MAX_RECURRING_EVENT_DURATION = datetime.timedelta(days=365)


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
        deletes: List[str] = []  # FIXME implement deletes
        updates = []

        raw_calendars = cast(Iterable[MsGraphCalendar], self.client.iter_calendars())
        for raw_calendar in raw_calendars:
            calendar = parse_calendar(raw_calendar)
            self.calendars_table[calendar.uid] = calendar.read_only
            updates.append(calendar)

        return CalendarSyncResponse(deletes, updates)

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
            event = parse_event(raw_event, read_only=read_only)
            updates.append(event)

            if isinstance(event, RecurringEvent):
                exceptions, cancellations = self._sync_event_overrides(
                    raw_event, event, read_only=read_only
                )
                updates.extend(exceptions)
                updates.extend(cancellations)

        return updates

    def _sync_event_overrides(
        self, raw_master_event: MsGraphEvent, master_event: RecurringEvent, *, read_only
    ) -> Tuple[List[MsGraphEvent], List[MsGraphEvent]]:
        assert raw_master_event["type"] == "seriesMaster"

        start = master_event.start
        end = start + MAX_RECURRING_EVENT_DURATION

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
