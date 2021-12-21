#!/usr/bin/env python
from gevent import monkey

monkey.patch_all()

import click

from inbox.contacts.search import index_namespace
from inbox.error_handling import maybe_enable_rollbar
from inbox.logging import configure_logging, get_logger

configure_logging()
log = get_logger()


@click.command()
@click.argument("namespace_ids", nargs=-1)
def main(namespace_ids):
    """
    Idempotently index the given namespace_ids.

    """
    maybe_enable_rollbar()

    for namespace_id in namespace_ids:
        log.info("indexing namespace {namespace_id}".format(namespace_id=namespace_id))
        index_namespace(namespace_id)


if __name__ == "__main__":
    main()
