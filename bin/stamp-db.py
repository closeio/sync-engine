#!/usr/bin/env python  # noqa: N999


import os
import sys

import alembic.command
import alembic.config
import alembic.util

from inbox.config import config
from inbox.error_handling import maybe_enable_rollbar


def main(revision_id) -> None:
    maybe_enable_rollbar()

    alembic_ini_filename = os.environ.get("ALEMBIC_INI_PATH", "alembic.ini")
    assert os.path.isfile(  # noqa: PTH113
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
                print(f"Stamping shard_id {key}")
                alembic_cfg = alembic.config.Config(alembic_ini_filename)
                alembic_cfg.set_main_option("shard_id", str(key))
                alembic.command.stamp(alembic_cfg, revision_id)
                print(f"Stamped shard_id {key}\n")
            except alembic.util.CommandError as e:
                print(f"FAILED to stamp shard_id {key} with error: {e!s}")
                continue


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: stamp-db revision_id")
        sys.exit(-1)

    main(sys.argv[1])
