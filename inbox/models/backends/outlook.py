from sqlalchemy import Column, ForeignKey, String

from inbox.config import config
from inbox.models.backends.imap import ImapAccount
from inbox.models.backends.oauth import OAuthAccount

PROVIDER = "microsoft"

MICROSOFT_EMAIL_SCOPES = [
    "https://outlook.office.com/IMAP.AccessAsUser.All",
    "https://outlook.office.com/SMTP.Send",
]


class OutlookAccount(ImapAccount, OAuthAccount):
    OAUTH_CLIENT_ID = config.get_required("MICROSOFT_OAUTH_CLIENT_ID")
    OAUTH_CLIENT_SECRET = config.get_required("MICROSOFT_OAUTH_CLIENT_SECRET")

    id = Column(ForeignKey(ImapAccount.id, ondelete="CASCADE"), primary_key=True)

    __mapper_args__ = {"polymorphic_identity": "outlookaccount"}

    client_id = Column(String(256))
    scope = Column(String(512))

    # TODO: These fields are unused.
    client_secret = Column(String(256))
    family_name = Column(String(256))
    given_name = Column(String(256))
    gender = Column(String(16))
    o_id = Column(String(32))  # `id`
    o_id_token = Column(String(1024))  # `id_token`
    link = Column(String(256))
    locale = Column(String(8))

    @property
    def email_scopes(self):
        return MICROSOFT_EMAIL_SCOPES

    @property
    def contacts_scopes(self):
        return None

    @property
    def calendar_scopes(self):
        return None

    @property
    def scopes(self):
        return self.email_scopes

    @property
    def provider(self):
        return PROVIDER

    @property
    def category_type(self):
        return "folder"

    @property
    def thread_cls(self):
        from inbox.models.backends.imap import ImapThread

        return ImapThread

    @property
    def actionlog_cls(self):
        from inbox.models.action_log import ActionLog

        return ActionLog
