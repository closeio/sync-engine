#!/usr/bin/env python


from setproctitle import setproctitle  # type: ignore[import-not-found]

setproctitle("inbox-console")

import click

from inbox.logging import get_logger

log = get_logger()

from inbox.console import start_client_console, start_console
from inbox.error_handling import maybe_enable_error_reporting


@click.command()
@click.option(
    "-e",
    "--email_address",
    default=None,
    help="Initialize a crispin client for a particular account.",
)
@click.option(
    "-c", "--client", is_flag=True, help="Start a repl with an APIClient"
)
def console(email_address, client) -> None:  # type: ignore[no-untyped-def]
    """REPL for Nylas."""
    maybe_enable_error_reporting()

    if client:
        start_client_console(email_address)
    else:
        start_console(email_address)


if __name__ == "__main__":
    console()
