import json


def test_create_generic_account(db, api_client) -> None:
    resp = api_client.post_data(
        "/accounts/",
        {
            "type": "generic",
            "email_address": "test@example.com",
            "imap_server_host": "imap.example.com",
            "imap_server_port": 143,
            "imap_username": "imap_username",
            "imap_password": "imap_password",
            "sync_email": True,
        },
    )
    account = json.loads(resp.data)
    assert account["email_address"] == "test@example.com"
    assert account["object"] == "account"
    assert account["provider"] == "custom"
    assert account["organization_unit"] == "folder"
    assert account["sync_state"] == "running"


def test_create_gmail_account(db, api_client) -> None:
    resp = api_client.post_data(
        "/accounts/",
        {
            "type": "gmail",
            "email_address": "test@example.com",
            "scopes": "scope1 scope2",
            "client_id": "clientid",
            "refresh_token": "refresh",
            "sync_email": True,
            "sync_calendar": True,
            "sync_events": False,
        },
    )
    account = json.loads(resp.data)
    assert account["email_address"] == "test@example.com"
    assert account["object"] == "account"
    assert account["provider"] == "gmail"
    assert account["organization_unit"] == "label"
    assert account["sync_state"] == "running"


def test_create_microsoft_account(db, api_client) -> None:
    resp = api_client.post_data(
        "/accounts/",
        {
            "type": "microsoft",
            "email_address": "test@example.com",
            "scopes": "scope1 scope2",
            "client_id": "clientid",
            "refresh_token": "refresh",
            "sync_email": True,
        },
    )
    account = json.loads(resp.data)
    assert account["email_address"] == "test@example.com"
    assert account["object"] == "account"
    assert account["provider"] == "microsoft"
    assert account["organization_unit"] == "folder"
    assert account["sync_state"] == "running"
