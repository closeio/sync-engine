# -*- coding: utf-8 -*-
import copy
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


@pytest.mark.skipif(True, reason="Need to investigate")
def test_update_account(db):
    handler = GenericAuthHandler()

    # Create an authenticated account
    account = handler.create_account(account_data)
    db.session.add(account)
    db.session.commit()
    id_ = account.id

    # A valid update
    account = handler.update_account(account, updated_settings["settings"])
    updated_data = attr.evolve(account_data, imap_username="other@example.com")
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


def test_parent_domain():
    assert parent_domain("x.a.com") == "a.com"
    assert parent_domain("a.com") == "a.com"
    assert parent_domain(".com") == ""
    assert parent_domain("test.google.com") == "google.com"

    assert parent_domain("smtp.example.a.com") == parent_domain("imap.example.a.com")
    assert parent_domain("smtp.example.a.com") == parent_domain("imap.a.com")

    assert parent_domain("company.co.uk") != parent_domain("evilcompany.co.uk")