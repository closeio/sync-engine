from sqlalchemy import (  # type: ignore[import-untyped]
    Column,
    ForeignKey,
    String,
)

from inbox.config import config
from inbox.logging import get_logger
from inbox.models.account import CategoryType
from inbox.models.backends.calendar_sync_account import (
    CalendarSyncAccountMixin,
)
from inbox.models.backends.imap import ImapAccount
from inbox.models.backends.oauth import OAuthAccount

log = get_logger()

PROVIDER = "gmail"

GOOGLE_CALENDAR_SCOPES = ["https://www.googleapis.com/auth/calendar"]
GOOGLE_EMAIL_SCOPES = ["https://mail.google.com/"]
GOOGLE_CONTACTS_SCOPES = ["https://www.google.com/m8/feeds"]


class GmailAccount(CalendarSyncAccountMixin, OAuthAccount, ImapAccount):
    OAUTH_CLIENT_ID = config.get_required("GOOGLE_OAUTH_CLIENT_ID")
    OAUTH_CLIENT_SECRET = config.get_required("GOOGLE_OAUTH_CLIENT_SECRET")

    id = Column(
        ForeignKey(ImapAccount.id, ondelete="CASCADE"), primary_key=True
    )

    __mapper_args__ = {"polymorphic_identity": "gmailaccount"}

    client_id = Column(String(256))
    scope = Column(String(512))

    @property
    def email_scopes(self):  # type: ignore[no-untyped-def]  # noqa: ANN201
        return GOOGLE_EMAIL_SCOPES

    @property
    def contacts_scopes(self):  # type: ignore[no-untyped-def]  # noqa: ANN201
        return GOOGLE_CONTACTS_SCOPES

    @property
    def calendar_scopes(self):  # type: ignore[no-untyped-def]  # noqa: ANN201
        return GOOGLE_CALENDAR_SCOPES

    @property
    def scopes(self):  # type: ignore[no-untyped-def]  # noqa: ANN201
        return [
            *self.calendar_scopes,
            *self.contacts_scopes,
            *self.email_scopes,
        ]

    @property
    def provider(self):  # type: ignore[no-untyped-def]  # noqa: ANN201
        return PROVIDER

    @property
    def category_type(self) -> CategoryType:
        return "label"

    @property
    def thread_cls(self):  # type: ignore[no-untyped-def]  # noqa: ANN201
        from inbox.models.backends.imap import ImapThread

        return ImapThread

    @property
    def actionlog_cls(self):  # type: ignore[no-untyped-def]  # noqa: ANN201
        from inbox.models.action_log import ActionLog

        return ActionLog

    def get_raw_message_contents(  # type: ignore[no-untyped-def]  # noqa: ANN201
        self, message
    ):
        from inbox.s3.backends.gmail import get_gmail_raw_contents

        return get_gmail_raw_contents(message)
