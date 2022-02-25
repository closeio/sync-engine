from unittest.mock import Mock

from imapclient import IMAPClient

from inbox.auth.generic import GenericAuthHandler
from inbox.basicauth import UserRecoverableConfigError


class MockIMAPClient(IMAPClient):
    def __init__(self):
        super().__init__("randomhost")

    def _create_IMAP4(self):
        return Mock()

    def logout(self):
        pass
