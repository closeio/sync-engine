from datetime import datetime

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
    inspect,
)
from sqlalchemy.orm import backref, object_session, relationship

from inbox.models.base import MailSyncBase
from inbox.models.constants import MAX_INDEXABLE_LENGTH
from inbox.models.mixins import (
    DeletedAtMixin,
    HasPublicID,
    HasRevisions,
    UpdatedAtMixin,
)
from inbox.models.namespace import Namespace


class Calendar(MailSyncBase, HasPublicID, HasRevisions, UpdatedAtMixin, DeletedAtMixin):
    API_OBJECT_NAME = "calendar"
    namespace_id = Column(ForeignKey(Namespace.id, ondelete="CASCADE"), nullable=False)

    namespace = relationship(
        Namespace, load_on_pending=True, backref=backref("calendars")
    )

    name = Column(String(MAX_INDEXABLE_LENGTH), nullable=True)
    provider_name = Column(String(128), nullable=True, default="DEPRECATED")
    description = Column(Text, nullable=True)

    # A server-provided unique ID.
    uid = Column(String(767, collation="ascii_general_ci"), nullable=False)

    read_only = Column(Boolean, nullable=False, default=False)

    last_synced = Column(DateTime, nullable=True)

    webhook_last_ping = Column("gpush_last_ping", DateTime)
    webhook_subscription_expiration = Column("gpush_expiration", DateTime)

    __table_args__ = (
        UniqueConstraint("namespace_id", "provider_name", "name", "uid", name="uuid"),
    )

    @property
    def should_suppress_transaction_creation(self):
        if self in object_session(self).new or self in object_session(self).deleted:
            return False
        obj_state = inspect(self)
        return not (
            obj_state.attrs.name.history.has_changes()
            or obj_state.attrs.description.history.has_changes()
            or obj_state.attrs.read_only.history.has_changes()
        )

    def update(self, calendar):
        self.uid = calendar.uid
        self.name = calendar.name[:MAX_INDEXABLE_LENGTH]
        self.read_only = calendar.read_only
        self.description = calendar.description

    def new_event_watch(self, expiration):
        """
        Google gives us expiration as a timestamp in milliseconds
        """
        expiration = datetime.fromtimestamp(int(expiration) / 1000.0)
        self.webhook_subscription_expiration = expiration
        self.webhook_last_ping = datetime.utcnow()

    def handle_webhook_notification(self):
        self.webhook_last_ping = datetime.utcnow()

    def can_sync(self):
        if self.name == "Emailed events" and self.uid == "inbox":
            # This is our own internal calendar
            return False

        # Common to the Birthdays and Holidays calendars.
        # If you try to watch Holidays, you get a 404.
        # If you try to watch Birthdays, you get a cryptic 'Missing Title'
        # error. Thanks, Google.
        if "group.v.calendar.google.com" in self.uid:
            return False

        # If you try to watch "Phases of the Moon" or holiday calendars, you
        # get 400 ("Push notifications are not supported by this resource.")
        if self.uid == "ht3jlfaac5lfd6263ulfh4tql8@group.calendar.google.com":
            return False

        if "holiday.calendar.google.com" in self.uid:
            return False

        return True

    def needs_new_watch(self):
        if not self.can_sync():
            return False

        return (
            self.webhook_subscription_expiration is None
            or self.webhook_subscription_expiration < datetime.utcnow()
        )

    def should_update_events(self, max_time_between_syncs, poll_frequency):
        """
        max_time_between_syncs: a timedelta object. The maximum amount of
        time we should wait until we sync, even if we haven't received
        any push notifications

        poll_frequency: a timedelta object. Amount of time we should wait until
        we sync if we don't have working push notifications.
        """
        # TODO: what do we do about calendars we cannot watch?
        if not self.can_sync():
            return False

        now = datetime.utcnow()

        return (
            # Never synced
            self.last_synced is None
            or
            # Push notifications channel is stale (and we didn't just sync it)
            (self.needs_new_watch() and now > self.last_synced + poll_frequency)
            or
            # Too much time has passed not to sync
            now > self.last_synced + max_time_between_syncs
            or
            # Events are stale, according to the push notifications
            (
                self.webhook_last_ping is not None
                and self.webhook_last_ping > self.last_synced
            )
        )
