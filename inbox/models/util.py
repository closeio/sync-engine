import math
import time
from collections import OrderedDict

import limitlion
from sqlalchemy import desc, func
from sqlalchemy.orm.exc import NoResultFound

from inbox.error_handling import log_uncaught_errors
from inbox.heartbeat.status import clear_heartbeat_status
from inbox.ignition import redis_txn
from inbox.logging import get_logger
from inbox.models import Account, Block, Message, Namespace
from inbox.models.session import session_scope, session_scope_by_shard_id
from inbox.models.transaction import TXN_REDIS_KEY, Transaction
from inbox.util.blockstore import delete_from_blockstore
from inbox.util.stats import statsd_client

# Some tables have cascading deletes so the actual number of rows deleted
# can be much larger than the CHUNK_SIZE.
CHUNK_SIZE = 100

log = get_logger()

# Use a single throttle instance for rate limiting.  Limits will be applied
# across all db shards (same approach as the original check_throttle()).
bulk_throttle = limitlion.throttle_wait("bulk", rps=0.75, window=5)


def reconcile_message(new_message, session):
    """
    Check to see if the (synced) Message instance new_message was originally
    created/sent via the Nylas API (based on the X-Inbox-Uid header. If so,
    update the existing message with new attributes from the synced message
    and return it.

    """
    from inbox.models.message import Message

    if new_message.nylas_uid is None:
        # try to reconcile using other means
        q = session.query(Message).filter(
            Message.namespace_id == new_message.namespace_id,
            Message.data_sha256 == new_message.data_sha256,
        )
        return q.first()

    if "-" not in new_message.nylas_uid:
        # Old X-Inbox-Id format; use the old reconciliation strategy.
        existing_message = (
            session.query(Message)
            .filter(
                Message.namespace_id == new_message.namespace_id,
                Message.nylas_uid == new_message.nylas_uid,
                Message.is_created,
            )
            .first()
        )
        version = None
    else:
        # new_message has the new X-Inbox-Id format <public_id>-<version>
        # If this is an old version of a current draft, we want to:
        # * not commit a new, separate Message object for it
        # * not update the current draft with the old header values in the code
        #   below.
        expected_public_id, version = new_message.nylas_uid.split("-")
        existing_message = (
            session.query(Message)
            .filter(
                Message.namespace_id == new_message.namespace_id,
                Message.public_id == expected_public_id,
                Message.is_created,
            )
            .first()
        )

    if existing_message is None:
        return None

    if version is None or int(version) == existing_message.version:
        existing_message.message_id_header = new_message.message_id_header
        existing_message.references = new_message.references
        # Non-persisted instance attribute used by EAS.
        existing_message.parsed_body = new_message.parsed_body

    return existing_message


def transaction_objects():
    """
    Return the mapping from API object name - which becomes the
    Transaction.object_type - for models that generate Transactions (i.e.
    models that implement the HasRevisions mixin).

    """
    from inbox.models import (
        Block,
        Calendar,
        Category,
        Contact,
        Event,
        Message,
        Metadata,
        Thread,
    )

    return {
        "calendar": Calendar,
        "contact": Contact,
        "draft": Message,
        "event": Event,
        "file": Block,
        "message": Message,
        "thread": Thread,
        "label": Category,
        "folder": Category,
        "account": Account,
        "metadata": Metadata,
    }


def get_accounts_to_delete(shard_id):
    ids_to_delete = []
    with session_scope_by_shard_id(shard_id) as db_session:
        ids_to_delete = [
            (acc.id, acc.namespace.id)
            for acc in db_session.query(Account)
            if acc.is_marked_for_deletion
        ]
    return ids_to_delete


class AccountDeletionErrror(Exception):
    pass


def batch_delete_namespaces(ids_to_delete, throttle=False, dry_run=False):
    start = time.time()

    for account_id, namespace_id in ids_to_delete:
        # try:
        try:
            delete_namespace(namespace_id, throttle=throttle, dry_run=dry_run)
        except AccountDeletionErrror as e:
            message = e.args[0] if e.args else ""
            log.critical("AccountDeletionErrror", error_message=message)
        except Exception:
            log_uncaught_errors(log, account_id=account_id)

    end = time.time()
    log.info(
        "All data deleted successfully for ids",
        ids_to_delete=ids_to_delete,
        time=end - start,
        count=len(ids_to_delete),
    )


def delete_namespace(namespace_id, throttle=False, dry_run=False):
    """
    Delete all the data associated with a namespace from the database.
    USE WITH CAUTION.

    NOTE: This function is only called from bin/delete-account-data.
    It prints to stdout.

    Raises AccountDeletionErrror with message if there are problems
    """
    with session_scope(namespace_id) as db_session:
        try:
            account = (
                db_session.query(Account)
                .join(Namespace)
                .filter(Namespace.id == namespace_id)
                .one()
            )
        except NoResultFound:
            raise AccountDeletionErrror("Could not find account in database")

        if not account.is_marked_for_deletion:
            raise AccountDeletionErrror(
                "Account is_marked_for_deletion is False. "
                "Change this to proceed with deletion."
            )
        account_id = account.id
        account_discriminator = account.discriminator

    log.info("Deleting account", account_id=account_id)
    start_time = time.time()

    # These folders are used to configure batch deletion in chunks for
    # specific tables that are prone to transaction blocking during
    # large concurrent write volume.  See _batch_delete
    # NOTE: ImapFolderInfo doesn't reall fall into this category but
    # we include here for simplicity anyway.

    filters = OrderedDict()
    for table in [
        "message",
        "block",
        "thread",
        "transaction",
        "actionlog",
        "event",
        "contact",
        "dataprocessingcache",
    ]:
        filters[table] = ("namespace_id", namespace_id)

    if account_discriminator == "easaccount":
        filters["easuid"] = ("easaccount_id", account_id)
        filters["easfoldersyncstatus"] = ("account_id", account_id)
    else:
        filters["imapuid"] = ("account_id", account_id)
        filters["imapfoldersyncstatus"] = ("account_id", account_id)
        filters["imapfolderinfo"] = ("account_id", account_id)

    from inbox.ignition import engine_manager

    # Bypass the ORM for performant bulk deletion;
    # we do /not/ want Transaction records created for these deletions,
    # so this is okay.
    engine = engine_manager.get_for_id(namespace_id)

    for cls in filters:
        _batch_delete(
            engine, cls, filters[cls], account_id, throttle=throttle, dry_run=dry_run
        )

    # Use a single delete for the other tables. Rows from tables which contain
    # cascade-deleted foreign keys to other tables deleted here (or above)
    # are also not always explicitly deleted, except where needed for
    # performance.
    #
    # NOTE: Namespace, Account are deleted at the end too.

    query = "DELETE FROM {} WHERE {}={};"

    filters = OrderedDict()
    for table in ("category", "calendar"):
        filters[table] = ("namespace_id", namespace_id)
    for table in ("folder", "label"):
        filters[table] = ("account_id", account_id)
    filters["namespace"] = ("id", namespace_id)

    for table, (column, id_) in filters.items():
        log.info("Performing bulk deletion", table=table)
        start = time.time()

        if throttle:
            bulk_throttle()

        if not dry_run:
            engine.execute(query.format(table, column, id_))
        else:
            log.debug(query.format(table, column, id_))

        end = time.time()
        log.info("Completed bulk deletion", table=table, time=end - start)

    # Delete the account object manually to get rid of the various objects
    # associated with it (e.g: secrets, tokens, etc.)
    with session_scope(account_id) as db_session:
        account = db_session.query(Account).get(account_id)
        if dry_run is False:
            db_session.delete(account)
            db_session.commit()

    # Delete liveness data ( heartbeats)
    log.debug("Deleting liveness data", account_id=account_id)
    clear_heartbeat_status(account_id)

    statsd_client.timing(
        "mailsync.account_deletion.queue.deleted", time.time() - start_time
    )


def _batch_delete(
    engine, table, column_id_filters, account_id, throttle=False, dry_run=False
):
    (column, id_) = column_id_filters
    count = engine.execute(
        f"SELECT COUNT(*) FROM {table} WHERE {column}={id_};"
    ).scalar()

    if count == 0:
        log.info("Completed batch deletion", table=table)
        return

    batches = int(math.ceil(float(count) / CHUNK_SIZE))

    log.info("Starting batch deletion", table=table, count=count, batches=batches)
    start = time.time()

    if table in ("message", "block"):
        query = ""
    else:
        query = f"DELETE FROM {table} WHERE {column}={id_} LIMIT {CHUNK_SIZE};"

    log.info("deleting", account_id=account_id, table=table)

    for _ in range(batches):
        if throttle:
            bulk_throttle()

        if table == "block":
            with session_scope(account_id) as db_session:
                blocks = list(
                    db_session.query(Block.id, Block.data_sha256)
                    .filter(Block.namespace_id == id_)
                    .limit(CHUNK_SIZE)
                )
            blocks = list(blocks)
            block_ids = [b[0] for b in blocks]
            block_hashes = [b[1] for b in blocks]

            # XXX: We currently don't check for existing blocks.
            if dry_run is False:
                delete_from_blockstore(*block_hashes)

            with session_scope(account_id) as db_session:
                block_query = db_session.query(Block).filter(Block.id.in_(block_ids))
                if dry_run is False:
                    block_query.delete(synchronize_session=False)

        elif table == "message":
            with session_scope(account_id) as db_session:
                # messages must be order by the foreign key `received_date`
                # otherwise MySQL will raise an error when deleting
                # from the message table
                messages = list(
                    db_session.query(Message.id, Message.data_sha256)
                    .filter(Message.namespace_id == id_)
                    .order_by(desc(Message.received_date))
                    .limit(CHUNK_SIZE)
                    .with_hint(
                        Message, "use index (ix_message_namespace_id_received_date)"
                    )
                )

            message_ids = [m[0] for m in messages]
            message_hashes = [m[1] for m in messages]

            with session_scope(account_id) as db_session:
                existing_hashes = list(
                    db_session.query(Message.data_sha256)
                    .filter(Message.data_sha256.in_(message_hashes))
                    .filter(Message.namespace_id != id_)
                    .distinct()
                )
            existing_hashes = [h[0] for h in existing_hashes]

            remove_hashes = set(message_hashes) - set(existing_hashes)
            if dry_run is False:
                delete_from_blockstore(*list(remove_hashes))

            with session_scope(account_id) as db_session:
                message_query = db_session.query(Message).filter(
                    Message.id.in_(message_ids)
                )
                if dry_run is False:
                    message_query.delete(synchronize_session=False)

        else:
            if dry_run is False:
                engine.execute(query)
            else:
                log.debug(query)

    end = time.time()
    log.info("Completed batch deletion", time=end - start, table=table)

    count = engine.execute(
        f"SELECT COUNT(*) FROM {table} WHERE {column}={id_};"
    ).scalar()

    if dry_run is False:
        assert count == 0


def check_throttle():
    """
    Returns True if deletions should be throttled and False otherwise.

    check_throttle is ignored entirely if the separate `throttle` flag is False
    (meaning that throttling is not done at all), but if throttling is enabled,
    this method determines when.
    """
    return True


def purge_transactions(
    shard_id, days_ago=60, limit=1000, throttle=False, dry_run=False, now=None
):
    start = "now()"
    if now is not None:
        start = "'{}'".format(now.strftime("%Y-%m-%d %H:%M:%S"))

    # Delete all items from the transaction table that are older than
    # `days_ago` days.
    if dry_run:
        offset = 0
        query = (
            "SELECT id FROM transaction where created_at < "
            f"DATE_SUB({start}, INTERVAL {days_ago} day) LIMIT {limit}"
        )
    else:
        query = (
            f"DELETE FROM transaction where created_at < DATE_SUB({start},"
            f" INTERVAL {days_ago} day) LIMIT {limit}"
        )
    try:
        # delete from rows until there are no more rows affected
        rowcount = 1
        while rowcount > 0:
            if throttle:
                bulk_throttle()

            with session_scope_by_shard_id(shard_id, versioned=False) as db_session:
                if dry_run:
                    rowcount = db_session.execute(f"{query} OFFSET {offset}").rowcount
                    offset += rowcount
                else:
                    rowcount = db_session.execute(query).rowcount
            log.info(
                "Deleted batch from transaction table",
                batch_size=limit,
                rowcount=rowcount,
            )
        log.info(
            "Finished purging transaction table for shard",
            shard_id=shard_id,
            date_delta=days_ago,
        )
    except Exception as e:
        log.critical("Exception encountered during deletion", exception=e)

    # remove old entries from the redis transaction zset
    if dry_run:
        # no dry run for removing things from a redis zset
        return
    try:
        with session_scope_by_shard_id(shard_id, versioned=False) as db_session:
            (min_txn_id,) = db_session.query(func.min(Transaction.id)).one()
        redis_txn.zremrangebyscore(
            TXN_REDIS_KEY,
            "-inf",
            f"({min_txn_id}" if min_txn_id is not None else "+inf",
        )
        log.info(
            "Finished purging transaction entries from redis",
            min_id=min_txn_id,
            date_delta=days_ago,
        )
    except Exception as e:
        log.critical("Exception encountered during deletion", exception=e)
