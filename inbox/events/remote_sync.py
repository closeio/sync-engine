from datetime import datetime, timedelta
from typing import Any, List, Tuple

from requests.exceptions import HTTPError

from inbox.basicauth import AccessNotEnabledError, OAuthError
from inbox.config import config
from inbox.contacts.processing import update_contacts_from_event
from inbox.events.google import URL_PREFIX, GoogleEventsProvider
from inbox.events.recurring import link_events
from inbox.logging import get_logger
from inbox.models import Calendar, Event
from inbox.models.account import Account
from inbox.models.event import RecurringEvent, RecurringEventOverride
from inbox.models.session import session_scope
from inbox.sync.base_sync import BaseSyncMonitor
from inbox.util.debug import bind_context

logger = get_logger()

EVENT_SYNC_FOLDER_ID = -2
EVENT_SYNC_FOLDER_NAME = "Events"

# Update frequency for accounts without push notifications
POLL_FREQUENCY = config.get("CALENDAR_POLL_FREQUENCY", 300)

# Update frequency for accounts with push notifications (accounts are only
# updated if there was a recent push notification).
PUSH_NOTIFICATION_POLL_FREQUENCY = 10

# How often accounts with push notifications are synced even if there was no
# push notification.
MAX_TIME_WITHOUT_SYNC = timedelta(seconds=3600)


class EventSync(BaseSyncMonitor):
    """Per-account event sync engine."""

    def __init__(
        self,
        email_address: str,
        provider_name: str,
        account_id: int,
        namespace_id: int,
        poll_frequency: int = POLL_FREQUENCY,
    ):
        bind_context(self, "eventsync", account_id)
        # Only Google for now, can easily parametrize by provider later.
        self.provider = GoogleEventsProvider(account_id, namespace_id)
        self.log = logger.new(account_id=account_id, component="calendar sync")

        BaseSyncMonitor.__init__(
            self,
            account_id,
            namespace_id,
            email_address,
            EVENT_SYNC_FOLDER_ID,
            EVENT_SYNC_FOLDER_NAME,
            provider_name,
            poll_frequency=poll_frequency,
            scope="calendar",
        )

    def sync(self) -> None:
        """Query a remote provider for updates and persist them to the
        database. This function runs every `self.poll_frequency`.
        """
        self.log.debug("syncing events")

        try:
            deleted_uids, calendar_changes = self.provider.sync_calendars()
        except AccessNotEnabledError:
            self.log.warning(
                "Access to provider calendar API not enabled; bypassing sync"
            )
            return
        with session_scope(self.namespace_id) as db_session:
            handle_calendar_deletes(
                self.namespace_id, deleted_uids, self.log, db_session
            )
            calendar_uids_and_ids = handle_calendar_updates(
                self.namespace_id, calendar_changes, self.log, db_session
            )
            db_session.commit()

        for (uid, id_) in calendar_uids_and_ids:
            # Get a timestamp before polling, so that we don't subsequently
            # miss remote updates that happen while the poll loop is executing.
            sync_timestamp = datetime.utcnow()
            with session_scope(self.namespace_id) as db_session:
                last_sync = (
                    db_session.query(Calendar.last_synced)
                    .filter(Calendar.id == id_)
                    .scalar()
                )

            event_changes = self.provider.sync_events(uid, sync_from_time=last_sync)

            with session_scope(self.namespace_id) as db_session:
                handle_event_updates(
                    self.namespace_id, id_, event_changes, self.log, db_session
                )
                cal = db_session.query(Calendar).get(id_)
                cal.last_synced = sync_timestamp
                db_session.commit()


def handle_calendar_deletes(
    namespace_id: int, deleted_calendar_uids: List[str], log: Any, db_session: Any
) -> None:
    """
    Delete any local Calendar rows with uid in `deleted_calendar_uids`. This
    delete cascades to associated events (if the calendar is gone, so are all
    of its events).

    """
    deleted_count = 0
    for uid in deleted_calendar_uids:
        local_calendar = (
            db_session.query(Calendar)
            .filter(Calendar.namespace_id == namespace_id, Calendar.uid == uid)
            .first()
        )
        if local_calendar is not None:
            _delete_calendar(db_session, local_calendar)
            deleted_count += 1
    log.info("deleted calendars", deleted=deleted_count)


def handle_calendar_updates(
    namespace_id: int, calendars, log: Any, db_session: Any
) -> List[Tuple[str, int]]:
    """Persists new or updated Calendar objects to the database."""
    ids_ = []
    added_count = 0
    updated_count = 0
    for calendar in calendars:
        assert calendar.uid is not None, "Got remote item with null uid"

        local_calendar = (
            db_session.query(Calendar)
            .filter(Calendar.namespace_id == namespace_id, Calendar.uid == calendar.uid)
            .first()
        )

        if local_calendar is not None:
            local_calendar.update(calendar)
            updated_count += 1
        else:
            local_calendar = Calendar(namespace_id=namespace_id)
            local_calendar.update(calendar)
            db_session.add(local_calendar)
            added_count += 1

        db_session.commit()
        ids_.append((local_calendar.uid, local_calendar.id))

    log.info(
        "synced added and updated calendars", added=added_count, updated=updated_count
    )
    return ids_


def handle_event_updates(
    namespace_id: int, calendar_id: int, events: List[Event], log: Any, db_session: Any
) -> None:
    """Persists new or updated Event objects to the database."""
    added_count = 0
    updated_count = 0
    existing_event_query = (
        db_session.query(Event)
        .filter(Event.namespace_id == namespace_id, Event.calendar_id == calendar_id)
        .exists()
    )
    events_exist = db_session.query(existing_event_query).scalar()
    for event in events:
        assert event.uid is not None, "Got remote item with null uid"

        local_event = None
        if events_exist:
            # Skip this lookup if there are no local events at all, for faster
            # first sync.
            local_event = (
                db_session.query(Event)
                .filter(
                    Event.namespace_id == namespace_id,
                    Event.calendar_id == calendar_id,
                    Event.uid == event.uid,
                )
                .first()
            )

        if local_event is not None:
            # We also need to mark all overrides as cancelled if we're
            # cancelling a recurring event. However, note the original event
            # may not itself be recurring (recurrence may have been added).
            if (
                isinstance(local_event, RecurringEvent)
                and event.status == "cancelled"
                and local_event.status != "cancelled"
            ):
                for override in local_event.overrides:
                    override.status = "cancelled"

            local_event.update(event)
            local_event.participants = event.participants

            updated_count += 1
        else:
            local_event = event
            local_event.namespace_id = namespace_id
            local_event.calendar_id = calendar_id
            db_session.add(local_event)
            added_count += 1

        db_session.flush()

        update_contacts_from_event(db_session, local_event, namespace_id)

        # If we just updated/added a recurring event or override, make sure
        # we link it to the right master event.
        if isinstance(event, (RecurringEvent, RecurringEventOverride)):
            link_events(db_session, event)

        # Batch commits to avoid long transactions that may lock calendar rows.
        if (added_count + updated_count) % 10 == 0:
            db_session.commit()

    log.info(
        "synced added and updated events",
        calendar_id=calendar_id,
        added=added_count,
        updated=updated_count,
    )


class GoogleEventSync(EventSync):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        with session_scope(self.namespace_id) as db_session:
            account = db_session.query(Account).get(self.account_id)
            if (
                self.provider.push_notifications_enabled(account)
                and kwargs.get("poll_frequency") is None
            ):
                # Run the sync loop more frequently if push notifications are
                # enabled. Note that we'll only update the calendar if a
                # Webhook was receicved recently, or if we haven't synced for
                # too long.
                self.poll_frequency = PUSH_NOTIFICATION_POLL_FREQUENCY

    def sync(self) -> None:
        """Query a remote provider for updates and persist them to the
        database. This function runs every `self.poll_frequency`.

        This function also handles refreshing google's push notifications
        if they are enabled for this account. Sync is bypassed if we are
        currently subscribed to push notificaitons and haven't heard anything
        new from Google.
        """
        self.log.debug("syncing events")

        try:
            if URL_PREFIX:
                self._refresh_gpush_subscriptions()
            else:
                self.log.warning(
                    "Cannot use Google push notifications (URL_PREFIX not "
                    "configured)"
                )
        except AccessNotEnabledError:
            self.log.warning(
                "Access to provider calendar API not enabled; "
                "cannot sign up for push notifications"
            )
        except OAuthError:
            # Not enough of a reason to halt the sync!
            self.log.warning(
                "Not authorized to set up push notifications for account"
                "(Safe to ignore this message if not recurring.)",
                account_id=self.account_id,
            )

        try:
            self._sync_data()
        except AccessNotEnabledError:
            self.log.warning(
                "Access to provider calendar API not enabled; bypassing sync"
            )

    def _refresh_gpush_subscriptions(self) -> None:
        with session_scope(self.namespace_id) as db_session:
            account = db_session.query(Account).get(self.account_id)

            if not self.provider.push_notifications_enabled(account):
                return

            if account.needs_new_calendar_list_watch():
                expir = self.provider.watch_calendar_list(account)
                if expir is not None:
                    account.new_calendar_list_watch(expir)

            cals_to_update = (
                cal for cal in account.namespace.calendars if cal.needs_new_watch()
            )
            for cal in cals_to_update:
                try:
                    expir = self.provider.watch_calendar(account, cal)
                    if expir is not None:
                        cal.new_event_watch(expir)
                except HTTPError as exc:
                    if exc.response.status_code == 404:
                        self.log.warning(
                            "Tried to subscribe to push notifications"
                            " for a deleted or inaccessible calendar. Deleting"
                            " local calendar",
                            calendar_id=cal.id,
                            calendar_uid=cal.uid,
                        )
                        _delete_calendar(db_session, cal)
                    else:
                        self.log.error(
                            "Error while updating calendar push notification "
                            "subscription",
                            cal_id=cal.id,
                            calendar_uid=cal.uid,
                            status_code=exc.response.status_code,
                        )
                        raise exc

    def _sync_data(self) -> None:
        with session_scope(self.namespace_id) as db_session:
            account = db_session.query(Account).get(self.account_id)
            if account.should_update_calendars(
                MAX_TIME_WITHOUT_SYNC, timedelta(seconds=POLL_FREQUENCY)
            ):
                self._sync_calendar_list(account, db_session)

            stale_calendars = (
                cal
                for cal in account.namespace.calendars
                if cal.should_update_events(
                    MAX_TIME_WITHOUT_SYNC, timedelta(seconds=POLL_FREQUENCY)
                )
            )

            # Sync user's primary calendar first. Note that the UID of the
            # primary calendar corresponds to the user's account email address.
            account_email = account.email_address
            stale_calendars_sorted = sorted(
                stale_calendars, key=lambda cal: cal.uid != account_email
            )

            for cal in stale_calendars_sorted:
                try:
                    self._sync_calendar(cal, db_session)
                except HTTPError as exc:
                    if exc.response.status_code == 404:
                        self.log.warning(
                            "Tried to sync a deleted calendar."
                            "Deleting local calendar.",
                            calendar_id=cal.id,
                            calendar_uid=cal.uid,
                        )
                        _delete_calendar(db_session, cal)
                    else:
                        self.log.error(
                            "Error while syncing calendar",
                            cal_id=cal.id,
                            calendar_uid=cal.uid,
                            status_code=exc.response.status_code,
                        )
                        raise exc

    def _sync_calendar_list(self, account: Account, db_session: Any) -> None:
        sync_timestamp = datetime.utcnow()
        deleted_uids, calendar_changes = self.provider.sync_calendars()

        handle_calendar_deletes(self.namespace_id, deleted_uids, self.log, db_session)
        handle_calendar_updates(
            self.namespace_id, calendar_changes, self.log, db_session
        )

        account.last_calendar_list_sync = sync_timestamp
        db_session.commit()

    def _sync_calendar(self, calendar: Calendar, db_session: Any) -> None:
        sync_timestamp = datetime.utcnow()
        event_changes = self.provider.sync_events(
            calendar.uid, sync_from_time=calendar.last_synced
        )

        handle_event_updates(
            self.namespace_id, calendar.id, event_changes, self.log, db_session
        )
        calendar.last_synced = sync_timestamp
        db_session.commit()


def _delete_calendar(db_session: Any, calendar: Calendar) -> None:
    """
    Delete the calendar after deleting its events in batches.

    Note we deliberately do not rely on the configured delete cascade -- doing
    so for a calendar with many events can result in the session post-flush
    processing (Transaction record creation) blocking the event loop.

    """
    for count, event in enumerate(calendar.events, start=1):
        db_session.delete(event)
        if count % 100 == 0:
            # Issue a DELETE for every 100 events.
            # This will ensure that when the DELETE for the calendar is issued,
            # the number of objects in the session and for which to create
            # Transaction records is small.
            db_session.commit()
    db_session.commit()

    # Delete the calendar
    db_session.delete(calendar)
    db_session.commit()
