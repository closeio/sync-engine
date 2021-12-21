# -*- coding: utf-8 -*-
import socket

import attr
import pytest

from inbox.auth.generic import GenericAccountData, GenericAuthHandler
from inbox.basicauth import SettingUpdateError, ValidationError
from inbox.models.account import Account
from inbox.util.url import parent_domain

account_data = GenericAccountData(
    email="benbitdit@aol.com",
    imap_server_host="imap.aol.com",
    imap_server_port=143,
    imap_username="benbitdit@aol.com",
    imap_password="IHate2Gmail",
    smtp_server_host="smtp.aol.com",
    smtp_server_port=587,
    smtp_username="benbitdit@aol.com",
    smtp_password="IHate2Gmail",
    sync_email=True,
)


def test_create_account(db):
    handler = GenericAuthHandler()

    # Create an authenticated account
    account = handler.create_account(account_data)
    db.session.add(account)
    db.session.commit()
    # Verify its settings
    id_ = account.id
    account = db.session.query(Account).get(id_)
    assert account.imap_endpoint == (
        account_data.imap_server_host,
        account_data.imap_server_port,
    )
    assert account.smtp_endpoint == (
        account_data.smtp_server_host,
        account_data.smtp_server_port,
    )
    # Ensure that the emailed events calendar was created
    assert account._emailed_events_calendar is not None
    assert account._emailed_events_calendar.name == "Emailed events"


def test_update_account(db):
    handler = GenericAuthHandler()

    # Create an authenticated account
    account = handler.create_account(account_data)
    db.session.add(account)
    db.session.commit()
    id_ = account.id

    # A valid update
    updated_data = attr.evolve(account_data, imap_username="other@example.com")
    account = handler.update_account(account, updated_data)
    db.session.add(account)
    db.session.commit()
    account = db.session.query(Account).get(id_)
    assert account.imap_username == "other@example.com"


def test_update_account_with_different_subdomain(db, monkeypatch):
    # Check that you can update the server endpoints for an account
    # provided that
    # 1/ they're on a subdomain of the same domain name.
    # 2/ they have the same IP address.
    #
    # To test this we use Microsoft's Office365 setup, which
    # has mail.office365.com and outlook.office365.com point to
    # the same address.
    updated_data = attr.evolve(
        account_data,
        imap_server_host="outlook.office365.com",
        smtp_server_host="outlook.office365.com",
    )

    handler = GenericAuthHandler()

    # Create an authenticated account
    account = handler.create_account(updated_data)
    db.session.add(account)
    db.session.commit()
    id_ = account.id

    def gethostbyname_patch(x):
        return "127.0.0.1"

    monkeypatch.setattr(socket, "gethostbyname", gethostbyname_patch)

    # A valid update
    updated_data = attr.evolve(
        account_data,
        imap_server_host="mail.office365.com",
        smtp_server_host="mail.office365.com",
    )
    account = handler.update_account(account, updated_data)
    db.session.add(account)
    db.session.commit()
    account = db.session.query(Account).get(id_)
    assert account._imap_server_host == "mail.office365.com"
    assert account._smtp_server_host == "mail.office365.com"


@pytest.mark.usefixtures("mock_smtp_get_connection")
def test_double_auth(db, mock_imapclient):
    password = "valid"
    email = account_data.email
    mock_imapclient._add_login(email, password)

    handler = GenericAuthHandler()

    # First authentication, using a valid password, succeeds.
    valid_settings = attr.evolve(
        account_data, imap_password=password, smtp_password=password
    )

    account = handler.create_account(valid_settings)
    assert handler.verify_account(account) is True

    db.session.add(account)
    db.session.commit()
    id_ = account.id
    account = db.session.query(Account).get(id_)
    assert account.email_address == email
    assert account.imap_username == email
    assert account.smtp_username == email
    assert account.imap_password == password
    assert account.smtp_password == password

    # Second auth using an invalid password should fail.
    invalid_settings = attr.evolve(account_data, imap_password="invalid_password")
    with pytest.raises(ValidationError):
        account = handler.update_account(account, invalid_settings)
        handler.verify_account(account)

    db.session.expire(account)

    # Ensure original account is unaffected
    account = db.session.query(Account).get(id_)
    assert account.email_address == email
    assert account.imap_username == email
    assert account.smtp_username == email
    assert account.imap_password == password
    assert account.smtp_password == password


def test_parent_domain():
    assert parent_domain("x.a.com") == "a.com"
    assert parent_domain("a.com") == "a.com"
    assert parent_domain(".com") == ""
    assert parent_domain("test.google.com") == "google.com"

    assert parent_domain("smtp.example.a.com") == parent_domain("imap.example.a.com")
    assert parent_domain("smtp.example.a.com") == parent_domain("imap.a.com")

    assert parent_domain("company.co.uk") != parent_domain("evilcompany.co.uk")


@pytest.mark.usefixtures("mock_smtp_get_connection")
def test_successful_reauth_resets_sync_state(db, mock_imapclient):
    email = account_data.email
    password = account_data.imap_password
    mock_imapclient._add_login(email, password)
    handler = GenericAuthHandler()

    account = handler.create_account(account_data)
    assert handler.verify_account(account) is True
    # Brand new accounts have `sync_state`=None.
    assert account.sync_state is None
    db.session.add(account)
    db.session.commit()

    # Pretend account sync starts, and subsequently the password changes,
    # causing the account to be in `sync_state`='invalid'.
    account.mark_invalid()
    db.session.commit()
    assert account.sync_state == "invalid"

    # Verify the `sync_state` is reset to 'running' on a successful "re-auth".
    account = handler.update_account(account, account_data)
    assert handler.verify_account(account) is True
    assert account.sync_state == "running"
    db.session.add(account)
    db.session.commit()
