import datetime
from typing import Iterable, List, Optional, cast

from inbox.events.abstract import AbstractEventsProvider
from inbox.events.microsoft.graph_client import MicrosoftGraphClient
from inbox.events.microsoft.graph_types import MsGraphCalendar, MsGraphEvent
from inbox.events.microsoft.parse import parse_calendar, parse_event
from inbox.events.util import CalendarSyncResponse
from inbox.models.account import Account
from inbox.models.calendar import Calendar
from inbox.models.event import Event


class MicrosoftEventsProvider(AbstractEventsProvider):
    def __init__(self, account_id: int, namespace_id: int):
        super().__init__(account_id, namespace_id)

        self.client = MicrosoftGraphClient(lambda: self._get_access_token())

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
        updates = []
        raw_events = cast(
            Iterable[MsGraphEvent],
            self.client.iter_events(calendar_uid, modified_after=sync_from_time),
        )
        read_only = self.calendars_table.get(calendar_uid, True)
        for raw_event in raw_events:
            event = parse_event(raw_event, read_only=read_only)

            # FIXME implement exceptions and cancellations

            updates.append(event)

        return updates

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
        raise NotImplementedError()

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
        raise NotImplementedError()
