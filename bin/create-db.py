#!/usr/bin/env python


import os

import alembic.command
import alembic.config
import click
import sqlalchemy  # type: ignore[import-untyped]

from inbox.config import config
from inbox.error_handling import maybe_enable_rollbar
from inbox.ignition import EngineManager, build_uri, init_db, verify_db
from inbox.sqlalchemy_ext.util import ForceStrictModePool


@click.command()
@click.option(
    "--target-hostname",
    default=None,
    help="Limit database initialization to only one host / set of shards",
)
@click.option("--host-ip", default=None)
def main(target_hostname, host_ip) -> None:  # type: ignore[no-untyped-def]
    maybe_enable_rollbar()

    database_hosts = config.get_required("DATABASE_HOSTS")
    database_users = config.get_required("DATABASE_USERS")
    engine_manager = EngineManager(
        database_hosts, database_users, include_disabled=True
    )
    for host in database_hosts:
        if target_hostname is not None and host["HOSTNAME"] != target_hostname:
            continue
        for shard in host["SHARDS"]:
            key = shard["ID"]
            assert isinstance(key, int)
            hostname = host["HOSTNAME"]
            connect_to = host_ip if host_ip else hostname
            mysql_user = database_users[hostname]["USER"]
            mysql_password = database_users[hostname]["PASSWORD"]
            base_uri = build_uri(
                username=mysql_user,
                password=mysql_password,
                hostname=connect_to,
                port=host["PORT"],
                database_name="",
            )
            base_engine = sqlalchemy.create_engine(
                base_uri,
                poolclass=ForceStrictModePool,
                connect_args={"binary_prefix": True},
            )

            schema_name = shard["SCHEMA_NAME"]
            print(f"Setting up database: {schema_name}")

            # Create the database IF needed.
            base_engine.execute(
                f"CREATE DATABASE IF NOT EXISTS {schema_name} DEFAULT CHARACTER "
                "SET utf8mb4 DEFAULT COLLATE utf8mb4_general_ci;"
            )

            engine = engine_manager.engines[int(key)]

            if engine.has_table("alembic_version"):
                # Verify alembic revision
                (current_revision,) = engine.execute(
                    "SELECT version_num from alembic_version"
                ).fetchone()
                assert (
                    current_revision
                ), "Need current revision in alembic_version table."
                print(
                    f"Already revisioned by alembic version: {current_revision}"
                )
            else:
                # Initialize shards, stamp alembic revision
                print("Initializing database.")
                init_db(engine, int(key))
                alembic_ini_filename = os.environ.get(
                    "ALEMBIC_INI_PATH", "alembic.ini"
                )
                assert os.path.isfile(  # noqa: PTH113
                    alembic_ini_filename
                ), f"Must have alembic.ini file at {alembic_ini_filename}"
                alembic_cfg = alembic.config.Config(alembic_ini_filename)
                # Alembic option values need to be strings.
                alembic_cfg.set_main_option("shard_id", str(key))

                print("Stamping with alembic revision.")
                alembic.command.stamp(alembic_cfg, "head")

            # Verify the database has been set up with correct auto_increments.
            print("Verifying database.")
            verify_db(engine, schema_name, int(key))

            print("Finished setting up database.\n")


if __name__ == "__main__":
    main()
