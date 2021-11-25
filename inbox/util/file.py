import errno
import fcntl
import os
import string
import sys
from typing import Generator, List

from gevent.lock import BoundedSemaphore

if sys.version_info < (3,):
    import builtins

    file_like = builtins.file
else:
    import io

    file_like = io.IOBase


def safe_filename(filename):
    """Strip filesystem-unfriendly characters from a filename."""
    valid_chars = "-_.() {}{}".format(string.ascii_letters, string.digits)
    return "".join(c for c in filename if c in valid_chars)


# http://my.safaribooksonline.com/book/programming/python/0596001673/files/pythoncook-chp-4-sect-16
def splitall(path):
    allparts = []
    while True:
        parts = os.path.split(path)
        if parts[0] == path:  # sentinel for absolute paths
            allparts.insert(0, parts[0])
            break
        elif parts[1] == path:  # sentinel for relative paths
            allparts.insert(0, parts[1])
            break
        else:
            path = parts[0]
            allparts.insert(0, parts[1])
    return allparts


def mkdirp(path):
    """
    Create directory and its parents. An equivalent to mkdir -p.

    This can go away in Python 3.2; just use exists_ok=True.

    Parameters
    ----------
    path : str
        Pathname to create.
    """
    try:
        os.makedirs(path)
    except OSError as exc:
        if exc.errno == errno.EEXIST and os.path.isdir(path):
            pass
        else:
            raise


def remove_file(filename):
    """
    Delete a file and don't raise an error if it doesn't exist.

    From:
    http://stackoverflow.com/questions/10840533/most-pythonic-way-to-delete-a-file-which-may-not-exist
    """
    try:
        os.remove(filename)
    except OSError as e:
        if e.errno != errno.ENOENT:
            raise


class Lock(object):
    """
    UNIX-specific exclusive file locks (released when the process ends).

    Based on
    http://blog.vmfarms.com/2011/03/cross-process-locking-and.html,
    adapted for context managers (the 'with' statement).

    Modified to be gevent-safe! Locks held by a given Greenlet may not be
    taken by other Greenlets until released, _as long as you only create one
    Lock object per lockfile_. THIS IS VERY IMPORTANT. *Make sure* that you're
    not creating multiple locks on the same file from the same process,
    otherwise you'll bypass the gevent lock!

    Parameters
    ----------
    f : file or str
        File handle or filename to use as the lock.
    block : bool
        Whether to block or throw IOError if the lock is grabbed multiple
        times.
    """

    TIMEOUT = 60

    def __init__(self, f, block=True):
        if isinstance(f, file_like):
            self.filename = f.name
            self.handle = f if not f.closed else open(f, "w")
        else:
            self.filename = f
            mkdirp(os.path.dirname(f))
            self.handle = open(f, "w")
        if block:
            self.lock_op = fcntl.LOCK_EX
        else:
            self.lock_op = fcntl.LOCK_EX | fcntl.LOCK_NB
        self.block = block
        self.gevent_lock = BoundedSemaphore(1)

    def acquire(self):
        got_gevent_lock = self.gevent_lock.acquire(blocking=self.block)
        if not got_gevent_lock:
            raise IOError(
                "cannot acquire gevent lock; associated file is {}".format(
                    self.filename
                )
            )
        fcntl.flock(self.handle, self.lock_op)

    def release(self):
        fcntl.flock(self.handle, fcntl.LOCK_UN)
        self.gevent_lock.release()

    def locked(self):
        return self.gevent_lock.locked()

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, type, value, traceback):
        self.release()

    def __del__(self):
        self.handle.close()


ROOT_PATH = os.path.normpath(os.path.join(__file__, os.pardir, os.pardir, os.pardir))


def get_data(filename):
    # type: (str) -> bytes
    """Read contents of a file relative to the project root folder"""
    with open(os.path.join(ROOT_PATH, filename), "rb") as file:
        return file.read()


def iter_module_names(paths):
    # type: (List[str]) -> Generator[str, None, None]
    """Iterate all Python module names in given paths"""
    for path in paths:
        for name in os.listdir(path):
            isdirectory = os.path.isdir(os.path.join(path, name))
            if not isdirectory and name == "__init__.py":
                continue

            if not isdirectory and name.endswith(".py"):
                yield name[:-3]
            elif isdirectory and os.path.isfile(
                os.path.join(path, name, "__init__.py")
            ):
                yield name
