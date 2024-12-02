import abc
import datetime
from typing import Dict, Iterable, List, Optional

from inbox.events.util import CalendarSyncResponse
from inbox.logging import get_logger
from inbox.models.account import Account
from inbox.models.backends.oauth import token_manager
from inbox.models.calendar import Calendar
from inbox.models.event import Event
from inbox.models.session import session_scope

log = get_logger()


class AbstractEventsProvider(abc.ABC):
    """
    Abstract class to fetch and parse calendar & event data for the
    specified account.
    """

    def __init__(self, account_id: int, namespace_id: int):
        self.account_id = account_id
        self.namespace_id = namespace_id
        self.log = log.new(account_id=account_id, component="calendar sync")

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
    ) -> Iterable[Event]:
        """
        Fetch event data for an individual calendar.

        Arguments:
                calendar_uid: the calendar identifier
                sync_from_time: Only sync events which have been added or
                    changed since this time.

        Returns:
            An iterable of uncommited Event instances
        """
        raise NotImplementedError()

    @abc.abstractmethod
    def webhook_notifications_enabled(self, account: Account) -> bool:
        """
        Return True if webhook notifications are enabled for a given account.
        """
        raise NotImplementedError()

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
        with session_scope(self.namespace_id) as db_session:
            acc = db_session.query(Account).get(self.account_id)
            # This will raise OAuthError if OAuth access was revoked. The
            # BaseSyncMonitor loop will catch this, clean up, and exit.
            return token_manager.get_token(
                acc, force_refresh=force_refresh, scopes=scopes
            )


class CalendarGoneException(Exception):
    pass
