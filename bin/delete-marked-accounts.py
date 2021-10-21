#!/usr/bin/env python
"""
Searches for accounts that are marked for deletion and deletes
all of their data

Includes:
* All data in the database.
* Account liveness/status data (in Redis).

"""
from __future__ import absolute_import

from gevent import monkey

monkey.patch_all()

import logging

import click
import gevent

from inbox.config import config
from inbox.error_handling import maybe_enable_rollbar
from inbox.logging import configure_logging, get_logger
from inbox.models.util import batch_delete_namespaces, get_accounts_to_delete

configure_logging(logging.INFO)
log = get_logger()


@click.command()
@click.option("--throttle", is_flag=True)
@click.option("--dry-run", is_flag=True)
def run(throttle, dry_run):
    maybe_enable_rollbar()

    pool = []

    for host in config["DATABASE_HOSTS"]:
        log.info("Spawning delete process for host", host=host["HOSTNAME"])
        pool.append(gevent.spawn(delete_account_data, host, throttle, dry_run))

    gevent.joinall(pool)


def delete_account_data(host, throttle, dry_run):
    while True:
        for shard in host["SHARDS"]:
            # Ensure shard is explicitly not marked as disabled
            if "DISABLED" in shard and not shard["DISABLED"]:
                namespace_ids = get_accounts_to_delete(shard["ID"])
                batch_delete_namespaces(namespace_ids, throttle, dry_run)
        gevent.sleep(600)


if __name__ == "__main__":
    run()
