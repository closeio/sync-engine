import ctypes
import sys
import threading
import time
from functools import cache
from typing import NoReturn

from inbox.logging import get_logger

logger = get_logger()


class MallInfo(ctypes.Structure):
    _fields_ = [
        (name, ctypes.c_int)
        for name in (
            "arena",
            "ordblks",
            "smblks",
            "hblks",
            "hblkhd",
            "usmblks",
            "fsmblks",
            "uordblks",
            "fordblks",
            "keepcost",
        )
    ]


@cache
def get_glibc() -> "ctypes.CDLL | None":
    try:
        libc = ctypes.CDLL("libc.so.6")
    except OSError:
        logger.warning("Not Linux, not starting malloc_trim thread")
        return None

    try:
        libc.gnu_get_libc_version
    except AttributeError:
        logger.warning(
            "Not glibc or glibc version too old, not starting malloc_trim thread"
        )
        return None

    # https://man7.org/linux/man-pages/man3/gnu_get_libc_version.3.html
    # since glibc 2.1
    gnu_get_libc_version = libc.gnu_get_libc_version
    gnu_get_libc_version.argtypes = []
    gnu_get_libc_version.restype = ctypes.c_char_p

    logger.info("glibc", version=gnu_get_libc_version().decode())

    # https://man7.org/linux/man-pages/man3/mallinfo.3.html
    # since glibc 2.0
    mallinfo = libc.mallinfo
    mallinfo.argtypes = []
    mallinfo.restype = MallInfo

    # https://man7.org/linux/man-pages/man3/malloc_stats.3.html
    # since glibc 2.0
    libc.malloc_stats.restype = None

    # https://man7.org/linux/man-pages/man3/malloc_trim.3.html
    # since glibc 2.0
    malloc_trim = libc.malloc_trim
    malloc_trim.argtypes = [ctypes.c_size_t]
    malloc_trim.restype = ctypes.c_int

    return libc


MEGABYTE = 1024 * 1024


def periodically_run_malloc_trim(libc: ctypes.CDLL, pad: int) -> NoReturn:
    while True:
        time.sleep(120)

        mallinfo_result = libc.mallinfo()
        if mallinfo_result.keepcost > pad:
            libc.malloc_trim(pad)


def maybe_start_malloc_trim_thread() -> "threading.Thread | None":
    libc = get_glibc()
    if libc is None:
        return None

    malloc_trim_thread = threading.Thread(
        target=periodically_run_malloc_trim,
        args=(libc, MEGABYTE),
        name="malloc_trim",
        daemon=True,
    )
    malloc_trim_thread.start()

    logger.info("Started malloc trim_thread")

    return malloc_trim_thread


def maybe_start_malloc_stats_thread() -> "threading.Thread | None":
    libc = get_glibc()
    if libc is None:
        return None

    malloc_stats_thread = threading.Thread(
        target=periodicallty_print_malloc_stats, args=(libc,), daemon=True
    )
    malloc_stats_thread.start()

    return malloc_stats_thread


def periodicallty_print_malloc_stats(libc):
    while True:
        libc.malloc_stats()
        info = libc.mallinfo()
        fields = [(name, getattr(info, name)) for name, _ in info._fields_]
        print("Malloc info:")
        for name, value in fields:
            print(f"- {name}: {value}")
        print("sys._debugmallocstats()")
        sys._debugmallocstats()
        time.sleep(60)
