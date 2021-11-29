from __future__ import absolute_import, print_function

import pytest

from inbox.models.session import session_scope

from .client import NylasTestClient
from .conftest import API_BASE, create_account, credentials, timeout_loop

try:
    # If there's no broken accounts file, well, tough luck but don't crash.
    # This should only be a problem locally; the jenkins jobs generates those
    # credentials.
    from accounts import broken_credentials
except ImportError:
    print("test_auth.py: Warning -- No broken accounts credentials.")
    broken_credentials = []


@timeout_loop("sync_start")
def wait_for_sync_start(client):
    return True if client.messages.first() else False


@timeout_loop("auth")
def wait_for_auth(client):
    namespaces = client.namespaces.all()
    if len(namespaces):
        client.email_address = namespaces[0]["email_address"]
        client.provider = namespaces[0]["provider"]
        return True
    return False


@pytest.mark.parametrize("account_credentials", credentials)
def test_account_auth(account_credentials):
    email, password = account_credentials
    create_account(email, password)
    client = NylasTestClient(email, API_BASE)
    wait_for_auth(client)

    # wait for sync to start. tests rely on things setup at beginning
    # of sync (e.g. folder hierarchy)
    wait_for_sync_start(client)


errors = __import__("inbox.basicauth", fromlist=["basicauth"])


def test_account_create_should_fail():
    """
    Test that creation fails with appropriate errors, as defined in
    the broken_credentials list.
    Credentials have the format:
    ({email, password}, error_type)
    e.g.
    ({'user': 'foo@foo.com', 'password': 'pass'}, 'ConfigurationError')
    """
    credentials = [((c["user"], c["password"]), e) for (c, e) in broken_credentials]

    for ((email, password), error) in credentials:
        error_obj = getattr(errors, error)
        with session_scope() as db_session:
            with pytest.raises(error_obj):
                create_account(db_session, email, password)


if __name__ == "__main__":
    pytest.main([__file__])
