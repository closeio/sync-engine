import ipaddress
import socket

from imapclient import IMAPClient  # type: ignore[import-untyped]
from imapclient.imap4 import (  # type: ignore[import-untyped]
    IMAP4WithTimeout,
)
from imapclient.tls import (  # type: ignore[import-untyped]
    IMAP4_TLS,
    wrap_socket,
)

from inbox.logging import get_logger

log = get_logger()


def resolve_hostname_ip_addresses(hostname: str) -> list[str]:
    """
    Resolve the hostname to every A and AAAA address, without duplicates.

    getaddrinfo() raises UnicodeError for a name that IDNA cannot encode.
    This converts it to OSError, which the IMAP callers already handle.
    """
    try:
        results = socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
    except UnicodeError as exc:
        raise OSError(str(exc)) from exc
    ips: list[str] = []
    seen: set[str] = set()
    for result in results:
        sockaddr = result[4]
        ip = str(sockaddr[0])
        if ip in seen:
            continue
        seen.add(ip)
        ips.append(ip)
    return ips


def is_private_or_special_use_ip(ip_address: str) -> bool:
    try:
        ip = ipaddress.ip_address(ip_address)
    except ValueError:
        return True

    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
        or ip in ipaddress.ip_network("100.64.0.0/10")  # Carrier-grade NAT
    )


def connect_public_mail_host(
    host: str, port: int, timeout: float | None
) -> socket.socket:
    """
    Connect to a mail host only if every IP it resolves to is public.

    A blocked host raises the same error as a hostname that does not exist.
    The account stores the error as its sync error, and closeio uses it to
    pick the message that the member sees. A distinct error would tell a
    member which internal names exist.
    """
    ips = resolve_hostname_ip_addresses(host)
    if not ips or any(is_private_or_special_use_ip(ip) for ip in ips):
        log.warning("Blocked a private mail host", host=host, ips=ips)
        raise socket.gaierror(socket.EAI_NONAME, "Name or service not known")
    for ip in ips[:-1]:
        try:
            return socket.create_connection((ip, port), timeout)
        except OSError:
            continue
    return socket.create_connection((ips[-1], port), timeout)


# No @override in the classes below. imapclient has no type information,
# so mypy cannot find the base methods.
class PublicIMAP4(IMAP4WithTimeout):
    def _create_socket(self, timeout: float | None = None) -> socket.socket:
        return connect_public_mail_host(
            self.host,
            self.port,
            timeout if timeout is not None else self._timeout,
        )


class PublicIMAP4TLS(IMAP4_TLS):
    # IMAP4_TLS.open() connects inline and has no socket hook to override,
    # so this copies it with the pinned connect.
    def open(
        self, host: str, port: int = 993, timeout: float | None = None
    ) -> None:
        self.host = host
        self.port = port
        sock = connect_public_mail_host(
            host, port, timeout if timeout is not None else self._timeout
        )
        self.sock = wrap_socket(sock, self.ssl_context, host)
        self.file = self.sock.makefile("rb")


class PublicIMAPClient(IMAPClient):
    def _create_IMAP4(self) -> IMAP4WithTimeout | IMAP4_TLS:  # noqa: N802
        connect_timeout = getattr(self._timeout, "connect", None)
        if self.ssl:
            return PublicIMAP4TLS(
                self.host, self.port, self.ssl_context, connect_timeout
            )
        return PublicIMAP4(self.host, self.port, connect_timeout)
