import sys
import time
from contextlib import contextmanager

from sqlalchemy import event  # type: ignore[import-untyped]
from sqlalchemy.exc import OperationalError  # type: ignore[import-untyped]
from sqlalchemy.ext.horizontal_shard import (  # type: ignore[import-untyped]
    ShardedSession,
)
from sqlalchemy.orm.session import Session  # type: ignore[import-untyped]

from inbox.config import config
from inbox.ignition import engine_manager
from inbox.logging import find_first_app_frame_and_name, get_logger
from inbox.util.stats import statsd_client

log = get_logger()


MAX_SANE_TRX_TIME_MS = 30000


def new_session(  # type: ignore[no-untyped-def]  # noqa: ANN201
    engine, versioned: bool = True
):
    """Returns a session bound to the given engine."""  # noqa: D401
    session = Session(bind=engine, autoflush=True, autocommit=False)

    if versioned:
        configure_versioning(session)

        # Make statsd calls for transaction times
        transaction_start_map = {}
        frame, modname = find_first_app_frame_and_name(
            ignores=[
                "sqlalchemy",
                "inbox.models.session",
                "inbox.logging",
                "contextlib",
            ]
        )
        funcname = frame.f_code.co_name
        modname = modname.replace(".", "-")
        metric_name = f"db.{engine.url.database}.{modname}.{funcname}"

        @event.listens_for(session, "after_begin")
        def after_begin(  # type: ignore[no-untyped-def]
            session, transaction, connection
        ) -> None:
            # It's okay to key on the session object here, because each session
            # binds to only one engine/connection. If this changes in the
            # future such that a session may encompass multiple engines, then
            # we'll have to get more sophisticated.
            transaction_start_map[session] = time.time()

        @event.listens_for(session, "after_commit")
        @event.listens_for(session, "after_rollback")
        def end(session) -> None:  # type: ignore[no-untyped-def]
            start_time = transaction_start_map.get(session)
            if not start_time:
                return

            del transaction_start_map[session]

            t = time.time()
            latency = int((t - start_time) * 1000)
            if config.get("ENABLE_DB_TXN_METRICS", False):
                statsd_client.timing(metric_name, latency)
                statsd_client.incr(metric_name)
            if latency > MAX_SANE_TRX_TIME_MS:
                log.warning(
                    "Long transaction",
                    latency=latency,
                    modname=modname,
                    funcname=funcname,
                )

    return session


def configure_versioning(session):  # type: ignore[no-untyped-def]  # noqa: ANN201
    from inbox.models.transaction import (
        bump_redis_txn_id,
        create_revisions,
        increment_versions,
        propagate_changes,
    )

    @event.listens_for(session, "before_flush")
    def before_flush(  # type: ignore[no-untyped-def]
        session, flush_context, instances
    ) -> None:
        propagate_changes(session)
        increment_versions(session)

    @event.listens_for(session, "after_flush")
    def after_flush(  # type: ignore[no-untyped-def]
        session, flush_context
    ) -> None:
        """
        Hook to log revision snapshots. Must be post-flush in order to
        grab object IDs on new objects.

        """  # noqa: D401
        # Note: `bump_redis_txn_id` __must__ come first. `create_revisions`
        # creates new objects which haven't been flushed to the db yet.
        # `bump_redis_txn_id` looks at objects on the session and expects them
        # to have already have an id (since they've already been flushed to the
        # db)
        try:
            bump_redis_txn_id(session)
        except Exception:
            log.exception("bump_redis_txn_id exception")
        create_revisions(session)

    return session


@contextmanager
def session_scope(id_, versioned: bool = True):  # type: ignore[no-untyped-def]  # noqa: ANN201
    """
    Provide a transactional scope around a series of operations.

    Takes care of rolling back failed transactions and closing the session
    when it goes out of scope.

    Note that sqlalchemy automatically starts a new database transaction when
    the session is created, and restarts a new transaction after every commit()
    on the session. Your database backend's transaction semantics are important
    here when reasoning about concurrency.

    Parameters
    ----------
    id_ : int
        Object primary key to grab a session for.

    versioned : bool
        Do you want to enable the transaction log?

    Yields
    ------
    Session
        The created session.

    """
    engine = engine_manager.get_for_id(id_)
    session = new_session(engine, versioned)

    try:
        if config.get("LOG_DB_SESSIONS"):
            start_time = time.time()
            frame = sys._getframe()
            assert frame  # type: ignore[truthy-bool]
            assert frame.f_back
            assert frame.f_back.f_back
            calling_frame = frame.f_back.f_back
            call_loc = "{}:{}".format(
                calling_frame.f_globals.get("__name__"), calling_frame.f_lineno
            )
            logger = log.bind(
                engine_id=id(engine), session_id=id(session), call_loc=call_loc
            )
            logger.info(
                "creating db_session", sessions_used=engine.pool.checkedout()
            )
        yield session
        session.commit()
    except BaseException as exc:
        try:
            session.rollback()
            raise
        except OperationalError:
            log.warning(
                "Encountered OperationalError on rollback",
                original_exception=type(exc),
            )
            raise exc  # noqa: B904
    finally:
        if config.get("LOG_DB_SESSIONS"):
            lifetime = (
                time.time() - start_time  # type: ignore[possibly-undefined]
            )
            logger.info(  # type: ignore[possibly-undefined]
                "closing db_session",
                lifetime=lifetime,
                sessions_used=engine.pool.checkedout(),
            )
        session.close()


@contextmanager
def session_scope_by_shard_id(  # type: ignore[no-untyped-def]  # noqa: ANN201
    shard_id, versioned: bool = True
):
    key = shard_id << 48

    with session_scope(key, versioned) as db_session:
        yield db_session


# GLOBAL (cross-shard) queries. USE WITH CAUTION.


def shard_chooser(  # type: ignore[no-untyped-def]  # noqa: ANN201
    mapper, instance, clause=None
):
    return str(engine_manager.shard_key_for_id(instance.id))


def id_chooser(query, ident):  # type: ignore[no-untyped-def]  # noqa: ANN201
    # STOPSHIP(emfree): is ident a tuple here???
    # TODO[k]: What if len(list) > 1?
    if isinstance(ident, list) and len(ident) == 1:
        ident = ident[0]
    return [str(engine_manager.shard_key_for_id(ident))]


def query_chooser(query):  # type: ignore[no-untyped-def]  # noqa: ANN201
    return [str(k) for k in engine_manager.engines]


@contextmanager
def global_session_scope():  # type: ignore[no-untyped-def]  # noqa: ANN201
    shards = {str(k): v for k, v in engine_manager.engines.items()}
    session = ShardedSession(
        shard_chooser=shard_chooser,
        id_chooser=id_chooser,
        query_chooser=query_chooser,
        shards=shards,
    )
    # STOPSHIP(emfree): need instrumentation and proper exception handling
    # here.
    try:
        yield session
    finally:
        session.close()
