import ssl

from imapclient import IMAPClient  # type: ignore[import-untyped]

from inbox.exceptions import SSLNotSupportedError
from inbox.logging import get_logger

log = get_logger()


def safe_decode(message: str | bytes) -> str:
    if isinstance(message, bytes):
        return message.decode("utf-8", errors="replace")

    return message


# Some servers require an application-specific password, token, or
# authorization code to login.
APP_SPECIFIC_PASSWORD_PREFIXES = [
    prefix.lower()
    for prefix in [
        "Please using authorized code to login.",  # http://service.mail.qq.com/cgi-bin/help?subtype=1&&id=28&&no=1001256
        "Authorized code is incorrect",  # http://service.mail.qq.com/cgi-bin/help?subtype=1&&id=28&&no=1001256
        "Login fail. Please using weixin token",  # http://service.exmail.qq.com/cgi-bin/help?subtype=1&no=1001023&id=23.
    ]
]


def auth_requires_app_password(exc: IMAPClient.Error) -> bool:
    if not exc.args:
        return False
    error_message = safe_decode(exc.args[0]).lower()
    return any(
        error_message.startswith(prefix)
        for prefix in APP_SPECIFIC_PASSWORD_PREFIXES
    )


# IMAP doesn't have error semantics, so we have to match the error message
# against a list of known responses to determine whether we couldn't log in
# because the credentials are invalid. Sometimes a single pattern matches many
# different responses from servers. For real error messages that have been
# received from servers in the past, see the test suite.
AUTH_INVALID_PATTERNS = [
    pattern.lower()
    for pattern in (
        "fail",
        "incorrect",
        "invalid",
        "bad",
        "login error",
        "username error",
        "password error",
        "please log in",
    )
]


def is_error_message_invalid_auth(error_message: str) -> bool:
    normalized_error_message = error_message.lower()
    return any(
        pattern in normalized_error_message
        for pattern in AUTH_INVALID_PATTERNS
    )


def create_imap_connection(  # type: ignore[no-untyped-def]  # noqa: ANN201
    host, port, use_timeout: bool = True
):
    """
    Return a connection to the IMAP server.

    If the port is the SSL port (993), use an SSL connection. Otherwise, use
    STARTTLS.

    Raises:
        SSLNotSupportedError: If an encrypted connection is not supported by
            the IMAP server.

    """
    is_ssl_port = port == 993
    timeout = 300 if use_timeout else None

    # TODO: certificate pinning for well known sites
    context = create_default_context()
    conn = IMAPClient(
        host,
        port=port,
        use_uid=True,
        ssl=is_ssl_port,
        ssl_context=context,
        timeout=timeout,
    )

    if not is_ssl_port:
        # Always use STARTTLS if we're using a non-SSL port.
        if conn.has_capability("STARTTLS"):
            try:
                conn.starttls(context)
            except Exception:
                log.warning("STARTTLS supported but failed.", exc_info=True)
                raise
        else:
            raise SSLNotSupportedError("Required IMAP STARTTLS not supported.")

    return conn


def create_default_context():  # type: ignore[no-untyped-def]  # noqa: ANN201
    """
    Return a backports.ssl.SSLContext object configured with sensible
    default settings. This was adapted from imapclient.create_default_context
    to allow all ciphers and disable certificate verification.

    """
    # adapted from Python 3.4's ssl.create_default_context

    context = ssl.SSLContext(ssl.PROTOCOL_SSLv23)

    # do not verify that certificate is signed nor that the
    # certificate matches the hostname
    context.verify_mode = ssl.CERT_NONE
    context.check_hostname = False

    return context
