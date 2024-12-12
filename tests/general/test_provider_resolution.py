import pytest

from inbox.auth.base import handler_from_provider
from inbox.auth.generic import GenericAuthHandler
from inbox.auth.google import GoogleAuthHandler
from inbox.exceptions import NotSupportedError
from inbox.util.url import InvalidEmailAddressError, provider_from_address


def test_provider_resolution(mock_dns_resolver) -> None:
    mock_dns_resolver._load_records(
        "tests/data/general_test_provider_resolution.json"
    )
    test_cases = [
        ("foo@example.com", "unknown"),
        ("foo@noresolve.com", "unknown"),
        ("foo@gmail.com", "gmail"),
        ("foo@postini.com", "gmail"),
        ("foo@yahoo.com", "yahoo"),
        ("foo@yahoo.se", "yahoo"),
        ("foo@hotmail.com", "microsoft"),
        ("foo@outlook.com", "microsoft"),
        ("foo@aol.com", "aol"),
        ("foo@love.com", "aol"),
        ("foo@games.com", "aol"),
        ("foo@exchange.mit.edu", "microsoft"),
        ("foo@fastmail.fm", "fastmail"),
        ("foo@fastmail.net", "fastmail"),
        ("foo@fastmail.com", "fastmail"),
        ("foo@hover.com", "hover"),
        ("foo@yahoo.com", "yahoo"),
        ("foo@yandex.com", "yandex"),
        ("foo@mrmail.com", "zimbra"),
        ("foo@icloud.com", "icloud"),
        ("foo@mac.com", "icloud"),
        ("foo@gmx.com", "gmx"),
        ("foo@gandi.net", "gandi"),
        ("foo@debuggers.co", "gandi"),
        ("foo@forumone.com", "gmail"),
        ("foo@getbannerman.com", "gmail"),
        ("foo@inboxapp.onmicrosoft.com", "microsoft"),
        ("foo@espertech.onmicrosoft.com", "microsoft"),
        ("foo@doesnotexist.nilas.com", "unknown"),
        ("foo@autobizbrokers.com", "bluehost"),
    ]
    for email, expected_provider in test_cases:
        assert (
            provider_from_address(email, lambda: mock_dns_resolver)
            == expected_provider
        )

    with pytest.raises(InvalidEmailAddressError):
        provider_from_address("notanemail", lambda: mock_dns_resolver)
    with pytest.raises(InvalidEmailAddressError):
        provider_from_address("not@anemail", lambda: mock_dns_resolver)
    with pytest.raises(InvalidEmailAddressError):
        provider_from_address("notanemail.com", lambda: mock_dns_resolver)


def test_auth_handler_dispatch() -> None:
    assert isinstance(handler_from_provider("custom"), GenericAuthHandler)
    assert isinstance(handler_from_provider("gmail"), GoogleAuthHandler)

    with pytest.raises(NotSupportedError):
        handler_from_provider("NOTAREALMAILPROVIDER")
