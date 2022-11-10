from datetime import datetime, timedelta
from typing import Optional

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
    default = Column(Boolean, nullable=True)  # only set for Outlook calendars

    # A server-provided unique ID.
    uid = Column(String(767, collation="ascii_general_ci"), nullable=False)

    read_only = Column(Boolean, nullable=False, default=False)

    last_synced = Column(DateTime, nullable=True)

    webhook_last_ping = Column(DateTime)
    webhook_subscription_expiration = Column(DateTime)

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

    def new_event_watch(self, expiration: datetime) -> None:
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

    def needs_new_watch(self) -> bool:
        if not self.can_sync():
            return False

        return (
            self.webhook_subscription_expiration is None
            or self.webhook_subscription_expiration < datetime.utcnow()
        )

    def should_update_events(
        self, max_time_between_syncs: timedelta, poll_frequency: timedelta
    ) -> bool:
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


def is_default_calendar(calendar: Calendar) -> Optional[bool]:
    """
    Determine if this is a default/primary user calendar.

    For calendars that are special "inbox" calendars which
    store events created from ICS attachments found in mail
    this is always None.

    For Google calendars the default calendar's uid is the same as account
    email address. In case of Microsoft uids are opaque and one needs
    to store it in the database on the default field.

    Arguments:
        calendar: The google, microsoft or "inbox" calendar
    """
    from inbox.models.backends.gmail import GmailAccount
    from inbox.models.backends.outlook import OutlookAccount

    if calendar.uid == "inbox":
        return None

    if isinstance(calendar.namespace.account, GmailAccount):
        return calendar.uid == calendar.namespace.account.email_address
    elif isinstance(calendar.namespace.account, OutlookAccount):
        assert calendar.default is not None
        return calendar.default
    else:
        raise NotImplementedError()
