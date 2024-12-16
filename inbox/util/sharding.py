from inbox.config import config


def get_shard_schemas():  # noqa: ANN201
    # Can't use engine_manager.engines here because it does not track
    # shard schemas.
    shard_schemas = {}
    database_hosts = config.get_required("DATABASE_HOSTS")
    for host in database_hosts:
        for shard in host["SHARDS"]:
            if not shard.get("DISABLED"):
                shard_id = shard["ID"]
                schema_name = shard["SCHEMA_NAME"]
                shard_schemas[shard_id] = schema_name
    return shard_schemas
