import collections
import time
from datetime import datetime

import gevent
from sqlalchemy import asc, bindparam, desc
from sqlalchemy.orm.exc import NoResultFound

from inbox.api.kellogs import APIEncoder, encode
from inbox.models import Account, Message, Namespace, Thread, Transaction
from inbox.models.session import session_scope
from inbox.models.util import transaction_objects
from inbox.sqlalchemy_ext.util import bakery

EVENT_NAME_FOR_COMMAND = {"insert": "create", "update": "modify", "delete": "delete"}


def get_transaction_cursor_near_timestamp(namespace_id, timestamp, db_session):
    """
    Exchange a timestamp for a 'cursor' into the transaction log entry near
    to that timestamp in age. The cursor is the public_id of that transaction
    (or '0' if there are no such transactions).

    Arguments
    ---------
    namespace_id: int
        Id of the namespace for which to get a cursor.
    timestamp: int
        Unix timestamp
    db_session: new_session
        database session

    Returns
    -------
    string
        A transaction public_id that can be passed as a 'cursor' parameter by
        API clients.

    """
    dt = datetime.utcfromtimestamp(timestamp)

    # We want this guarantee: if you pass a timestamp for, say,
    # '2015-03-20 12:22:20', and you have multiple transactions immediately
    # prior, e.g.:
    # id | created_at
    # ---+-----------
    # 23 | 2015-03-20 12:22:19
    # 24 | 2015-03-20 12:22:19
    # 25 | 2015-03-20 12:22:19
    # then you get the last one by id (25). Otherwise you might pass a
    # timestamp far in the future, but not actually get the last cursor.
    # The obvious way to accomplish this is to filter by `created_at` but order
    # by `id`. However, that causes MySQL to perform a potentially expensive
    # filesort. Instead, get transactions with timestamp *matching* the last
    # one before what you have, and sort those by id:
    latest_timestamp = (
        db_session.query(Transaction.created_at)
        .order_by(desc(Transaction.created_at))
        .filter(Transaction.created_at < dt, Transaction.namespace_id == namespace_id)
        .limit(1)
        .subquery()
    )
    latest_transaction = (
        db_session.query(Transaction)
        .filter(
            Transaction.created_at == latest_timestamp,
            Transaction.namespace_id == namespace_id,
        )
        .order_by(desc(Transaction.id))
        .first()
    )

    if latest_transaction is None:
        # If there are no earlier deltas, use '0' as a special stamp parameter
        # to signal 'process from the start of the log'.
        return "0"

    return latest_transaction.public_id


def _get_last_trx_id_for_namespace(namespace_id, db_session):
    q = bakery(lambda session: session.query(Transaction.id))
    q += lambda q: q.filter(Transaction.namespace_id == bindparam("namespace_id"))
    q += (
        lambda q: q.order_by(desc(Transaction.created_at))
        .order_by(desc(Transaction.id))
        .limit(1)
    )
    return q(db_session).params(namespace_id=namespace_id).one()[0]


def format_transactions_after_pointer(
    namespace,
    pointer,
    db_session,
    result_limit,
    exclude_types=None,
    include_types=None,
    exclude_folders=True,
    exclude_metadata=True,
    exclude_account=True,
    expand=False,
    is_n1=False,
):
    """
    Return a pair (deltas, new_pointer), where deltas is a list of change
    events, represented as dictionaries:
    {
      "object": <API object type, e.g. "thread">,
      "event": <"create", "modify", or "delete>,
      "attributes": <API representation of the object for insert/update events>
      "cursor": <public_id of the transaction>
    }

    and new_pointer is the integer id of the last included transaction

    Arguments
    ---------
    namespace_id: int
        Id of the namespace for which to get changes.
    pointer: int
        Process transactions starting after this id.
    db_session: new_session
        database session
    result_limit: int
        Maximum number of results to return. (Because we may roll up multiple
        changes to the same object, fewer results can be returned.)
    format_transaction_fn: function pointer
        Function that defines how to format the transactions.
    exclude_types: list, optional
        If given, don't include transactions for these types of objects.

    """
    exclude_types = set(exclude_types) if exclude_types else set()
    # Begin backwards-compatibility shim -- suppress new object types for now,
    # because clients may not be able to deal with them.
    if exclude_folders is True:
        exclude_types.update(("folder", "label"))
    if exclude_account is True:
        exclude_types.add("account")
    # End backwards-compatibility shim.

    # Metadata is excluded by default, and can only be included by setting the
    # exclude_metadata flag to False. If listed in include_types, remove it.
    if exclude_metadata is True:
        exclude_types.add("metadata")
    if include_types is not None and "metadata" in include_types:
        include_types.remove("metadata")

    try:
        last_trx = _get_last_trx_id_for_namespace(namespace.id, db_session)
    except NoResultFound:
        return ([], pointer)

    if last_trx == pointer:
        return ([], pointer)

    while True:
        transactions = db_session.query(Transaction).filter(
            Transaction.id > pointer, Transaction.namespace_id == namespace.id
        )

        if exclude_types is not None:
            transactions = transactions.filter(
                ~Transaction.object_type.in_(exclude_types)
            )

        if include_types is not None:
            transactions = transactions.filter(
                Transaction.object_type.in_(include_types)
            )

        transactions = (
            transactions.order_by(asc(Transaction.id)).limit(result_limit).all()
        )

        if not transactions:
            return ([], pointer)

        results = []

        # Group deltas by object type.
        trxs_by_obj_type = collections.defaultdict(list)
        for trx in transactions:
            trxs_by_obj_type[trx.object_type].append(trx)

        for obj_type, trxs in list(trxs_by_obj_type.items()):
            # Build a dictionary mapping pairs (record_id, command) to
            # transaction. If successive modifies for a given record id appear
            # in the list of transactions, this will only keep the latest
            # one (which is what we want).
            sorted_trxs = sorted(trxs, key=lambda t: t.id)
            latest_trxs = {(trx.record_id, trx.command): trx for trx in sorted_trxs}
            oldest_trxs = {
                (trx.record_id, trx.command): trx for trx in reversed(sorted_trxs)
            }
            # Load all referenced not-deleted objects.
            ids_to_query = [
                trx.record_id
                for trx in list(latest_trxs.values())
                if trx.command != "delete"
            ]

            object_cls = transaction_objects()[obj_type]

            if object_cls == Account:
                # The base query for Account queries the /Namespace/ table
                # since the API-returned "`account`" is a `namespace`
                # under-the-hood.
                query = (
                    db_session.query(Namespace)
                    .join(Account)
                    .filter(Account.id.in_(ids_to_query), Namespace.id == namespace.id)
                )

                # Key by /namespace.account_id/ --
                # namespace.id may not be equal to account.id
                # and trx.record_id == account.id for `account` trxs.
                objects = {obj.account_id: obj for obj in query}
            else:
                query = db_session.query(object_cls).filter(
                    object_cls.id.in_(ids_to_query),
                    object_cls.namespace_id == namespace.id,
                )

                if object_cls == Thread:
                    query = query.options(*Thread.api_loading_options(expand))
                if object_cls == Message:
                    query = query.options(*Message.api_loading_options(expand))
                    # T7045: Workaround for some SQLAlchemy bugs.
                    objects = {obj.id: obj for obj in query if obj.thread is not None}
                else:
                    objects = {obj.id: obj for obj in query}

            for key, trx in list(latest_trxs.items()):
                oldest_trx = oldest_trxs[key]
                delta = {
                    "object": trx.object_type,
                    "event": EVENT_NAME_FOR_COMMAND[trx.command],
                    "id": trx.object_public_id,
                    "cursor": trx.public_id,
                    "start_timestamp": oldest_trx.created_at,
                    "end_timestamp": trx.created_at,
                }
                if trx.command != "delete":
                    obj = objects.get(trx.record_id)
                    if obj is None:
                        continue
                    repr_ = encode(
                        obj,
                        namespace_public_id=namespace.public_id,
                        expand=expand,
                        is_n1=is_n1,
                    )
                    delta["attributes"] = repr_

                results.append((trx.id, delta))

        if results:
            # Sort deltas by id of the underlying transactions.
            results.sort()
            deltas = [d for _, d in results]
            return (deltas, results[-1][0])
        else:
            # It's possible that none of the referenced objects exist any more,
            # meaning the result list is empty. In that case, keep traversing
            # the log until we get actual results or reach the end.
            pointer = transactions[-1].id


def streaming_change_generator(
    namespace,
    poll_interval,
    timeout,
    transaction_pointer,
    exclude_types=None,
    include_types=None,
    exclude_folders=True,
    exclude_metadata=True,
    exclude_account=True,
    expand=False,
    is_n1=False,
):
    """
    Poll the transaction log for the given `namespace_id` until `timeout`
    expires, and yield each time new entries are detected.
    Arguments
    ---------
    namespace_id: int
        Id of the namespace for which to check changes.
    poll_interval: float
        How often to check for changes.
    timeout: float
        How many seconds to allow the connection to remain open.
    transaction_pointer: int, optional
        Yield transaction rows starting after the transaction with id equal to
        `transaction_pointer`.

    """
    encoder = APIEncoder(is_n1=is_n1)
    start_time = time.time()
    while time.time() - start_time < timeout:
        with session_scope(namespace.id) as db_session:
            deltas, new_pointer = format_transactions_after_pointer(
                namespace,
                transaction_pointer,
                db_session,
                100,
                exclude_types,
                include_types,
                exclude_folders,
                exclude_metadata,
                exclude_account,
                expand=expand,
                is_n1=is_n1,
            )

        if new_pointer is not None and new_pointer != transaction_pointer:
            transaction_pointer = new_pointer
            for delta in deltas:
                yield encoder.cereal(delta) + "\n"
        else:
            yield "\n"
            gevent.sleep(poll_interval)
