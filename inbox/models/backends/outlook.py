from sqlalchemy import Column, String, ForeignKey

from inbox.models.backends.imap import ImapAccount
from inbox.models.backends.oauth import OAuthAccount

PROVIDER = "_outlook"


class OutlookAccount(ImapAccount, OAuthAccount):
    id = Column(ForeignKey(ImapAccount.id, ondelete="CASCADE"), primary_key=True)

    __mapper_args__ = {"polymorphic_identity": "outlookaccount"}

    # STOPSHIP(emfree) store these either as secrets or as properties of the
    # developer app.
    client_id = Column(String(256))
    client_secret = Column(String(256))
    scope = Column(String(512))
    family_name = Column(String(256))
    given_name = Column(String(256))
    gender = Column(String(16))
    o_id = Column(String(32))  # `id`
    o_id_token = Column(String(1024))  # `id_token`
    link = Column(String(256))
    locale = Column(String(8))

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
