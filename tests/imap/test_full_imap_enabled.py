from unittest.mock import Mock

from imapclient import IMAPClient


class MockIMAPClient(IMAPClient):
    def __init__(self):
        super().__init__("randomhost")

    def _create_IMAP4(self):
        return Mock()

    def logout(self):
        pass
