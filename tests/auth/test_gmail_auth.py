import copy

import attr
import mock
import pytest

from inbox.auth.google import GoogleAccountData, GoogleAuthHandler
from inbox.basicauth import ImapSupportDisabledError
from inbox.models.account import Account
from inbox.models.secret import SecretType

account_data = GoogleAccountData(
    email="t.est@gmail.com",
    secret_type=SecretType.Token,
    secret_value="MyRefreshToken",
    client_id="",
    scope="a b",
    sync_email=True,
    sync_contacts=False,
    sync_events=True,
)


@pytest.fixture
def patched_gmail_client(monkeypatch):
    def raise_exc(*args, **kwargs):
        raise ImapSupportDisabledError()

    monkeypatch.setattr("inbox.crispin.GmailCrispinClient.__init__", raise_exc)


def test_create_account(db):
    handler = GoogleAuthHandler()

    # Create an account
    account = handler.create_account(account_data)
    db.session.add(account)
    db.session.commit()
    # Verify its settings
    id_ = account.id
    account = db.session.query(Account).get(id_)
    assert account.email_address == account_data.email
    assert account.sync_email == account_data.sync_email
    assert account.sync_contacts == account_data.sync_contacts
    assert account.sync_events == account_data.sync_events
    # Ensure that the emailed events calendar was created
    assert account._emailed_events_calendar is not None
    assert account._emailed_events_calendar.name == "Emailed events"


def test_update_account(db):
    handler = GoogleAuthHandler()

    # Create an account
    account = handler.create_account(account_data)
    db.session.add(account)
    db.session.commit()
    id_ = account.id

    # Verify it is updated correctly.
    updated_data = attr.evolve(account_data, secret_value="NewRefreshToken")
    account = handler.update_account(account, updated_data)
    db.session.add(account)
    db.session.commit()
    account = db.session.query(Account).get(id_)
    assert account.refresh_token == "NewRefreshToken"
