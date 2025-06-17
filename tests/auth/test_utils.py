import pytest

from inbox.auth.utils import is_error_message_invalid_auth


@pytest.mark.parametrize(
    ("error_message", "result"),
    [
        # Server responses seen in the wild.
        ("[authenticationfailed]", True),
        ("incorrect username or password", True),
        ("invalid login or password", True),
        ("login login error password error", True),
        ("[auth] authentication failed.", True),
        ("invalid login credentials", True),
        ("[ALERT] Please log in via your web browser", True),
        ("LOGIN Authentication failed", True),
        ("authentication failed", True),
        ("[ALERT] Invalid credentials(Failure)", True),
        ("Invalid email login", True),
        ("failed: Re-Authentication Failure", True),
        ("Invalid", True),
        ("Login incorrect", True),
        ("LOGIN GroupWise login failed", True),
        ("LOGIN bad", True),
        ("[AUTHORIZATIONFAILED]", True),
        ("incorrect password", True),
        # Strings not seen in the wild.
        ("asdbadasd", False),  # Contains "bad", but not as a word.
    ],
)
def test_auth_is_invalid(error_message, result):
    assert is_error_message_invalid_auth(error_message) is result
