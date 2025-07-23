import threading
import time
from unittest import mock

import pytest

from inbox.mailsync.backends.imap.monitor import (
    FolderSyncEngine,
    ImapSyncMonitor,
)


def test_imap_sync_monitor_start_stop(mock_imapclient, db, default_account):
    from inbox.mailsync.gc import DeleteHandler

    mock_imapclient.list_folders = mock.Mock(
        return_value=[
            ((b"\\All", b"\\HasNoChildren"), b"/", "[Gmail]/All Mail")
        ]
    )

    monitor = ImapSyncMonitor(default_account)

    assert monitor.is_alive() is False
    assert monitor.ready() is False
    assert monitor.successful() is False

    monitor.start()

    assert monitor.is_alive() is True
    assert monitor.ready() is False

    # Give it a moment to start the subthreads
    for __ in range(100):
        if threading.active_count() >= 4:
            break
        time.sleep(0.1)
    else:
        pytest.fail("Timed out waiting for threads to start")

    assert sorted(
        (thread.__class__ for thread in threading.enumerate()), key=repr
    ) == [
        FolderSyncEngine,
        ImapSyncMonitor,
        DeleteHandler,
        threading._MainThread,
    ]

    monitor.stop()
    monitor.join(timeout=1)

    assert monitor.is_alive() is False
    assert monitor.ready() is True
    assert threading.enumerate() == [threading.main_thread()]
