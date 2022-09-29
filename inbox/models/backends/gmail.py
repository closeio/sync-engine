from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, String

from inbox.basicauth import ConnectionError, OAuthError
from inbox.config import config
from inbox.logging import get_logger
from inbox.models.backends.calendar_sync_mixin import CalendarSyncAccountMixin
from inbox.models.backends.imap import ImapAccount
from inbox.models.backends.oauth import OAuthAccount
from inbox.models.base import MailSyncBase
from inbox.models.mixins import DeletedAtMixin, UpdatedAtMixin
from inbox.models.secret import Secret
from inbox.models.session import session_scope

log = get_logger()

PROVIDER = "gmail"

GOOGLE_CALENDAR_SCOPE = "https://www.googleapis.com/auth/calendar"
GOOGLE_EMAIL_SCOPE = "https://mail.google.com/"
GOOGLE_CONTACTS_SCOPE = "https://www.google.com/m8/feeds"


class GmailAccount(OAuthAccount, ImapAccount, CalendarSyncAccountMixin):
    OAUTH_CLIENT_ID = config.get_required("GOOGLE_OAUTH_CLIENT_ID")
    OAUTH_CLIENT_SECRET = config.get_required("GOOGLE_OAUTH_CLIENT_SECRET")

    id = Column(ForeignKey(ImapAccount.id, ondelete="CASCADE"), primary_key=True)

    __mapper_args__ = {"polymorphic_identity": "gmailaccount"}

    client_id = Column(String(256))
    scope = Column(String(512))

    # for google push notifications:
    last_calendar_list_sync = Column(DateTime)
    webhook_calendar_list_last_ping = Column("gpush_calendar_list_last_ping", DateTime)
    webhook_calendar_list_subscription_expiration = Column(
        "gpush_calendar_list_expiration", DateTime
    )

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

    def get_raw_message_contents(self, message):
        from inbox.s3.backends.gmail import get_gmail_raw_contents

        return get_gmail_raw_contents(message)
