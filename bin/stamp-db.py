#!/usr/bin/env python
from __future__ import print_function

import os
import sys

import alembic.command
import alembic.config
import alembic.util

from inbox.config import config
from inbox.error_handling import maybe_enable_rollbar


def main(revision_id):
    maybe_enable_rollbar()

    alembic_ini_filename = os.environ.get("ALEMBIC_INI_PATH", "alembic.ini")
    assert os.path.isfile(
        alembic_ini_filename
    ), "Missing alembic.ini file at {}".format(alembic_ini_filename)

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
                print("Stamping shard_id {}".format(key))
                alembic_cfg = alembic.config.Config(alembic_ini_filename)
                alembic_cfg.set_main_option("shard_id", str(key))
                alembic.command.stamp(alembic_cfg, revision_id)
                print("Stamped shard_id {}\n".format(key))
            except alembic.util.CommandError as e:
                print("FAILED to stamp shard_id {} with error: {}".format(key, str(e)))
                continue


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: stamp-db revision_id")
        sys.exit(-1)

    main(sys.argv[1])
