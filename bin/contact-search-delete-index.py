#!/usr/bin/env python
import click

from inbox.contacts.search import delete_namespace_indexes as delete_indexes
from inbox.error_handling import maybe_enable_rollbar
from inbox.logging import configure_logging, get_logger

configure_logging()
log = get_logger()


@click.command()
@click.argument("namespace_ids")
def delete_namespace_indexes(namespace_ids):
    """
    Delete the CloudSearch indexes for a list of namespaces, specified by id.

    """
    maybe_enable_rollbar()

    delete_indexes(namespace_ids)


if __name__ == "__main__":
    delete_namespace_indexes()
