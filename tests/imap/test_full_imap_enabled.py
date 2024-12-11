from unittest.mock import Mock

from imapclient import IMAPClient


class MockIMAPClient(IMAPClient):
    def __init__(self) -> None:
        super().__init__("randomhost")

    def _create_IMAP4(self):
        return Mock()

    def logout(self) -> None:
        pass
