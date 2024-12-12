#!/usr/bin/env python  # noqa: N999


from inbox.config import config
from inbox.error_handling import maybe_enable_rollbar
from inbox.ignition import EngineManager, verify_db


def main() -> None:
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

            print(f"Verifying database: {schema}")
            verify_db(engine, schema, key)


if __name__ == "__main__":
    main()
