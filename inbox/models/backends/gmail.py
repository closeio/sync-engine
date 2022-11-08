from datetime import datetime, timedelta

from sqlalchemy import Column, DateTime, ForeignKey, String

from inbox.basicauth import ConnectionError, OAuthError
from inbox.config import config
from inbox.logging import get_logger
from inbox.models.backends.imap import ImapAccount
from inbox.models.backends.oauth import OAuthAccount
from inbox.models.base import MailSyncBase
from inbox.models.mixins import DeletedAtMixin, UpdatedAtMixin
from inbox.models.secret import Secret
from inbox.models.session import session_scope

log = get_logger()

PROVIDER = "gmail"

GOOGLE_CALENDAR_SCOPES = ["https://www.googleapis.com/auth/calendar"]
GOOGLE_EMAIL_SCOPES = ["https://mail.google.com/"]
GOOGLE_CONTACTS_SCOPES = ["https://www.google.com/m8/feeds"]


class GmailAccount(OAuthAccount, ImapAccount):
    OAUTH_CLIENT_ID = config.get_required("GOOGLE_OAUTH_CLIENT_ID")
    OAUTH_CLIENT_SECRET = config.get_required("GOOGLE_OAUTH_CLIENT_SECRET")

    id = Column(ForeignKey(ImapAccount.id, ondelete="CASCADE"), primary_key=True)

    __mapper_args__ = {"polymorphic_identity": "gmailaccount"}

    client_id = Column(String(256))
    scope = Column(String(512))

    # for Google webhook notifications:
    last_calendar_list_sync = Column(DateTime)
    webhook_calendar_list_last_ping = Column("gpush_calendar_list_last_ping", DateTime)
    gpush_calendar_list_expiration = Column(DateTime)

    @property
    def email_scopes(self):
        return GOOGLE_EMAIL_SCOPES

    @property
    def contacts_scopes(self):
        return GOOGLE_CONTACTS_SCOPES

    @property
    def calendar_scopes(self):
        return GOOGLE_CALENDAR_SCOPES

    @property
    def scopes(self):
        return [*self.calendar_scopes, *self.contacts_scopes, *self.email_scopes]

    @property
    def provider(self):
        return PROVIDER

    @property
    def category_type(self):
        return "label"

    @property
    def thread_cls(self):
        from inbox.models.backends.imap import ImapThread

        return ImapThread

    @property
    def actionlog_cls(self):
        from inbox.models.action_log import ActionLog

        return ActionLog

    def new_calendar_list_watch(self, expiration: datetime) -> None:
        self.gpush_calendar_list_expiration = expiration
        self.webhook_calendar_list_last_ping = datetime.utcnow()

    def handle_gpush_notification(self):
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
            self.gpush_calendar_list_expiration is None
            or self.gpush_calendar_list_expiration < datetime.utcnow()
        )

    def get_raw_message_contents(self, message):
        from inbox.s3.backends.gmail import get_gmail_raw_contents

        return get_gmail_raw_contents(message)
