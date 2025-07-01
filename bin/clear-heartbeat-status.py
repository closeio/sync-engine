#!/usr/bin/env python


from sys import exit

import click

from inbox.config import config
from inbox.error_handling import maybe_enable_error_reporting
from inbox.heartbeat.status import clear_heartbeat_status
from inbox.logging import configure_logging, get_logger

configure_logging(config.get("LOGLEVEL"))
log = get_logger()


@click.command()
@click.option("--host", "-h", type=str)
@click.option("--port", "-p", type=int, default=6379)
@click.option("--account-id", "-a", type=int, required=True)
@click.option("--folder-id", "-f", type=int)
@click.option("--device-id", "-d", type=int)
def main(  # type: ignore[no-untyped-def]
    host, port, account_id, folder_id, device_id
) -> None:
    maybe_enable_error_reporting()

    print("Clearing heartbeat status...")
    n = clear_heartbeat_status(  # type: ignore[call-arg]
        account_id, folder_id, device_id, host, port
    )
    print(f"{n} folders cleared.")
    exit(0)


if __name__ == "__main__":
    main()
