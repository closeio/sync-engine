import json


def test_cors_headers_set_when_origin_present(test_client) -> None:
    resp = test_client.get("/", headers={"Origin": "http://example.com"})
    assert resp.headers["Access-Control-Allow-Origin"] == "http://example.com"
    assert "Authorization" in resp.headers["Access-Control-Allow-Headers"]
    assert "Content-Type" in resp.headers["Access-Control-Allow-Headers"]
    assert "GET" in resp.headers["Access-Control-Allow-Methods"]
    assert resp.headers["Access-Control-Allow-Credentials"] == "true"


def test_cors_headers_absent_without_origin(test_client) -> None:
    resp = test_client.get("/")
    assert "Access-Control-Allow-Origin" not in resp.headers


def test_invalid_resource_id_returns_json_error(db, api_client) -> None:
    # A malformed base-36 ID cannot refer to any resource and must be rejected
    # before any DB query is attempted.
    resp = api_client.get_raw("/threads/not-a-valid-id")
    assert resp.status_code == 400
    data = json.loads(resp.data)
    assert data["type"] == "invalid_request_error"
    assert "message" in data


def test_account_response_content_type(db, api_client) -> None:
    resp = api_client.post_data(
        "/accounts/",
        {
            "type": "generic",
            "email_address": "content-type-test@example.com",
            "imap_server_host": "imap.example.com",
            "imap_server_port": 143,
            "imap_username": "user",
            "imap_password": "pass",
        },
    )
    assert resp.status_code == 200
    assert "application/json" in resp.content_type


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
