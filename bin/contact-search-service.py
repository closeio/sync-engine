#!/usr/bin/env python
""" Start the contact search indexing service. """
import sys

if sys.version_info < (3,):
    import gevent_openssl

    gevent_openssl.monkey_patch()

from gevent import monkey

monkey.patch_all()

import os

import click
from setproctitle import setproctitle

from inbox.config import config as inbox_config
from inbox.error_handling import maybe_enable_rollbar
from inbox.logging import configure_logging
from inbox.util.startup import preflight

setproctitle("nylas-contact-search-index-service")


@click.command()
@click.option(
    "--prod/--no-prod",
    default=False,
    help="Disables the autoreloader and potentially other " "non-production features.",
)
@click.option("-c", "--config", default=None, help="Path to JSON configuration file.")
def main(prod, config):
    """ Launch the contact search index service. """
    level = os.environ.get("LOGLEVEL", inbox_config.get("LOGLEVEL"))
    configure_logging(log_level=level)

    maybe_enable_rollbar()

    if config is not None:
        from inbox.util.startup import load_overrides

        config_path = os.path.abspath(config)
        load_overrides(config_path)

    # import here to make sure config overrides are loaded
    from inbox.transactions.search import ContactSearchIndexService

    if not prod:
        preflight()

    contact_search_indexer = ContactSearchIndexService()

    contact_search_indexer.start()
    contact_search_indexer.join()


if __name__ == "__main__":
    main()
