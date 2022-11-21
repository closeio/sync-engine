import json

import pytest

from inbox.models.account import Account

from tests.api.base import new_api_client
from tests.util.base import add_fake_yahoo_account, db, generic_account, gmail_account

__all__ = ["db", "generic_account", "gmail_account"]


def test_account(db, api_client, generic_account, gmail_account):
    # Because we're using the generic_account namespace
    api_client = new_api_client(db, generic_account.namespace)

    resp_data = api_client.get_data("/account")

    assert resp_data["id"] == generic_account.namespace.public_id
    assert resp_data["object"] == "account"
    assert resp_data["account_id"] == generic_account.namespace.public_id
    assert resp_data["email_address"] == generic_account.email_address
    assert resp_data["name"] == generic_account.name
    assert resp_data["organization_unit"] == "folder"
    assert "sync_state" in resp_data
    assert "server_settings" not in resp_data

    # Because we're using the gmail account namespace
    api_client = new_api_client(db, gmail_account.namespace)

    resp_data = api_client.get_data("/account")

    assert resp_data["id"] == gmail_account.namespace.public_id
    assert resp_data["provider"] == "gmail"
    assert resp_data["organization_unit"] == "label"
    assert "sync_state" in resp_data
    assert "server_settings" not in resp_data


def test_account_expanded(db, api_client, generic_account, gmail_account):
    # Generic accounts expose a `server_settings` attribute
    # Custom IMAP
    api_client = new_api_client(db, generic_account.namespace)
    resp_data = api_client.get_data("/account/?view=expanded")
    assert resp_data["provider"] == "custom"
    assert "server_settings" in resp_data
    assert set(resp_data["server_settings"]) == set(
        {
            "imap_host": "imap.custom.com",
            "smtp_host": "smtp.custom.com",
            "imap_port": 993,
            "smtp_port": 587,
        }
    )

    # Yahoo
    yahoo_account = add_fake_yahoo_account(db.session)
    api_client = new_api_client(db, yahoo_account.namespace)
    resp_data = api_client.get_data("/account/?view=expanded")
    assert resp_data["provider"] == "yahoo"
    assert "server_settings" in resp_data
    assert set(resp_data["server_settings"]) == set(
        {
            "imap_host": "imap.mail.yahoo.com",
            "smtp_host": "smtp.mail.yahoo.com",
            "imap_port": 993,
            "smtp_port": 587,
        }
    )

    # Gmail accounts don't expose a `server_settings` attribute
    api_client = new_api_client(db, gmail_account.namespace)
    resp_data = api_client.get_data("/account/?view=expanded")
    assert resp_data["provider"] == "gmail"
    assert "server_settings" not in resp_data


def test_account_repr_for_new_account(db):
    account = add_fake_yahoo_account(db.session)

    # Sync for the account has not started yet.
    assert account.sync_state is None

    # However the API-returned account object has `sync_state=running`
    # so API clients can do the right thing.
    api_client = new_api_client(db, account.namespace)
    resp_data = api_client.get_data("/account")
    assert resp_data["id"] == account.namespace.public_id
    assert resp_data["sync_state"] == "running"

    # Verify other sync_states are not masked.
    account.sync_state = "invalid"
    db.session.commit()

    api_client = new_api_client(db, account.namespace)
    resp_data = api_client.get_data("/account")
    assert resp_data["id"] == account.namespace.public_id
    assert resp_data["sync_state"] == "invalid"


@pytest.mark.parametrize("type", ["microsoft", "gmail"])
@pytest.mark.parametrize("sync_calendar", [True, False])
def test_account_create(db, api_client, type, sync_calendar):
    data = {
        "type": type,
        "email_address": "fake@example.com",
        "scopes": "fake",
        "authalligator": "fake",
        "sync_calendar": sync_calendar,
    }

    response = api_client.post_data("/accounts/", data)
    public_id = json.loads(response.data)["account_id"]
    account = (
        db.session.query(Account)
        .filter(Account.namespace.has(public_id=public_id))
        .one()
    )

    assert account.provider == type
    assert account.sync_events == sync_calendar
