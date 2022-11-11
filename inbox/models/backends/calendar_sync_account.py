from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import Column, DateTime


class CalendarSyncAccountMixin:

    last_calendar_list_sync = Column(DateTime)
    old_webhook_calendar_list_last_ping = Column(
        "gpush_calendar_list_last_ping", DateTime
    )
    new_webhook_calendar_list_last_ping = Column(
        "webhook_calendar_list_last_ping", DateTime
    )
    old_webhook_calendar_list_expiration = Column(
        "gpush_calendar_list_expiration", DateTime
    )
    new_webhook_calendar_list_expiration = Column(
        "webhook_calendar_list_expiration", DateTime
    )

    @property
    def webhook_calendar_list_last_ping(self) -> Optional[datetime]:
        return (
            self.new_webhook_calendar_list_last_ping
            or self.old_webhook_calendar_list_last_ping
        )

    @webhook_calendar_list_last_ping.setter
    def webhook_calendar_list_last_ping(self, value: datetime) -> None:
        self.old_webhook_calendar_last_last_ping = value
        self.new_webhook_calendar_list_last_ping = value

    @property
    def webhook_calendar_list_expiration(self) -> Optional[datetime]:
        return (
            self.new_webhook_calendar_list_expiration
            or self.old_webhook_calendar_list_expiration
        )

    @webhook_calendar_list_expiration.setter
    def webhook_calendar_list_expiration(self, value: datetime) -> None:
        self.old_webhook_calendar_list_expiration = value
        self.new_webhook_calendar_list_expiration = value

    def new_calendar_list_watch(self, expiration: datetime) -> None:
        self.webhook_calendar_list_expiration = expiration
        self.webhook_calendar_list_last_ping = datetime.utcnow()

    def handle_webhook_notification(self):
        self.webhook_calendar_list_last_ping = datetime.utcnow()

    def should_update_calendars(
        self, max_time_between_syncs: timedelta, poll_frequency: timedelta
    ) -> bool:
        """
        Arguments:
            max_time_between_syncs: The maximum amount of time we should wait
                until we sync, even if we haven't received any push notifications.

            poll_frequency: Amount of time we should wait until
                we sync if we don't have working push notifications.
        """
        now = datetime.utcnow()
        return (
            # Never synced
            self.last_calendar_list_sync is None
            or
            # Too much time has passed to not sync
            (now > self.last_calendar_list_sync + max_time_between_syncs)
            or
            # Push notifications channel is stale (and we didn't just sync it)
            (
                self.needs_new_calendar_list_watch()
                and now > self.last_calendar_list_sync + poll_frequency
            )
            or
            # Our info is stale, according to google's push notifications
            (
                self.webhook_calendar_list_last_ping is not None
                and (
                    self.last_calendar_list_sync < self.webhook_calendar_list_last_ping
                )
            )
        )

    def needs_new_calendar_list_watch(self) -> bool:
        return (
            self.webhook_calendar_list_expiration is None
            or self.webhook_calendar_list_expiration < datetime.utcnow()
        )
