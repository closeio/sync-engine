from unittest import mock

import attr
import pytest
import requests

from inbox.auth.google import GoogleAccountData, GoogleAuthHandler
from inbox.auth.oauth import OAUTH_TOKEN_REQUEST_TIMEOUT
from inbox.exceptions import ConnectionError, GmailDisabledError
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
def patched_gmail_client(monkeypatch) -> None:
    def raise_exc(*args, **kwargs):
        raise GmailDisabledError()

    monkeypatch.setattr("inbox.crispin.GmailCrispinClient.__init__", raise_exc)


def test_create_account(db) -> None:
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


def test_update_account(db) -> None:
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


@pytest.mark.parametrize(
    "error",
    [
        requests.exceptions.ConnectionError(),
        requests.exceptions.ReadTimeout(),
    ],
)
def test_access_token_request_error_raises_connection_error(
    db, monkeypatch, error
) -> None:
    handler = GoogleAuthHandler()
    account = handler.create_account(account_data)
    db.session.add(account)
    db.session.commit()
    post = mock.Mock(side_effect=error)
    monkeypatch.setattr("inbox.auth.oauth.requests.post", post)

    with pytest.raises(ConnectionError):
        handler.acquire_access_token(account)

    assert post.call_args.kwargs["timeout"] == OAUTH_TOKEN_REQUEST_TIMEOUT


def test_verify_account(db, patched_gmail_client) -> None:
    handler = GoogleAuthHandler()
    handler.get_authenticated_imap_connection = lambda account: None

    # Create an account with sync_email=True
    account = handler.create_account(account_data)
    db.session.add(account)
    db.session.commit()
    assert account.sync_email is True
    # Verify an exception is raised if there is an email settings error.
    with pytest.raises(GmailDisabledError):
        handler.verify_account(account)

    # Create an account with sync_email=False
    updated_data = attr.evolve(
        account_data, email="another@gmail.com", sync_email=False
    )
    account = handler.create_account(updated_data)
    db.session.add(account)
    db.session.commit()
    assert account.sync_email is False
    # Verify an exception is NOT raised if there is an email settings error.
    account = handler.verify_account(account)
