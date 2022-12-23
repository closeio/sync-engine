import abc
import datetime
from typing import Dict, List, Optional

from inbox.events.util import CalendarSyncResponse
from inbox.logging import get_logger
from inbox.models.account import Account
from inbox.models.backends.oauth import token_manager
from inbox.models.calendar import Calendar
from inbox.models.event import Event

log = get_logger()


class AbstractEventsProvider(abc.ABC):
    """
    Abstract class to fetch and parse calendar & event data for the
    specified account.
    """

    def __init__(self, account: Account):
        self.account = account
        self.namespace_id = account.namespace.id
        self.log = log.new(account_id=account.id, component="calendar sync")

        # A hash to store whether a calendar is read-only or not.
        # This is a bit of a hack because this isn't exposed at the event level
        # by the Google or Microsoft API.
        self.calendars_table: Dict[str, bool] = {}

    @abc.abstractmethod
    def sync_calendars(self) -> CalendarSyncResponse:
        """
        Fetch data for the user's calendars.
        """
        raise NotImplementedError()

    @abc.abstractmethod
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
    def webhook_notifications_enabled(self) -> bool:
        """
        Return True if webhook notifications are enabled for a given account.
        """
        raise NotImplementedError()

    @abc.abstractmethod
    def watch_calendar_list(self) -> Optional[datetime.datetime]:
        """
        Subscribe to webhook notifications for changes to calendar list.

        Returns:
            The expiration of the notification channel
        """
        raise NotImplementedError()

    @abc.abstractmethod
    def watch_calendar(self, calendar: Calendar) -> Optional[datetime.datetime]:
        """
        Subscribe to webhook notifications for changes to events in a calendar.

        Arguments:
            calendar: The calendar

        Returns:
            The expiration of the notification channel
        """
        raise NotImplementedError()

    def _get_access_token(
        self, force_refresh: bool = False, scopes: Optional[List[str]] = None
    ) -> str:
        """
        Get access token used to fetch data using APIs.

        Arguments:
            force_refresh: Whether to force refreshing the token
            scopes: Desired token scopes

        Returns:
            The token
        """
        return token_manager.get_token(
            self.account, force_refresh=force_refresh, scopes=scopes
        )


class CalendarGoneException(Exception):
    pass
