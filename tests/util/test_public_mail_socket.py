import imaplib
import socket
import ssl
from unittest import mock

import pytest
from imapclient import SocketTimeout

from inbox.util.public_mail_socket import (
    PublicIMAPClient,
    connect_public_mail_host,
    is_private_or_special_use_ip,
)


@pytest.mark.parametrize(
    ("ip_address", "expected"),
    [
        # Private IPv4 addresses (RFC 1918)
        pytest.param("10.0.0.1", True, id="private-10.x.x.x"),
        pytest.param("172.16.0.1", True, id="private-172.16.x.x"),
        pytest.param("192.168.0.1", True, id="private-192.168.x.x"),
        # Loopback addresses
        pytest.param("127.0.0.1", True, id="loopback-ipv4"),
        pytest.param("::1", True, id="loopback-ipv6"),
        # Link-local addresses (includes AWS IMDS)
        pytest.param("169.254.169.254", True, id="aws-imds"),
        pytest.param("fe80::1", True, id="link-local-ipv6"),
        pytest.param("fe80::1%eth0", True, id="link-local-ipv6-scoped"),
        # IPv6 forms of private IPv4 addresses
        pytest.param("::ffff:10.0.0.5", True, id="ipv4-mapped-private"),
        pytest.param("64:ff9b::a00:5", True, id="nat64-private"),
        # Carrier-grade NAT (RFC 6598)
        pytest.param("100.64.0.1", True, id="cgnat"),
        # Broadcast address
        pytest.param("255.255.255.255", True, id="broadcast"),
        # Unspecified
        pytest.param("0.0.0.0", True, id="unspecified-ipv4"),
        pytest.param("::", True, id="unspecified-ipv6"),
        # Multicast
        pytest.param("224.0.0.1", True, id="multicast-ipv4"),
        pytest.param("ff00::1", True, id="multicast-ipv6"),
        # Unique local IPv6 (fc00::/7)
        pytest.param("fc00::1", True, id="unique-local-ipv6"),
        pytest.param("fd00:ec2::254", True, id="aws-imds-ipv6"),
        # Reserved addresses
        pytest.param("240.0.0.1", True, id="reserved-ipv4"),
        # Public addresses
        pytest.param("8.8.8.8", False, id="public-ipv4"),
        pytest.param("2606:2800:220:1:248:1893:25c8:1946", False, id="ipv6"),
        # Values that are not IP addresses
        pytest.param("invalid", True, id="invalid-string"),
        pytest.param("", True, id="empty-string"),
    ],
)
def test_is_private_or_special_use_ip(ip_address, expected) -> None:
    assert is_private_or_special_use_ip(ip_address) is expected


@pytest.fixture
def mock_resolve_hostname_ip_addresses():
    with mock.patch(
        "inbox.util.public_mail_socket.resolve_hostname_ip_addresses",
        autospec=True,
    ) as m:
        yield m


@pytest.fixture
def mock_create_connection():
    with mock.patch(
        "inbox.util.public_mail_socket.socket.create_connection",
        autospec=True,
    ) as m:
        yield m


def test_connect_public_mail_host_connects_to_the_resolved_ip(
    mock_resolve_hostname_ip_addresses, mock_create_connection
) -> None:
    mock_resolve_hostname_ip_addresses.return_value = ["93.184.216.34"]

    sock = connect_public_mail_host("imap.example.com", 993, 30)

    assert sock is mock_create_connection.return_value
    assert mock_create_connection.mock_calls == [
        mock.call(("93.184.216.34", 993), 30)
    ]


@pytest.mark.parametrize(
    "ips",
    [
        pytest.param(["10.0.0.5"], id="private"),
        pytest.param(["127.0.0.1"], id="loopback"),
        pytest.param(["93.184.216.34", "169.254.169.254"], id="mixed"),
        pytest.param(["::ffff:10.0.0.5"], id="ipv4-mapped-private"),
        pytest.param(["2606:4700::1111", "fd00:ec2::254"], id="mixed-ipv6"),
        pytest.param([], id="empty"),
    ],
)
def test_connect_public_mail_host_blocks_a_host_that_is_not_all_public(
    mock_resolve_hostname_ip_addresses, mock_create_connection, ips
) -> None:
    mock_resolve_hostname_ip_addresses.return_value = ips

    with pytest.raises(socket.gaierror) as exc_info:
        connect_public_mail_host("imap.example.com", 993, 30)

    assert exc_info.value.args == (
        socket.EAI_NONAME,
        "Name or service not known",
    )
    assert mock_create_connection.mock_calls == []


def test_connect_public_mail_host_blocks_a_private_ip_literal(
    mock_create_connection,
) -> None:
    with pytest.raises(socket.gaierror) as exc_info:
        connect_public_mail_host("10.0.0.5", 993, 30)

    assert exc_info.value.args == (
        socket.EAI_NONAME,
        "Name or service not known",
    )
    assert mock_create_connection.mock_calls == []


def test_connect_public_mail_host_tries_the_next_ip_on_a_connect_error(
    mock_resolve_hostname_ip_addresses, mock_create_connection
) -> None:
    mock_resolve_hostname_ip_addresses.return_value = [
        "93.184.216.34",
        "93.184.216.35",
    ]
    sock = mock.Mock(spec=socket.socket)
    mock_create_connection.side_effect = [ConnectionRefusedError(), sock]

    assert connect_public_mail_host("imap.example.com", 993, 30) is sock
    assert mock_create_connection.mock_calls == [
        mock.call(("93.184.216.34", 993), 30),
        mock.call(("93.184.216.35", 993), 30),
    ]


def test_connect_public_mail_host_raises_the_last_connect_error(
    mock_resolve_hostname_ip_addresses, mock_create_connection
) -> None:
    mock_resolve_hostname_ip_addresses.return_value = [
        "93.184.216.34",
        "93.184.216.35",
    ]
    last_error = TimeoutError("timed out")
    mock_create_connection.side_effect = [ConnectionRefusedError(), last_error]

    with pytest.raises(TimeoutError) as exc_info:
        connect_public_mail_host("imap.example.com", 993, 30)

    assert exc_info.value is last_error


@pytest.fixture
def mock_imap4_connect():
    """
    Skip the IMAP greeting and CAPABILITY exchange after the socket opens.
    """
    with mock.patch.object(imaplib.IMAP4, "_connect", autospec=True) as m:
        yield m


def test_public_imap_client_connects_to_the_resolved_ip(
    mock_resolve_hostname_ip_addresses,
    mock_create_connection,
    mock_imap4_connect,
) -> None:
    mock_resolve_hostname_ip_addresses.return_value = ["93.184.216.34"]

    PublicIMAPClient("imap.example.com", 143, ssl=False, timeout=30)

    assert mock_create_connection.call_args_list == [
        mock.call(("93.184.216.34", 143), 30)
    ]


def test_public_imap_client_ssl_wraps_the_pinned_socket_with_the_hostname(
    mock_resolve_hostname_ip_addresses,
    mock_create_connection,
    mock_imap4_connect,
) -> None:
    mock_resolve_hostname_ip_addresses.return_value = ["93.184.216.34"]
    raw_sock = mock.Mock(spec=socket.socket)
    context = mock.Mock(spec=ssl.SSLContext)
    mock_create_connection.return_value = raw_sock

    PublicIMAPClient("imap.example.com", 993, ssl=True, ssl_context=context)

    assert mock_create_connection.call_args_list == [
        mock.call(("93.184.216.34", 993), None)
    ]
    assert context.wrap_socket.call_args_list == [
        mock.call(raw_sock, server_hostname="imap.example.com")
    ]


@pytest.mark.parametrize(
    ("port", "use_ssl"),
    [pytest.param(143, False, id="plain"), pytest.param(993, True, id="tls")],
)
def test_public_imap_client_connects_with_the_connect_timeout(
    mock_resolve_hostname_ip_addresses,
    mock_create_connection,
    mock_imap4_connect,
    port,
    use_ssl,
) -> None:
    mock_resolve_hostname_ip_addresses.return_value = ["93.184.216.34"]

    PublicIMAPClient(
        "imap.example.com",
        port,
        ssl=use_ssl,
        ssl_context=mock.Mock(spec=ssl.SSLContext),
        timeout=SocketTimeout(connect=7, read=99),
    )

    assert mock_create_connection.call_args_list == [
        mock.call(("93.184.216.34", port), 7)
    ]


def test_public_imap_client_blocks_a_host_with_a_private_ip(
    mock_resolve_hostname_ip_addresses,
    mock_create_connection,
    mock_imap4_connect,
) -> None:
    mock_resolve_hostname_ip_addresses.return_value = ["10.0.0.5"]

    with pytest.raises(socket.gaierror) as exc_info:
        PublicIMAPClient("imap.example.com", 993, ssl=True)

    assert exc_info.value.args == (
        socket.EAI_NONAME,
        "Name or service not known",
    )
    assert mock_create_connection.mock_calls == []
