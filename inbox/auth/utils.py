from backports import ssl
from imapclient import IMAPClient
from nylas.logging import get_logger
from OpenSSL._util import lib as ossllib

from inbox.basicauth import SSLNotSupportedError

log = get_logger()


def auth_requires_app_password(exc):
    # Some servers require an application specific password, token, or
    # authorization code to login
    PREFIXES = (
        "Please using authorized code to login.",  # http://service.mail.qq.com/cgi-bin/help?subtype=1&&id=28&&no=1001256
        "Authorized code is incorrect",  # http://service.mail.qq.com/cgi-bin/help?subtype=1&&id=28&&no=1001256
        "Login fail. Please using weixin token",  # http://service.exmail.qq.com/cgi-bin/help?subtype=1&no=1001023&id=23.
    )
    return any(exc.message.lower().startswith(msg.lower()) for msg in PREFIXES)


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
    return any(
        exc.message.lower().startswith(msg.lower()) for msg in AUTH_INVALID_PREFIXES
    )


def create_imap_connection(host, port, ssl_required, use_timeout=True):
    """
    Return a connection to the IMAP server.
    The connection is encrypted if the specified port is the default IMAP
    SSL port (993) or the server supports STARTTLS.
    IFF neither condition is met and SSL is not required, an insecure connection
    is returned. Otherwise, an exception is raised.

    """
    use_ssl = port == 993
    timeout = 300 if use_timeout else None

    # TODO: certificate pinning for well known sites
    context = create_default_context()
    conn = IMAPClient(
        host, port=port, use_uid=True, ssl=use_ssl, ssl_context=context, timeout=timeout
    )

    if not use_ssl:
        # If STARTTLS is available, always use it. If it's not/ it fails, use
        # `ssl_required` to determine whether to fail or continue with
        # plaintext authentication.
        if conn.has_capability("STARTTLS"):
            try:
                conn.starttls(context)
            except Exception:
                if not ssl_required:
                    log.warning(
                        "STARTTLS supported but failed for SSL NOT "
                        "required authentication",
                        exc_info=True,
                    )
                else:
                    raise
        elif ssl_required:
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

    # SSLv2 considered harmful.
    context.options |= ossllib.SSL_OP_NO_SSLv2

    # SSLv3 has problematic security and is only required for really old
    # clients such as IE6 on Windows XP
    context.options |= ossllib.SSL_OP_NO_SSLv3

    # disable compression to prevent CRIME attacks (OpenSSL 1.0+)
    context.options |= ossllib.SSL_OP_NO_COMPRESSION

    # Prefer the server's ciphers by default so that we get stronger
    # encryption
    context.options |= ossllib.SSL_OP_CIPHER_SERVER_PREFERENCE

    # Use single use keys in order to improve forward secrecy
    context.options |= ossllib.SSL_OP_SINGLE_DH_USE
    context.options |= ossllib.SSL_OP_SINGLE_ECDH_USE

    context._ctx.set_mode(
        ossllib.SSL_MODE_ENABLE_PARTIAL_WRITE
        | ossllib.SSL_MODE_ACCEPT_MOVING_WRITE_BUFFER
        | ossllib.SSL_MODE_AUTO_RETRY
    )

    return context
