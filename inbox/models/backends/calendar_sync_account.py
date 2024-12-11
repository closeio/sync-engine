from datetime import datetime, timedelta

from sqlalchemy import Column, DateTime


class CalendarSyncAccountMixin:
    last_calendar_list_sync = Column(DateTime)
    webhook_calendar_list_last_ping = Column(DateTime)
    webhook_calendar_list_expiration = Column(DateTime)

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
                    self.last_calendar_list_sync
                    < self.webhook_calendar_list_last_ping
                )
            )
        )

    def needs_new_calendar_list_watch(self) -> bool:
        return (
            self.webhook_calendar_list_expiration is None
            or self.webhook_calendar_list_expiration < datetime.utcnow()
        )
