#!/usr/bin/env python
"""
Run the syncback service separately. You should run this if you run the
API under something like gunicorn. (For convenience, the bin/inbox-api script
also starts up the syncback service.)

"""


import os
import sys

import click
from setproctitle import setproctitle

from inbox.config import config as inbox_config
from inbox.error_handling import maybe_enable_rollbar
from inbox.logging import configure_logging
from inbox.mailsync.frontend import SyncbackHTTPFrontend
from inbox.transactions.actions import SyncbackService
from inbox.util.logging_helper import reconfigure_logging
from inbox.util.startup import load_overrides, preflight


@click.command()
@click.option(
    "--prod/--no-prod",
    default=False,
    help="Disables the autoreloader and potentially other non-production features.",
)
@click.option(
    "-c", "--config", default=None, help="Path to JSON configuration file."
)
@click.option(
    "--process_num",
    default=0,
    help="This process's number in the process group: a unique "
    "number satisfying 0 <= process_num < total_processes.",
)
@click.option(
    "--syncback-id",
    default=0,
    type=int,
    help="This sync instance's id: a unique number assigned to "
    "each syncback instance.",
)
@click.option(
    "--enable-profiler/--disable-profiler",
    default=False,
    help="Enables the CPU profiler web API",
)
def main(prod, config, process_num, syncback_id, enable_profiler) -> None:
    """Launch the actions syncback service."""
    setproctitle(f"syncback-{process_num}")

    maybe_enable_rollbar()

    print("Python", sys.version, file=sys.stderr)

    if config is not None:
        config_path = os.path.abspath(config)
        load_overrides(config_path)
    level = os.environ.get("LOGLEVEL", inbox_config.get("LOGLEVEL"))
    configure_logging(log_level=level)
    reconfigure_logging()

    total_processes = int(os.environ.get("SYNCBACK_PROCESSES", 1))

    def start():
        # Start the syncback service, and just hang out forever
        syncback = SyncbackService(syncback_id, process_num, total_processes)

        if enable_profiler:
            inbox_config["DEBUG_PROFILING_ON"] = True

        port = 16384 + process_num
        enable_profiler_api = inbox_config.get("DEBUG_PROFILING_ON")
        frontend = SyncbackHTTPFrontend(port, enable_profiler_api)
        frontend.start()

        syncback.start()
        syncback.join()

    if prod:
        start()
    else:
        preflight()
        from werkzeug.serving import run_with_reloader

        run_with_reloader(start)


if __name__ == "__main__":
    main()
