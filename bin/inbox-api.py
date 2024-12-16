#!/usr/bin/env python

import os
import sys
from typing import Any

import click
import werkzeug.serving
from setproctitle import setproctitle  # type: ignore[import-not-found]

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
from inbox.util.startup import load_overrides

syncback: Any = None


@click.command()
@click.option(
    "--prod/--no-prod",
    default=False,
    help="Disables the autoreloader and potentially other non-production features.",
)
@click.option(
    "-c", "--config", default=None, help="Path to JSON configuration file."
)
@click.option("-p", "--port", default=5555, help="Port to run flask app on.")
def main(prod, config, port) -> None:  # type: ignore[no-untyped-def]
    """Launch the Nylas API service."""
    level = os.environ.get("LOGLEVEL", inbox_config.get("LOGLEVEL"))
    configure_logging(log_level=level)

    maybe_enable_rollbar()

    if config is not None:
        config_path = os.path.abspath(config)  # noqa: PTH100
        load_overrides(config_path)

    start(port=int(port), use_reloader=not prod)


def start(*, port: int, use_reloader: bool = False) -> None:
    # We need to import this down here, because this in turn imports
    # ignition.engine, which has to happen *after* we read any config overrides
    # for the database parameters. Boo for imports with side-effects.
    from inbox.api.srv import app

    nylas_logger = get_logger()

    nylas_logger.info("Starting API server", port=port)
    werkzeug.serving.run_simple("", port, app, use_reloader=use_reloader)


if __name__ == "__main__":
    main()
