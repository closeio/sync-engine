#!/usr/bin/env python


import ctypes
import os
import platform
import signal
import socket
import sys
import threading
import time

import click
import memray
import pyinstrument
import setproctitle

from inbox.malloc_trim import maybe_start_malloc_trim_thread

# Check that the inbox package is installed. It seems Vagrant may sometimes
# fail to provision the box appropriately; this check is a reasonable
# approximation of "Did the setup script run?"
try:
    from inbox.config import config as inbox_config
except ImportError:
    sys.exit(
        "Could not find 'inbox' Python package installation. "
        "Maybe the Vagrant box provisioning didn't succeed?\n"
        "Try running sudo ./setup.sh"
    )

import inbox.thread_inspector
from inbox.error_handling import maybe_enable_rollbar
from inbox.logging import configure_logging, get_logger
from inbox.mailsync.frontend import SyncHTTPFrontend
from inbox.mailsync.service import SyncService
from inbox.util.logging_helper import reconfigure_logging
from inbox.util.startup import preflight

# Set a default timeout for sockets.
SOCKET_TIMEOUT = 2 * 60
socket.setdefaulttimeout(SOCKET_TIMEOUT)

esc = "\033"

banner = rf"""{esc}[1;95m
      _   _       _
     | \ | |     | |
     |  \| |_   _| | __ _ ___
     | . ` | | | | |/ _` / __|
     | |\  | |_| | | (_| \__ \
     \_| \_/\__, |_|\__,_|___/
             __/ |
            |___/
     {esc}[0m{esc}[94m
      S Y N C   E N G I N E

     {esc}[0m
     Use CTRL-C to stop.
    """


@click.command()
@click.option(
    "--prod/--no-prod",
    default=False,
    help="Disables the autoreloader and potentially other non-production features.",
)
@click.option(
    "--enable-profiler/--disable-profiler",
    default=False,
    help="Enables the CPU profiler web API",
)
@click.option("-c", "--config", default=None, help="Path to JSON configuration file.")
@click.option(
    "--process_num",
    default=0,
    help="This process's number in the process group: a unique "
    "number satisfying 0 <= process_num < total_processes.",
)
def main(prod, enable_profiler, config, process_num):
    """Launch the Nylas sync service."""
    threading.stack_size(524288)

    level = os.environ.get("LOGLEVEL", inbox_config.get("LOGLEVEL"))
    configure_logging(log_level=level)
    reconfigure_logging()

    maybe_enable_rollbar()

    if config is not None:
        from inbox.util.startup import load_overrides

        config_path = os.path.abspath(config)
        load_overrides(config_path)

    if not prod:
        preflight()

    total_processes = int(os.environ.get("MAILSYNC_PROCESSES", 1))

    setproctitle.setproctitle(f"sync-engine-{process_num}")

    log = get_logger()
    log.info(
        "start",
        components=["mail sync", "contact sync", "calendar sync"],
        host=platform.node(),
        process_num=process_num,
        total_processes=total_processes,
        recursion_limit=sys.getrecursionlimit(),
    )

    print(banner, file=sys.stderr)
    print(file=sys.stderr)
    print("Python", sys.version, file=sys.stderr)

    if enable_profiler:
        inbox_config["DEBUG_PROFILING_ON"] = True

    port = 16384 + process_num
    enable_profiler_api = inbox_config.get("DEBUG_PROFILING_ON")

    process_identifier = f"{platform.node()}:{process_num}"

    sync_service = SyncService(process_identifier, process_num)

    signal.signal(signal.SIGTERM, lambda *_: sync_service.stop())
    signal.signal(signal.SIGINT, lambda *_: sync_service.stop())
    signal.signal(signal.SIGUSR1, lambda *_: track_memory())
    signal.signal(signal.SIGUSR2, lambda *_: dump_threads())
    signal.signal(signal.SIGHUP, lambda *_: profile())
    prepare_malloc_stats()

    maybe_start_malloc_trim_thread()

    http_frontend = SyncHTTPFrontend(sync_service, port, enable_profiler_api)
    http_frontend.start()

    # trace()
    sync_service.run()

    print("\033[94mNylas Sync Engine exiting...\033[0m", file=sys.stderr)


tracker = None


def track_memory():
    global tracker

    if not tracker:
        tracker = memray.Tracker(
            f"bin/inbox-start-{int(time.time())}.bin", trace_python_allocators=True
        )
        tracker.__enter__()
    else:
        tracker.__exit__(None, None, None)
        tracker = None


profiler = None


def profile():
    global profiler

    time.sleep(1)

    if not profiler:
        profiler = pyinstrument.Profiler(0.000001)
        profiler.start()
    else:
        profiler.stop()
        profiler.write_html(f"bin/inbox-start-{int(time.time())}.html")
        profiler = None


def dump_threads():
    for thread in inbox.thread_inspector.enumerate():
        print("-->", thread, hex(thread.native_id))


def prepare_malloc_stats() -> None:
    malloc_stats_thread = threading.Thread(target=malloc_stats, daemon=True)
    malloc_stats_thread.start()


libc = ctypes.CDLL("libc.so.6")
libc.malloc_stats.restype = None


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


mallinfo = libc.mallinfo
mallinfo.argtypes = []
mallinfo.restype = MallInfo


def malloc_stats():
    while True:
        libc.malloc_stats()
        info = mallinfo()
        fields = [(name, getattr(info, name)) for name, _ in info._fields_]
        print("Malloc info:")
        for name, value in fields:
            print(f"- {name}: {value}")
        print("sys._debugmallocstats()")
        sys._debugmallocstats()
        time.sleep(60)


if __name__ == "__main__":
    main()
