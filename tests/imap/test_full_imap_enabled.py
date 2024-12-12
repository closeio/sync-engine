from unittest.mock import Mock

from imapclient import IMAPClient


class MockIMAPClient(IMAPClient):
    def __init__(self) -> None:
        super().__init__("randomhost")

    def _create_IMAP4(self):  # noqa: N802
        return Mock()

    def logout(self) -> None:
        pass
