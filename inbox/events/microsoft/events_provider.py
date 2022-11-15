import abc
import datetime
from typing import List, Optional

from inbox.events.abstract import AbstractEventsProvider
from inbox.events.util import CalendarSyncResponse
from inbox.models.account import Account
from inbox.models.calendar import Calendar
from inbox.models.event import Event


class MicrosoftEventsProvider(AbstractEventsProvider):
    def sync_calendars(self) -> CalendarSyncResponse:
        """
        Fetch data for the user's calendars.
        """
        raise NotImplementedError()

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
        raise NotImplementedError()

    @abc.abstractmethod
    def webhook_notifications_enabled(self, account: Account) -> bool:
        """
        Return True if webhook notifications are enabled for a given account.
        """
        return True

    @abc.abstractmethod
    def watch_calendar_list(self, account: Account) -> Optional[datetime.datetime]:
        """
        Subscribe to webhook notifications for changes to calendar list.

        Arguments:
            account: The account

        Returns:
            The expiration of the notification channel
        """
        raise NotImplementedError()

    @abc.abstractmethod
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
