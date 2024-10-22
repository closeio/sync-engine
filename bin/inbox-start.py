#!/usr/bin/env python


import os
import platform
import signal
import socket
import sys

import click
import setproctitle

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

    http_frontend = SyncHTTPFrontend(
        sync_service, port, enable_tracer, enable_profiler_api
    )
    if enable_tracer:
        sync_service.register_pending_avgs_provider(http_frontend)
    http_frontend.start()

    sync_service.run()

    print("\033[94mNylas Sync Engine exiting...\033[0m", file=sys.stderr)


if __name__ == "__main__":
    main()
