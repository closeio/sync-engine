#!/usr/bin/env python


from sys import exit

import click
from redis import BlockingConnectionPool, StrictRedis

from inbox.error_handling import maybe_enable_rollbar
from inbox.heartbeat.config import (
    MAX_CONNECTIONS,
    SOCKET_TIMEOUT,
    STATUS_DATABASE,
    WAIT_TIMEOUT,
)


@click.command()
@click.option("--host", "-h", type=str, default="localhost")
@click.option("--port", "-p", type=int, default=6379)
@click.option("--database", "-d", type=int, default=STATUS_DATABASE)
def main(host, port, database):
    maybe_enable_rollbar()

    connection_pool = BlockingConnectionPool(
        host=host,
        port=port,
        db=database,
        max_connections=MAX_CONNECTIONS,
        timeout=WAIT_TIMEOUT,
        socket_timeout=SOCKET_TIMEOUT,
    )

    client = StrictRedis(host, port, database, connection_pool=connection_pool)
    batch_client = client.pipeline()

    count = 0
    for name in client.scan_iter(count=100):
        if name == "ElastiCacheMasterReplicationTimestamp":
            continue
        batch_client.delete(name)
        count += 1

    batch_client.execute()
    print("{} heartbeats deleted!".format(count))
    exit(0)


if __name__ == "__main__":
    main()
