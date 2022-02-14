""" Test that the All Mail folder is enabled for Gmail. """
import pytest

from inbox.auth.google import GoogleAuthHandler
from inbox.basicauth import GmailSettingError
from inbox.crispin import GmailCrispinClient


class AccountStub:
    id = 0
    email_address = "bob@bob.com"
    access_token = None
    imap_endpoint = None
    sync_state = "running"

    def new_token(self):
        return ("foo", 22)

    def validate_token(self, new_token):
        return True


class ConnectionStub:
    def logout(self):
        pass


def get_auth_handler(monkeypatch, folders):
    g = GoogleAuthHandler()

    def mock_connect(a):
        return ConnectionStub()

    g.get_authenticated_imap_connection = mock_connect
    monkeypatch.setattr(GmailCrispinClient, "folder_names", lambda x: folders)
    return g


def test_all_mail_missing(monkeypatch):
    """
    Test that validate_folders throws a GmailSettingError if All Mail
    is not in the list of folders.

    """
    g = get_auth_handler(monkeypatch, {"inbox": "INBOX"})
    with pytest.raises(GmailSettingError):
        g.verify_account(AccountStub())


def test_all_mail_present(monkeypatch):
    """
    Test that the validate_folders passes if All Mail is present.

    """
    g = get_auth_handler(
        monkeypatch, {"all": "ALL", "inbox": "INBOX", "trash": "TRASH"}
    )
    g.verify_account(AccountStub())
