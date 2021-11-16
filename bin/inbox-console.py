#!/usr/bin/env python
from gevent import monkey

monkey.patch_all()

import sys

if sys.version_info < (3,):
    import gevent_openssl

    gevent_openssl.monkey_patch()

from setproctitle import setproctitle

setproctitle("inbox-console")

import click

from inbox.logging import get_logger

log = get_logger()

from inbox.console import start_client_console, start_console
from inbox.error_handling import maybe_enable_rollbar


@click.command()
@click.option(
    "-e",
    "--email_address",
    default=None,
    help="Initialize a crispin client for a particular account.",
)
@click.option("-c", "--client", is_flag=True, help="Start a repl with an APIClient")
def console(email_address, client):
    """ REPL for Nylas. """
    maybe_enable_rollbar()

    if client:
        start_client_console(email_address)
    else:
        start_console(email_address)


if __name__ == "__main__":
    console()
