import pytest

from inbox.auth.utils import (
    is_error_message_disabled_imap,
    is_error_message_invalid_auth,
)


@pytest.mark.parametrize(
    "error_message",
    [
        "[authenticationfailed]",
        "incorrect username or password",
        "invalid login or password",
        "login login error password error",
        "[auth] authentication failed.",
        "invalid login credentials",
        "[ALERT] Please log in via your web browser",
        "LOGIN Authentication failed",
        "authentication failed",
        "[ALERT] Invalid credentials(Failure)",
        "Invalid email login",
        "failed: Re-Authentication Failure",
        "Invalid",
        "Login incorrect",
        "LOGIN GroupWise login failed",
        "LOGIN bad",
        "[AUTHORIZATIONFAILED]",
        "incorrect password",
    ],
)
def test_auth_is_invalid(error_message):
    assert is_error_message_invalid_auth(error_message) is True


def test_imap_disabled_detection():
    assert (
        is_error_message_disabled_imap(
            "[ALERT] You are yet to enable IMAP for your account. Please"
            " contact your administrator (Failure)"
        )
        is True
    )
