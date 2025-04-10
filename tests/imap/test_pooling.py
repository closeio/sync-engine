import imaplib
import queue
import socket
import ssl
from typing import Any, Never
from unittest import mock

import pytest

from inbox.crispin import ConnectionPoolTimeoutError, CrispinConnectionPool


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


def test_timeout_on_depleted_pool() -> None:
    pool = TestableConnectionPool(1, num_connections=1, readonly=True)
    # Test that getting a connection when the pool is empty times out
    with (
        pytest.raises(ConnectionPoolTimeoutError),
        pool.get(),
        pool.get(timeout=0.1),
    ):
        pass


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
def test_imap_and_network_errors(error_class, expect_logout_called) -> Never:
    pool = TestableConnectionPool(1, num_connections=3, readonly=True)
    with pytest.raises(error_class), pool.get() as conn:
        raise error_class
    assert pool._queue.full()
    # Check that the connection wasn't returned to the pool
    while not pool._queue.empty():
        item = pool._queue.get()
        assert item is None
    assert conn.logout.called is expect_logout_called


def test_connection_retained_on_other_errors() -> Never:
    pool = TestableConnectionPool(1, num_connections=3, readonly=True)
    with pytest.raises(ValueError), pool.get() as conn:
        raise ValueError
    assert conn in get_all(pool._queue)
    assert not conn.logout.called
