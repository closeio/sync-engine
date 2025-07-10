#!/usr/bin/env python

from sys import exit

import click

from inbox.error_handling import maybe_enable_error_reporting
from inbox.heartbeat.config import (  # type: ignore[attr-defined]
    REPORT_DATABASE,
    STATUS_DATABASE,
    _get_redis_client,
    get_redis_client,
)


@click.command()
@click.option("--host", "-h", type=str)
@click.option("--port", "-p", type=int, default=6379)
def main(host, port) -> None:  # type: ignore[no-untyped-def]
    maybe_enable_error_reporting()

    if host:
        status_client = _get_redis_client(host, port, STATUS_DATABASE)
        report_client = _get_redis_client(host, port, REPORT_DATABASE)
    else:
        status_client = get_redis_client(STATUS_DATABASE)
        report_client = get_redis_client(REPORT_DATABASE)
    status_client.flushdb()
    report_client.flushdb()
    exit(0)


if __name__ == "__main__":
    main()
