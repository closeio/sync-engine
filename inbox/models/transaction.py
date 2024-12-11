from sqlalchemy import BigInteger, Column, Enum, Index, String, func, inspect
from sqlalchemy.orm import relationship

from inbox.ignition import redis_txn
from inbox.models.base import MailSyncBase
from inbox.models.category import EPOCH
from inbox.models.mixins import HasPublicID, HasRevisions
from inbox.models.namespace import Namespace

TXN_REDIS_KEY = "latest-txn-by-namespace"


class Transaction(MailSyncBase, HasPublicID):
    """Transactional log to enable client syncing."""

    # Do delete transactions if their associated namespace is deleted.
    namespace_id = Column(BigInteger, nullable=False)
    namespace = relationship(
        Namespace,
        primaryjoin="foreign(Transaction.namespace_id) == remote(Namespace.id)",
    )

    object_type = Column(String(20), nullable=False)
    record_id = Column(BigInteger, nullable=False, index=True)
    object_public_id = Column(String(191), nullable=False, index=True)
    command = Column(Enum("insert", "update", "delete"), nullable=False)


Index("object_type_record_id", Transaction.object_type, Transaction.record_id)
Index(
    "namespace_id_created_at", Transaction.namespace_id, Transaction.created_at
)
Index(
    "ix_transaction_namespace_id_object_type_id",
    Transaction.namespace_id,
    Transaction.object_type,
    Transaction.id,
)


class AccountTransaction(MailSyncBase, HasPublicID):
    namespace_id = Column(BigInteger, index=True, nullable=False)
    namespace = relationship(
        Namespace,
        primaryjoin="foreign(AccountTransaction.namespace_id) == remote(Namespace.id)",
    )

    object_type = Column(String(20), nullable=False)
    record_id = Column(BigInteger, nullable=False, index=True)
    object_public_id = Column(String(191), nullable=False, index=True)
    command = Column(Enum("insert", "update", "delete"), nullable=False)


Index("ix_accounttransaction_table_name", Transaction.object_type)
Index("ix_accounttransaction_command", Transaction.command)
Index(
    "ix_accounttransaction_object_type_record_id",
    AccountTransaction.object_type,
    AccountTransaction.record_id,
)
Index(
    "ix_accounttransaction_namespace_id_created_at",
    AccountTransaction.namespace_id,
    AccountTransaction.created_at,
)


def is_dirty(session, obj) -> bool:
    if obj in session.dirty and obj.has_versioned_changes():
        return True
    if hasattr(obj, "dirty") and obj.dirty:
        return True
    return False


def create_revisions(session) -> None:
    for obj in session:
        if (
            not isinstance(obj, HasRevisions)
            or obj.should_suppress_transaction_creation
        ):
            continue
        if obj in session.new:
            create_revision(obj, session, "insert")
        elif is_dirty(session, obj):
            # Need to unmark the object as 'dirty' to prevent an infinite loop
            # (the pre-flush hook may be called again before a commit
            # occurs). This emulates what happens to objects in session.dirty,
            # in that they are no longer present in the set during the next
            # invocation of the pre-flush hook.
            obj.dirty = False
            create_revision(obj, session, "update")
        elif obj in session.deleted:
            create_revision(obj, session, "delete")


def create_revision(obj, session, revision_type) -> None:
    assert revision_type in ("insert", "update", "delete")

    # If available use object dates for the transaction timestamp
    # otherwise use DB time. This is needed because CURRENT_TIMESTAMP
    # changes during a transaction which can lead to inconsistencies
    # between object timestamps and the transaction timestamps.
    if revision_type == "delete":
        created_at = getattr(obj, "deleted_at", None)
        # Sometimes categories are deleted explicitly which leaves
        # their deleted_at default value, EPOCH, when the
        # transaction is created.
        if created_at == EPOCH:
            created_at = func.now()
    else:
        created_at = getattr(obj, "updated_at", None)

    if created_at is None:
        created_at = func.now()

    # Always create a Transaction record -- this maintains a total ordering over
    # all events for an account.
    revision = Transaction(
        command=revision_type,
        record_id=obj.id,
        object_type=obj.API_OBJECT_NAME,
        object_public_id=obj.public_id,
        namespace_id=obj.namespace.id,
        created_at=created_at,
    )
    session.add(revision)

    # Additionally, record account-level events in the AccountTransaction --
    # this is an optimization needed so these sparse events can be still be
    # retrieved efficiently for webhooks etc.
    if obj.API_OBJECT_NAME == "account":
        revision = AccountTransaction(
            command=revision_type,
            record_id=obj.id,
            object_type=obj.API_OBJECT_NAME,
            object_public_id=obj.public_id,
            namespace_id=obj.namespace.id,
        )
        session.add(revision)


def propagate_changes(session) -> None:
    """
    Mark an object's related object as dirty when certain attributes of the
    object (its `propagated_attributes`) change.

    For example, when a message's `is_read`, `is_starred` or `categories`
    changes, the message.thread is marked as dirty.
    """
    from inbox.models.message import Message

    for obj in session.dirty:
        if isinstance(obj, Message):
            obj_state = inspect(obj)
            for attr in obj.propagated_attributes:
                if (
                    getattr(obj_state.attrs, attr).history.has_changes()
                    and obj.thread
                ):
                    obj.thread.dirty = True


def increment_versions(session) -> None:
    from inbox.models.metadata import Metadata
    from inbox.models.thread import Thread

    for obj in session:
        if isinstance(obj, Thread) and is_dirty(session, obj):
            # This issues SQL for an atomic increment.
            obj.version = Thread.version + 1
        if isinstance(obj, Metadata) and is_dirty(session, obj):
            # This issues SQL for an atomic increment.
            obj.version = Metadata.version + 1  # TODO what's going on here?


def bump_redis_txn_id(session) -> None:
    """
    Called from post-flush hook to bump the latest id stored in redis
    """

    def get_namespace_public_id(namespace_id):
        # the namespace was just used to create the transaction, so it should
        # still be in the session. If not, a sql statement will be emitted.
        namespace = session.query(Namespace).get(namespace_id)
        assert namespace, "namespace for transaction doesn't exist"
        return str(namespace.public_id)

    mappings = {
        get_namespace_public_id(obj.namespace_id): obj.id
        for obj in session
        if (obj in session.new and isinstance(obj, Transaction) and obj.id)
    }
    if mappings:
        redis_txn.zadd(TXN_REDIS_KEY, mapping=mappings)
