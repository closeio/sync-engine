from collections import defaultdict, namedtuple
from datetime import datetime, timedelta
from random import shuffle

from nylas.logging import get_logger
from sqlalchemy import BigInteger, Boolean, Column, DateTime, ForeignKey, String
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.orm import backref, relationship
from sqlalchemy.orm.session import object_session

from inbox.basicauth import ConnectionError, OAuthError
from inbox.config import config
from inbox.models.backends.imap import ImapAccount
from inbox.models.backends.oauth import OAuthAccount
from inbox.models.base import MailSyncBase
from inbox.models.mixins import DeletedAtMixin, UpdatedAtMixin
from inbox.models.session import session_scope

log = get_logger()

PROVIDER = "gmail"

GOOGLE_CALENDAR_SCOPE = "https://www.googleapis.com/auth/calendar"
GOOGLE_EMAIL_SCOPE = "https://mail.google.com/"
GOOGLE_CONTACTS_SCOPE = "https://www.google.com/m8/feeds"

OAUTH_CLIENT_ID = config.get_required("GOOGLE_OAUTH_CLIENT_ID")
OAUTH_CLIENT_SECRET = config.get_required("GOOGLE_OAUTH_CLIENT_SECRET")
OAUTH_REDIRECT_URI = config.get_required("GOOGLE_OAUTH_REDIRECT_URI")


class GmailAccount(OAuthAccount, ImapAccount):
    id = Column(ForeignKey(ImapAccount.id, ondelete="CASCADE"), primary_key=True)

    __mapper_args__ = {"polymorphic_identity": "gmailaccount"}

    client_id = Column(String(256))
    scope = Column(String(512))

    # XXX: These fields are not currently used.
    client_secret = Column(String(256))
    access_type = Column(String(64))
    family_name = Column(String(256))
    given_name = Column(String(256))
    gender = Column(String(16))
    g_id = Column(String(32))  # `id`
    g_id_token = Column(String(2048))  # `id_token`
    g_user_id = Column(String(32))  # `user_id`
    link = Column(String(256))
    locale = Column(String(8))
    picture = Column(String(1024))
    home_domain = Column(String(256))

    # for google push notifications:
    last_calendar_list_sync = Column(DateTime)
    gpush_calendar_list_last_ping = Column(DateTime)
    gpush_calendar_list_expiration = Column(DateTime)

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

    def get_client_info(self):
        if self.client_id is None or self.client_id == OAUTH_CLIENT_ID:
            return (OAUTH_CLIENT_ID, OAUTH_CLIENT_SECRET)
        else:
            raise OAuthError("No valid tokens.")

    def new_calendar_list_watch(self, expiration):
        # Google gives us back expiration timestamps in milliseconds
        expiration = datetime.fromtimestamp(int(expiration) / 1000.0)
        self.gpush_calendar_list_expiration = expiration
        self.gpush_calendar_list_last_ping = datetime.utcnow()

    def handle_gpush_notification(self):
        self.gpush_calendar_list_last_ping = datetime.utcnow()

    def should_update_calendars(self, max_time_between_syncs, poll_frequency):
        """
        max_time_between_syncs: a timedelta object. The maximum amount of
        time we should wait until we sync, even if we haven't received
        any push notifications

        poll_frequency: a timedelta object. Amount of time we should wait until
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
                self.gpush_calendar_list_last_ping is not None
                and (self.last_calendar_list_sync < self.gpush_calendar_list_last_ping)
            )
        )

    def needs_new_calendar_list_watch(self):
        return (
            self.gpush_calendar_list_expiration is None
            or self.gpush_calendar_list_expiration < datetime.utcnow()
        )

    def get_raw_message_contents(self, message):
        from inbox.s3.backends.gmail import get_gmail_raw_contents

        return get_gmail_raw_contents(message)
