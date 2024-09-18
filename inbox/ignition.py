import time
import weakref
from socket import gethostname
from typing import Any, Dict, MutableMapping
from urllib.parse import quote_plus as urlquote
from warnings import filterwarnings

import gevent
import limitlion
import redis
from sqlalchemy import create_engine, event

from inbox.config import config
from inbox.logging import find_first_app_frame_and_name, get_logger
from inbox.sqlalchemy_ext.util import (
    ForceStrictModePool,
    disabled_dubiously_many_queries_warning,
)
from inbox.util.stats import statsd_client

filterwarnings("ignore", message="Invalid utf8mb4 character string")
log = get_logger()


DB_POOL_SIZE = config.get_required("DB_POOL_SIZE")
# Sane default of max overflow=5 if value missing in config.
DB_POOL_MAX_OVERFLOW = config.get("DB_POOL_MAX_OVERFLOW") or 5
DB_POOL_TIMEOUT = config.get("DB_POOL_TIMEOUT") or 60


pool_tracker: MutableMapping[Any, Dict[str, Any]] = weakref.WeakKeyDictionary()


hub = gevent.hub.get_hub()


# See
# https://github.com/PyMySQL/mysqlclient-python/blob/master/samples/waiter_gevent.py
def gevent_waiter(fd):
    hub.wait(hub.loop.io(fd, 1))


def build_uri(username, password, hostname, port, database_name):
    uri_template = (
        "mysql+mysqldb://{username}:{password}@{hostname}"
        ":{port}/{database_name}?charset=utf8mb4"
    )
    return uri_template.format(
        username=urlquote(username),
        password=urlquote(password),
        hostname=urlquote(hostname),
        port=port,
        database_name=urlquote(database_name),
    )


def engine(
    database_name,
    database_uri,
    pool_size=DB_POOL_SIZE,
    max_overflow=DB_POOL_MAX_OVERFLOW,
    pool_timeout=DB_POOL_TIMEOUT,
    echo=False,
    use_gevent=True,
):
    connect_args = {"binary_prefix": True, "charset": "utf8mb4", "connect_timeout": 60}

    if use_gevent:
        connect_args["waiter"] = gevent_waiter

    engine = create_engine(
        database_uri,
        poolclass=ForceStrictModePool,
        isolation_level="READ COMMITTED",
        echo=echo,
        pool_size=pool_size,
        pool_timeout=pool_timeout,
        pool_recycle=3600,
        max_overflow=max_overflow,
        connect_args=connect_args,
    )

    @event.listens_for(engine, "checkout")
    def receive_checkout(dbapi_connection, connection_record, connection_proxy):
        """Log checkedout and overflow when a connection is checked out"""
        hostname = gethostname().replace(".", "-")
        process_name = str(config.get("PROCESS_NAME", "main_process"))

        if config.get("ENABLE_DB_TXN_METRICS", False):
            statsd_client.gauge(
                ".".join(
                    ["dbconn", database_name, hostname, process_name, "checkedout"]
                ),
                connection_proxy._pool.checkedout(),
            )

            statsd_client.gauge(
                ".".join(["dbconn", database_name, hostname, process_name, "overflow"]),
                connection_proxy._pool.overflow(),
            )

        # Keep track of where and why this connection was checked out.
        log = get_logger()
        context = log._context._dict.copy()
        f, name = find_first_app_frame_and_name(
            ignores=["sqlalchemy", "inbox.ignition", "inbox.logging"]
        )
        source = f"{name}:{f.f_lineno}"

        pool_tracker[dbapi_connection] = {
            "source": source,
            "context": context,
            "checkedout_at": time.time(),
        }

    @event.listens_for(engine, "checkin")
    def receive_checkin(dbapi_connection, connection_record):
        if dbapi_connection in pool_tracker:
            del pool_tracker[dbapi_connection]

    return engine


class EngineManager:
    def __init__(self, databases, users, include_disabled=False, use_gevent=True):
        self.engines = {}
        self._engine_zones = {}
        keys = set()
        schema_names = set()
        use_proxysql = config.get("USE_PROXYSQL", False)
        for database in databases:
            hostname = "127.0.0.1" if use_proxysql else database["HOSTNAME"]
            port = database["PORT"]
            username = users[hostname]["USER"]
            password = users[hostname]["PASSWORD"]
            zone = database.get("ZONE")
            for shard in database["SHARDS"]:
                schema_name = shard["SCHEMA_NAME"]
                key = shard["ID"]

                # Perform some sanity checks on the configuration.
                assert isinstance(key, int)
                assert key not in keys, f"Shard key collision: key {key} is repeated"
                assert (
                    schema_name not in schema_names
                ), f"Shard name collision: {schema_name} is repeated"
                keys.add(key)
                schema_names.add(schema_name)

                if shard.get("DISABLED") and not include_disabled:
                    log.info(
                        "Not creating engine for disabled shard",
                        schema_name=schema_name,
                        hostname=hostname,
                        key=key,
                    )
                    continue

                uri = build_uri(
                    username=username,
                    password=password,
                    database_name=schema_name,
                    hostname=hostname,
                    port=port,
                )
                self.engines[key] = engine(schema_name, uri, use_gevent=use_gevent)
                self._engine_zones[key] = zone

        log.info("EngineManager initialized", use_gevent=use_gevent)

    def shard_key_for_id(self, id_):
        return 0

    def get_for_id(self, id_):
        return self.engines[self.shard_key_for_id(id_)]

    def zone_for_id(self, id_):
        return self._engine_zones[self.shard_key_for_id(id_)]

    def shards_for_zone(self, zone):
        return [k for k, z in self._engine_zones.items() if z == zone]


engine_manager = EngineManager(
    config.get_required("DATABASE_HOSTS"),
    config.get_required("DATABASE_USERS"),
    use_gevent=config.get("USE_GEVENT", True),
)


def init_db(engine, key=0):
    """
    Make the tables.

    This is called only from bin/create-db, which is run during setup.
    Previously we allowed this to run everytime on startup, which broke some
    alembic revisions by creating new tables before a migration was run.
    From now on, we should ony be creating tables+columns via SQLalchemy *once*
    and all subsequent changes done via migration scripts.

    """
    from sqlalchemy import DDL, event

    from inbox.models.base import MailSyncBase

    # Hopefully setting auto_increment via an event listener will make it safe
    # to execute this function multiple times.
    # STOPSHIP(emfree): verify
    increment = (key << 48) + 1
    for table in MailSyncBase.metadata.tables.values():
        event.listen(
            table,
            "after_create",
            DDL(f"ALTER TABLE {table} AUTO_INCREMENT={increment}"),
        )
    with disabled_dubiously_many_queries_warning():
        MailSyncBase.metadata.create_all(engine)


def verify_db(engine, schema, key):
    from inbox.models.base import MailSyncBase

    query = """SELECT AUTO_INCREMENT from information_schema.TABLES where
    table_schema='{}' AND table_name='{}';"""

    verified = set()
    for table in MailSyncBase.metadata.sorted_tables:
        # ContactSearchIndexCursor does not need to be checked because there's
        # only one row in the table
        if str(table) == "contactsearchindexcursor":
            continue

        increment = engine.execute(query.format(schema, table)).scalar()
        if increment is not None:
            assert (increment >> 48) == key, "table: {}, increment: {}, key: {}".format(
                table, increment, key
            )
        else:
            # We leverage the following invariants about the sync
            # schema to make the assertion below: one, in the sync
            # schema, a table's id column is assigned the
            # auto_increment since we use this column as the
            # primary_key. Two, the only tables that have a None
            # auto_increment are inherited tables (like '*account',
            # '*thread' '*actionlog', 'recurringevent*'), because
            # their id column is instead a foreign_key on their
            # parent's id column.
            parent = list(table.columns["id"].foreign_keys)[0].column.table
            assert parent in verified
        verified.add(table)


def reset_invalid_autoincrements(engine, schema, key, dry_run=True):
    from inbox.models.base import MailSyncBase

    query = """SELECT AUTO_INCREMENT from information_schema.TABLES where
    table_schema='{}' AND table_name='{}';"""

    reset = set()
    for table in MailSyncBase.metadata.sorted_tables:
        increment = engine.execute(query.format(schema, table)).scalar()
        if increment is not None and (increment >> 48) != key:
            if not dry_run:
                reset_query = f"ALTER TABLE {table} AUTO_INCREMENT={(key << 48) + 1}"
                engine.execute(reset_query)
            reset.add(str(table))
    return reset


# Probably not the best place for this but for now
# we are using limitlion to protect the DBs so we
# should be able to assume this will be loaded
# before a DB is accessed.
redis_limitlion = redis.Redis(
    config["THROTTLE_REDIS_HOSTNAME"],
    int(config["REDIS_PORT"]),
    db=config.get("THROTTLE_REDIS_DB", 0),
)
limitlion.throttle_configure(redis_limitlion)

# these are _required_. nylas shouldn't start if these aren't present.
redis_txn = redis.Redis(
    config["TXN_REDIS_HOSTNAME"], int(config["REDIS_PORT"]), db=config["TXN_REDIS_DB"]
)
