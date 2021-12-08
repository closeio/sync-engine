import random
import uuid
from builtins import range
from datetime import datetime, timedelta

import pytest
from sqlalchemy import desc

from inbox.ignition import redis_txn
from inbox.models.transaction import TXN_REDIS_KEY, Transaction
from inbox.models.util import purge_transactions


def get_latest_transaction(db_session, namespace_id):
    return (
        db_session.query(Transaction)
        .filter(Transaction.namespace_id == namespace_id)
        .order_by(desc(Transaction.id))
        .first()
    )


def create_transaction(db, created_at, namespace_id):
    t = Transaction(
        created_at=created_at,
        namespace_id=namespace_id,
        object_type="message",
        command="insert",
        record_id=random.randint(1, 9999),
        object_public_id=uuid.uuid4().hex,
    )
    db.session.add(t)
    db.session.commit()
    return t


def format_datetime(dt):
    return "'{}'".format(dt.strftime("%Y-%m-%d %H:%M:%S"))


class TestTransactionDeletion:
    """
    Test transaction deletion. These tests arbitrarily chose 30 days for
    `days_ago`.
    """

    @pytest.fixture
    def clear_redis(self):
        redis_txn.flushdb()

    @pytest.fixture
    def now(self):
        return datetime.now()

    @pytest.fixture
    def transactions(self, clear_redis, now, db, default_namespace):
        """Creates transactions, some new and some old.

        Yields the newest transaction
        """

        # Transactions created less than 30 days ago should not be deleted
        t0 = create_transaction(db, now, default_namespace.id)
        create_transaction(db, now - timedelta(days=29), default_namespace.id)
        create_transaction(db, now - timedelta(days=30), default_namespace.id)

        # Transactions older than 30 days should be deleted
        for i in range(10):
            create_transaction(db, now - timedelta(days=31 + i), default_namespace.id)

        return t0

    def test_transaction_deletion_dry_run(self, now, db, default_namespace):
        shard_id = default_namespace.id >> 48
        query = "SELECT count(id) FROM transaction WHERE namespace_id={}".format(
            default_namespace.id
        )
        all_transactions = db.session.execute(query).scalar()

        # Ensure no transactions are deleted during a dry run
        purge_transactions(shard_id, days_ago=30, dry_run=True, now=now)
        assert db.session.execute(query).scalar() == all_transactions

    def test_transaction_deletion_30_days(self, now, db, default_namespace):
        shard_id = default_namespace.id >> 48
        query = "SELECT count(id) FROM transaction WHERE namespace_id={}".format(
            default_namespace.id
        )
        all_transactions = db.session.execute(query).scalar()
        date_query = (
            "SELECT count(id) FROM transaction WHERE created_at < "
            "DATE_SUB({}, INTERVAL 30 day)"
        ).format(format_datetime(now))
        older_than_thirty_days = db.session.execute(date_query).scalar()

        # Delete all transactions older than 30 days
        purge_transactions(shard_id, days_ago=30, dry_run=False, now=now)
        assert (
            all_transactions - older_than_thirty_days
            == db.session.execute(query).scalar()
        )

    def test_transaction_deletion_one_day(
        self, now, transactions, db, default_namespace
    ):
        shard_id = default_namespace.id >> 48
        query = "SELECT count(id) FROM transaction WHERE namespace_id={}".format(
            default_namespace.id
        )
        all_transactions = db.session.execute(query).scalar()

        date_query = (
            "SELECT count(id) FROM transaction WHERE created_at < "
            "DATE_SUB({}, INTERVAL 1 day)"
        ).format(format_datetime(now))
        older_than_one_day = db.session.execute(date_query).scalar()
        # Delete all transactions older than 1 day
        purge_transactions(shard_id, days_ago=1, dry_run=False, now=now)
        assert (
            all_transactions - older_than_one_day == db.session.execute(query).scalar()
        )

        latest_transaction = get_latest_transaction(db.session, default_namespace.id)
        assert latest_transaction.id == transactions.id

    def test_transaction_deletion_purges_redis(
        self, now, transactions, db, default_namespace
    ):
        def _get_redis_transactions():
            return redis_txn.zrangebyscore(
                TXN_REDIS_KEY, "-inf", "+inf", withscores=True, score_cast_func=int,
            )

        assert db.session.query(Transaction).count()
        assert len(_get_redis_transactions())

        shard_id = default_namespace.id >> 48
        purge_transactions(
            shard_id, days_ago=1, dry_run=False, now=now + timedelta(days=30)
        )

        assert not db.session.query(Transaction).count()
        assert not len(_get_redis_transactions())
