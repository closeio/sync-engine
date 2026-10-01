import imaplib
import queue
import socket
import ssl
from typing import Any, Never
from unittest import mock

import pytest

from inbox.auth.utils import IMAP_SOCKET_TIMEOUT
from inbox.crispin import (
    ConnectionPoolTimeoutError,
    CrispinConnectionPool,
    connection_pool,
    writable_connection_pool,
)
from inbox.interruptible_threading import InterruptibleThreadTimeout


class TestableConnectionPool(CrispinConnectionPool):
    def _set_account_info(self):
        pass

    def _new_connection(self):
        return mock.Mock()


def get_all(queue: "queue.Queue[Any]") -> list[Any]:
    items = []
    while not queue.empty():
        items.append(queue.get())
    return items


def test_pool() -> None:
    pool = TestableConnectionPool(1, num_connections=3, readonly=True)
    with pool.get() as conn:
        pass
    assert pool._queue.full()
    assert conn in get_all(pool._queue)


@pytest.mark.parametrize(
    "get_connection_pool", [connection_pool, writable_connection_pool]
)
def test_connections_have_socket_timeout(
    db, generic_account, get_connection_pool, monkeypatch
) -> None:
    imap_client_class = mock.Mock()
    monkeypatch.setattr("inbox.auth.utils.IMAPClient", imap_client_class)
    # Start with no pools, so that the test does not get a pool of another
    # test.
    monkeypatch.setattr("inbox.crispin._pool_map", {})
    monkeypatch.setattr("inbox.crispin._writable_pool_map", {})

    with get_connection_pool(generic_account.id).get() as crispin_client:
        assert crispin_client.conn is imap_client_class.return_value

    assert imap_client_class.call_args.kwargs["timeout"] == IMAP_SOCKET_TIMEOUT


def test_writable_connection_logged_out_after_use() -> None:
    pool = TestableConnectionPool(1, num_connections=1, readonly=False)
    with pool.get() as conn:
        pass
    assert conn.logout.called
    assert not conn.shutdown.called
    assert get_all(pool._queue) == [None]


def test_writable_connection_closed_after_failed_logout() -> None:
    pool = TestableConnectionPool(1, num_connections=1, readonly=False)
    with pool.get() as conn:
        conn.logout.side_effect = TimeoutError
    assert conn.logout.called
    assert conn.shutdown.called
    assert get_all(pool._queue) == [None]


def test_timeout_on_depleted_pool() -> None:
    pool = TestableConnectionPool(1, num_connections=1, readonly=True)
    # Test that getting a connection when the pool is empty times out
    with (
        pytest.raises(ConnectionPoolTimeoutError),
        pool.get(),
        pool.get(timeout=0.1),
    ):
        pass


@pytest.mark.parametrize("readonly", [True, False])
@pytest.mark.parametrize(
    ("error_class", "expect_logout_called"),
    [
        (imaplib.IMAP4.error, True),
        (imaplib.IMAP4.abort, False),
        (socket.error, False),
        (socket.timeout, False),
        (ssl.SSLError, False),
        (ssl.CertificateError, False),
    ],
)
def test_imap_and_network_errors(
    error_class, expect_logout_called, readonly
) -> Never:
    pool = TestableConnectionPool(1, num_connections=3, readonly=readonly)
    with pytest.raises(error_class), pool.get() as conn:
        raise error_class
    assert pool._queue.full()
    # Check that the connection wasn't returned to the pool
    while not pool._queue.empty():
        item = pool._queue.get()
        assert item is None
    assert conn.logout.called is expect_logout_called
    # Without LOGOUT, the pool closes the socket.
    assert conn.shutdown.called is not expect_logout_called


def test_connection_retained_on_other_errors() -> Never:
    pool = TestableConnectionPool(1, num_connections=3, readonly=True)
    with pytest.raises(ValueError), pool.get() as conn:
        raise ValueError
    assert conn in get_all(pool._queue)
    assert not conn.logout.called
    assert not conn.shutdown.called


def test_writable_connection_discarded_on_other_errors() -> None:
    pool = TestableConnectionPool(1, num_connections=1, readonly=False)
    with pytest.raises(ValueError), pool.get() as conn:
        raise ValueError
    assert get_all(pool._queue) == [None]
    assert not conn.logout.called
    assert conn.shutdown.called


@pytest.mark.parametrize("readonly", [True, False])
def test_connection_discarded_on_timeout(readonly) -> None:
    pool = TestableConnectionPool(1, num_connections=1, readonly=readonly)
    with pytest.raises(InterruptibleThreadTimeout), pool.get() as conn:
        raise InterruptibleThreadTimeout
    assert get_all(pool._queue) == [None]
    assert not conn.logout.called
    assert conn.shutdown.called


def test_connection_discarded_on_timeout_in_logout() -> None:
    pool = TestableConnectionPool(1, num_connections=1, readonly=False)
    with pytest.raises(InterruptibleThreadTimeout), pool.get() as conn:
        conn.logout.side_effect = InterruptibleThreadTimeout
    assert get_all(pool._queue) == [None]
    assert conn.shutdown.called


def test_error_on_shutdown_does_not_replace_original_error(
    monkeypatch,
) -> None:
    conn = mock.Mock()
    conn.shutdown.side_effect = OSError
    pool = TestableConnectionPool(1, num_connections=1, readonly=False)
    monkeypatch.setattr(pool, "_new_connection", lambda: conn)
    with pytest.raises(InterruptibleThreadTimeout), pool.get():
        raise InterruptibleThreadTimeout
    assert conn.shutdown.called
    assert get_all(pool._queue) == [None]
