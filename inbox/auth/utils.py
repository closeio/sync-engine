import ssl
from typing import Union

from imapclient import IMAPClient

from inbox.basicauth import SSLNotSupportedError
from inbox.logging import get_logger

log = get_logger()


def safe_decode(message):
    # type: (Union[str, bytes]) -> str
    if isinstance(message, bytes):
        return message.decode("utf-8", errors="replace")

    return message


def auth_requires_app_password(exc):
    # Some servers require an application specific password, token, or
    # authorization code to login
    PREFIXES = (
        "Please using authorized code to login.",  # http://service.mail.qq.com/cgi-bin/help?subtype=1&&id=28&&no=1001256
        "Authorized code is incorrect",  # http://service.mail.qq.com/cgi-bin/help?subtype=1&&id=28&&no=1001256
        "Login fail. Please using weixin token",  # http://service.exmail.qq.com/cgi-bin/help?subtype=1&no=1001023&id=23.
    )
    message = safe_decode(exc.args[0]) if exc.args else ""
    return any(message.lower().startswith(msg.lower()) for msg in PREFIXES)


def auth_is_invalid(exc):
    # IMAP doesn't really have error semantics, so we have to match the error
    # message against a list of known response strings to determine whether we
    # couldn't log in because the credentials are invalid, or because of some
    # temporary server error.
    AUTH_INVALID_PREFIXES = (
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
        "authentication failed",
        "LOGIN bad",  # LOGIN bad username or password
        "[AUTHORIZATIONFAILED]",
        "incorrect password",
    )
    message = safe_decode(exc.args[0]) if exc.args else ""
    return any(message.lower().startswith(msg.lower()) for msg in AUTH_INVALID_PREFIXES)


def create_imap_connection(host, port, use_timeout=True):
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
                log.warning(
                    "STARTTLS supported but failed.", exc_info=True,
                )
                raise
        else:
            raise SSLNotSupportedError("Required IMAP STARTTLS not supported.")

    return conn


def create_default_context():
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
