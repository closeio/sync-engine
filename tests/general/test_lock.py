""" Tests for file lock implementation. """
from __future__ import print_function

import tempfile

import pytest
from gevent import sleep, spawn

from inbox.util.file import Lock


def tmp_lock(block, filename=None):
    if filename is None:
        handle, filename = tempfile.mkstemp()
    return Lock(filename, block=block)


def grab_lock(lock):
    """ Stub fn to grab lock inside a Greenlet. """
    lock.acquire()
    print("Got the lock again", lock.filename)
    lock.release()


def test_non_blocking_lock():
    with tmp_lock(block=False) as lock:
        filename = lock.filename
        with pytest.raises(IOError), tmp_lock(block=False, filename=filename):
            pass
    # Should be able to acquire the lock again after the scope ends (also
    # testing that the non-context-manager acquire works).
    lock.acquire()
    # Should NOT be able to take the same lock from a Greenlet.
    g = spawn(grab_lock, lock)
    g.join()
    assert not g.successful(), "greenlet should throw error"
    lock.release()


def test_blocking_lock():
    with tmp_lock(block=True) as lock:
        # A greenlet should hang forever if it tries to acquire this lock.
        g = spawn(grab_lock, lock)
        # Wait long enough that the greenlet ought to be able to finish if
        # it's not blocking, but not long enough to make the test suite hella
        # slow.
        sleep(0.2)
        assert not g.ready(), "greenlet shouldn't be able to grab lock"
