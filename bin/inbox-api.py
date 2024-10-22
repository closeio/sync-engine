#!/usr/bin/env python

import os
import sys

import click
import werkzeug.serving
from setproctitle import setproctitle

setproctitle("inbox-api")


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
from inbox.mailsync.frontend import SyncbackHTTPFrontend
from inbox.util.startup import load_overrides

syncback = None
http_server = None


@click.command()
@click.option(
    "--prod/--no-prod",
    default=False,
    help="Disables the autoreloader and potentially other non-production features.",
)
@click.option(
    "--start-syncback/--no-start-syncback",
    default=True,
    help="Also start the syncback service",
)
@click.option(
    "--enable-profiler/--disable-profiler",
    default=False,
    help="Enables the CPU profiler web API",
)
@click.option("-c", "--config", default=None, help="Path to JSON configuration file.")
@click.option("-p", "--port", default=5555, help="Port to run flask app on.")
def main(prod, start_syncback, config, port, enable_profiler):
    """Launch the Nylas API service."""
    level = os.environ.get("LOGLEVEL", inbox_config.get("LOGLEVEL"))
    configure_logging(log_level=level)

    maybe_enable_rollbar()

    if config is not None:
        config_path = os.path.abspath(config)
        load_overrides(config_path)

    start(
        port=int(port),
        start_syncback=start_syncback,
        enable_tracer=False,
        enable_profiler=enable_profiler,
        use_reloader=not prod,
    )


def start(
    *,
    port: int,
    start_syncback: bool,
    enable_tracer: bool,
    enable_profiler: bool,
    use_reloader: bool = False
) -> None:
    # We need to import this down here, because this in turn imports
    # ignition.engine, which has to happen *after* we read any config overrides
    # for the database parameters. Boo for imports with side-effects.
    from inbox.api.srv import app

    if start_syncback:
        # start actions service
        from inbox.transactions.actions import SyncbackService

        if enable_profiler:
            inbox_config["DEBUG_PROFILING_ON"] = True
        enable_profiler_api = inbox_config.get("DEBUG_PROFILING_ON")

        syncback = SyncbackService(0, 0, 1)
        profiling_frontend = SyncbackHTTPFrontend(
            port + 1, enable_tracer, enable_profiler_api
        )
        profiling_frontend.start()
        syncback.start()

    nylas_logger = get_logger()

    nylas_logger.info("Starting API server", port=port)
    werkzeug.serving.run_simple("", port, app, use_reloader=use_reloader)

    if start_syncback:
        syncback.join()


if __name__ == "__main__":
    main()
