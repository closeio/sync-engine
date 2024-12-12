#!/usr/bin/env python


import click

from inbox.config import config
from inbox.error_handling import maybe_enable_rollbar
from inbox.ignition import EngineManager, reset_invalid_autoincrements


@click.command()
@click.option("--dry-run", is_flag=True)
def reset_db(dry_run):
    maybe_enable_rollbar()

    database_hosts = config.get_required("DATABASE_HOSTS")
    database_users = config.get_required("DATABASE_USERS")
    # Do not include disabled shards since application services do not use them.
    engine_manager = EngineManager(
        database_hosts, database_users, include_disabled=False
    )

    for host in database_hosts:
        for shard in host["SHARDS"]:
            if shard.get("DISABLED"):
                continue
            key = int(shard["ID"])
            engine = engine_manager.engines[key]
            schema = shard["SCHEMA_NAME"]

            print(f"Resetting invalid autoincrements for database: {schema}")
            reset_tables = reset_invalid_autoincrements(
                engine, schema, key, dry_run
            )
            if dry_run:
                print("dry_run=True")
            if reset_tables:
                print("Reset tables: {}".format(", ".join(reset_tables)))
            else:
                print(f"Schema {schema} okay")


if __name__ == "__main__":
    reset_db()
