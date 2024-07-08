#!/usr/bin/env python


import subprocess
import sys
from typing import Optional

import click

from inbox.config import config
from inbox.error_handling import maybe_enable_rollbar


@click.command()
@click.option("--shard-num", type=int)
@click.option("-e", "--execute", type=str, help="Execute the statement and quit")
@click.option(
    "-B",
    "--batch",
    is_flag=True,
    default=False,
    help="Print results using tab as the column separator",
)
def main(shard_num: Optional[int], execute: Optional[str], batch: bool) -> None:
    maybe_enable_rollbar()

    users = config.get_required("DATABASE_USERS")

    creds = dict(hostname=None, username=None, password=None, db_name=None)

    database_hosts = config.get_required("DATABASE_HOSTS")
    if shard_num is None:
        if len(database_hosts) == 1 and len(database_hosts[0]["SHARDS"]) == 1:
            shard_num = database_hosts[0]["SHARDS"][0]["ID"]
            print("No shard provided, falling back to", shard_num)
        else:
            print("There are many shards, please provide --shard-num", file=sys.stderr)
            sys.exit(1)

    for database in database_hosts:
        for shard in database["SHARDS"]:
            if shard["ID"] == shard_num:
                creds["hostname"] = database["HOSTNAME"]
                hostname = creds["hostname"]
                creds["username"] = users[hostname]["USER"]
                creds["password"] = users[hostname]["PASSWORD"]
                creds["db_name"] = shard["SCHEMA_NAME"]
                break

    for key in creds.keys():
        if creds[key] is None:
            print(f"Error: {key} is None")
            sys.exit(-1)

    process_arguments = [
        "mysql",
        "-h" + creds["hostname"],
        "-u" + creds["username"],
        "-D" + creds["db_name"],
        "-p" + creds["password"],
        "--safe-updates",
    ]
    if batch:
        process_arguments.append("--batch")
    if execute is not None:
        process_arguments.append("--execute")
        process_arguments.append(execute)

    process = subprocess.Popen(process_arguments)
    process.wait()


if __name__ == "__main__":
    main()
