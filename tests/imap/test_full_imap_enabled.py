from imapclient import IMAPClient
from mock import Mock


class MockIMAPClient(IMAPClient):
    def __init__(self):
        super(MockIMAPClient, self).__init__("randomhost")

    def _create_IMAP4(self):
        return Mock()

    def logout(self):
        pass
