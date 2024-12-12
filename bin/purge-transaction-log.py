#!/usr/bin/env python
"""
Deletes entries in the transaction older than `days_ago` days( as measured by
the created_at column)

"""


import logging
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import click

from inbox.config import config
from inbox.error_handling import maybe_enable_rollbar
from inbox.logging import configure_logging, get_logger
from inbox.models.util import purge_transactions

configure_logging(logging.INFO)
log = get_logger()


@click.command()
@click.option("--days-ago", type=int, default=60)
@click.option("--limit", type=int, default=1000)
@click.option("--throttle", is_flag=True)
@click.option("--dry-run", is_flag=True)
def run(days_ago, limit, throttle, dry_run) -> None:
    maybe_enable_rollbar()

    print("Python", sys.version, file=sys.stderr)

    with ThreadPoolExecutor(
        max_workers=len(config["DATABASE_HOSTS"])
    ) as executor:
        for host in config["DATABASE_HOSTS"]:
            executor.submit(
                purge_old_transactions,
                host,
                days_ago,
                limit,
                throttle,
                dry_run,
            )


def purge_old_transactions(host, days_ago, limit, throttle, dry_run) -> None:
    while True:
        for shard in host["SHARDS"]:
            # Ensure shard is explicitly not marked as disabled
            if "DISABLED" in shard and not shard["DISABLED"]:
                log.info(
                    "Spawning transaction purge process for shard",
                    shard_id=shard["ID"],
                )
                purge_transactions(
                    shard["ID"], days_ago, limit, throttle, dry_run
                )
            else:
                log.info(
                    "Will not spawn process for disabled shard",
                    shard_id=shard["ID"],
                )
        time.sleep(600)


if __name__ == "__main__":
    run()
