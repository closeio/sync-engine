#!/usr/bin/env python


import os

import alembic.command
import alembic.config
import alembic.util

from inbox.config import config
from inbox.error_handling import maybe_enable_rollbar


def main() -> None:
    maybe_enable_rollbar()

    alembic_ini_filename = os.environ.get("ALEMBIC_INI_PATH", "alembic.ini")
    assert os.path.isfile(
        alembic_ini_filename
    ), f"Missing alembic.ini file at {alembic_ini_filename}"

    database_hosts = config.get_required("DATABASE_HOSTS")

    for host in database_hosts:
        for shard in host["SHARDS"]:
            key = shard["ID"]

            if shard.get("DISABLED"):
                # Do not include disabled shards since application services
                # do not use them.
                continue

            key = shard["ID"]

            try:
                print(f"Upgrading shard_id {key}")
                alembic_cfg = alembic.config.Config(alembic_ini_filename)
                alembic_cfg.set_main_option("shard_id", str(key))
                alembic.command.upgrade(alembic_cfg, "head")
                print(f"Upgraded shard_id {key}\n")
            except alembic.util.CommandError as e:
                print(f"FAILED to upgrade shard_id {key} with error: {e!s}")
                continue


if __name__ == "__main__":
    main()
