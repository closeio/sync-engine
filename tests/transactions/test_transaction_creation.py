from future import standard_library

standard_library.install_aliases()
from datetime import datetime

from flanker import mime
from sqlalchemy import desc

from inbox.models import AccountTransaction, Calendar, Transaction
from inbox.models.mixins import HasRevisions
from inbox.models.util import transaction_objects

from tests.util.base import (
    add_fake_category,
    add_fake_event,
    add_fake_message,
    add_fake_thread,
)


def get_latest_transaction(db_session, object_type, record_id, namespace_id):
    return (
        db_session.query(Transaction)
        .filter(
            Transaction.namespace_id == namespace_id,
            Transaction.object_type == object_type,
            Transaction.record_id == record_id,
        )
        .order_by(desc(Transaction.id))
        .first()
    )


def get_latest_transaction_any(db_session, namespace_id):
    return (
        db_session.query(Transaction)
        .filter(Transaction.namespace_id == namespace_id)
        .order_by(desc(Transaction.id))
        .first()
    )


def test_thread_insert_creates_transaction(db, default_namespace):
    thr = add_fake_thread(db.session, default_namespace.id)
    transaction = get_latest_transaction(
        db.session, "thread", thr.id, default_namespace.id
    )
    assert transaction.command == "insert"


def test_message_insert_creates_transaction(db, default_namespace):
    with db.session.no_autoflush:
        thr = add_fake_thread(db.session, default_namespace.id)
        msg = add_fake_message(db.session, default_namespace.id, thr)
        transaction = get_latest_transaction(
            db.session, "message", msg.id, default_namespace.id
        )
        assert transaction.command == "insert"

        # Test that the thread gets revised too
        transaction = get_latest_transaction(
            db.session, "thread", thr.id, default_namespace.id
        )
        assert transaction.command == "update"


def test_message_updates_create_transaction(db, default_namespace):
    with db.session.no_autoflush:
        thr = add_fake_thread(db.session, default_namespace.id)
        msg = add_fake_message(db.session, default_namespace.id, thr)

        msg.is_read = True
        db.session.commit()
        transaction = get_latest_transaction(
            db.session, "message", msg.id, default_namespace.id
        )
        assert transaction.record_id == msg.id
        assert transaction.object_type == "message"
        assert transaction.command == "update"


def test_message_updates_create_thread_transaction(db, default_namespace):
    with db.session.no_autoflush:
        thr = add_fake_thread(db.session, default_namespace.id)
        msg = add_fake_message(db.session, default_namespace.id, thr)

        transaction = get_latest_transaction(
            db.session, "thread", thr.id, default_namespace.id
        )
        assert transaction.record_id == thr.id and transaction.object_type == "thread"
        assert transaction.command == "update"

        # An update to one of the message's propagated_attributes creates a
        # revision for the thread
        msg.is_read = True
        db.session.commit()

        new_transaction = get_latest_transaction(
            db.session, "thread", thr.id, default_namespace.id
        )
        assert new_transaction.id != transaction.id
        assert (
            new_transaction.record_id == thr.id
            and new_transaction.object_type == "thread"
        )
        assert new_transaction.command == "update"

        # An update to one of its other attributes does not
        msg.subject = "Ice cubes and dogs"
        db.session.commit()

        same_transaction = get_latest_transaction(
            db.session, "thread", thr.id, default_namespace.id
        )
        assert same_transaction.id == new_transaction.id


def test_message_category_updates_create_transaction(db, default_namespace):
    with db.session.no_autoflush:
        thr = add_fake_thread(db.session, default_namespace.id)
        msg = add_fake_message(db.session, default_namespace.id, thr)
        cat = add_fake_category(db.session, default_namespace.id, "category")
        thread_trx_before_category_change = get_latest_transaction(
            db.session, "thread", thr.id, default_namespace.id
        )

        msg.categories = [cat]
        db.session.commit()
        latest_message_trx = get_latest_transaction(
            db.session, "message", msg.id, default_namespace.id
        )
        thread_trx_after_category_change = get_latest_transaction(
            db.session, "thread", thr.id, default_namespace.id
        )

        assert latest_message_trx.command == "update"
        assert (
            thread_trx_before_category_change.id != thread_trx_after_category_change.id
        )


def test_object_type_distinguishes_messages_and_drafts(db, default_namespace):
    with db.session.no_autoflush:
        thr = add_fake_thread(db.session, default_namespace.id)
        msg = add_fake_message(db.session, default_namespace.id, thr)
        msg.is_draft = 1
        db.session.commit()
        transaction = get_latest_transaction(
            db.session, "draft", msg.id, default_namespace.id
        )
        assert transaction.command == "update"
        db.session.delete(msg)
        db.session.commit()
        transaction = get_latest_transaction(
            db.session, "draft", msg.id, default_namespace.id
        )
        assert transaction.command == "delete"


def test_event_insert_creates_transaction(db, default_namespace):
    with db.session.no_autoflush:
        event = add_fake_event(db.session, default_namespace.id)
        transaction = get_latest_transaction(
            db.session, "event", event.id, default_namespace.id
        )
        assert transaction.record_id == event.id
        assert transaction.object_type == "event"
        assert transaction.command == "insert"


def test_transactions_created_for_calendars(db, default_namespace):
    calendar = Calendar(
        namespace_id=default_namespace.id, name="New Calendar", uid="uid"
    )
    db.session.add(calendar)
    db.session.commit()
    transaction = get_latest_transaction(
        db.session, "calendar", calendar.id, default_namespace.id
    )
    assert transaction.record_id == calendar.id
    assert transaction.object_type == "calendar"
    assert transaction.command == "insert"

    calendar.name = "Updated Calendar"
    db.session.commit()
    transaction = get_latest_transaction(
        db.session, "calendar", calendar.id, default_namespace.id
    )
    assert transaction.record_id == calendar.id
    assert transaction.object_type == "calendar"
    assert transaction.command == "update"

    db.session.delete(calendar)
    db.session.commit()
    transaction = get_latest_transaction(
        db.session, "calendar", calendar.id, default_namespace.id
    )
    assert transaction.record_id == calendar.id
    assert transaction.object_type == "calendar"
    assert transaction.command == "delete"


def test_file_transactions(db, default_namespace):
    from inbox.models.message import Message

    account = default_namespace.account
    thread = add_fake_thread(db.session, default_namespace.id)
    mime_msg = mime.create.multipart("mixed")
    mime_msg.append(
        mime.create.text("plain", "This is a message with attachments"),
        mime.create.attachment(
            "image/png", "filler", "attached_image.png", "attachment"
        ),
        mime.create.attachment(
            "application/pdf", "filler", "attached_file.pdf", "attachment"
        ),
    )
    msg = Message.create_from_synced(
        account,
        22,
        "[Gmail]/All Mail",
        datetime.utcnow(),
        mime_msg.to_string().encode(),
    )
    msg.thread = thread
    db.session.add(msg)
    db.session.commit()

    assert len(msg.parts) == 2
    assert all(part.content_disposition == "attachment" for part in msg.parts)

    block_ids = [part.block.id for part in msg.parts]

    with db.session.no_autoflush:
        transaction = get_latest_transaction(
            db.session, "file", block_ids[0], default_namespace.id
        )
        assert transaction.command == "insert"

        transaction = get_latest_transaction(
            db.session, "file", block_ids[1], default_namespace.id
        )
        assert transaction.command == "insert"


def test_account_transactions(db, default_namespace):
    account = default_namespace.account

    transaction = get_latest_transaction(
        db.session, "account", account.id, default_namespace.id
    )
    assert transaction.command == "insert"
    transaction_id = transaction.id

    with db.session.no_autoflush:
        account.last_synced_events = datetime.utcnow()
        db.session.commit()
        transaction = get_latest_transaction(
            db.session, "account", account.id, default_namespace.id
        )
        assert transaction.id == transaction_id

        account.sync_state = "invalid"
        db.session.commit()
        transaction = get_latest_transaction(
            db.session, "account", account.id, default_namespace.id
        )
        assert transaction.id != transaction_id
        assert transaction.command == "update"

        account.sync_host = "anewhost"
        db.session.commit()
        same_transaction = get_latest_transaction(
            db.session, "account", account.id, default_namespace.id
        )
        assert same_transaction.id == transaction.id


def test_object_deletions_create_transaction(db, default_namespace):
    with db.session.no_autoflush:
        thr = add_fake_thread(db.session, default_namespace.id)
        msg = add_fake_message(db.session, default_namespace.id, thr)
        db.session.delete(msg)
        db.session.commit()
        transaction = get_latest_transaction(
            db.session, "message", msg.id, default_namespace.id
        )
        assert transaction.record_id == msg.id
        assert transaction.object_type == "message"
        assert transaction.command == "delete"

        db.session.delete(thr)
        db.session.commit()
        transaction = get_latest_transaction(
            db.session, "thread", thr.id, default_namespace.id
        )
        assert transaction.record_id == thr.id
        assert transaction.object_type == "thread"
        assert transaction.command == "delete"


def test_transaction_creation_for_self_referential_message_relationship(
    db, default_namespace
):
    # Make sure that updating the self-refential relationship
    # `Message.reply_to_message` does not create a spurious update delta for
    # the parent message.
    thr = add_fake_thread(db.session, default_namespace.id)
    msg = add_fake_message(db.session, default_namespace.id, thr)
    reply = add_fake_message(db.session, default_namespace.id, thr)
    reply.reply_to_message = msg
    db.session.commit()
    assert reply.reply_to_message_id is not None
    assert msg.reply_to_message_id is None
    transaction = get_latest_transaction(
        db.session, "message", msg.id, default_namespace.id
    )
    assert transaction.record_id == msg.id
    assert transaction.object_type == "message"
    assert transaction.command == "insert"


def test_transaction_objects_mapped_for_all_models(db, default_namespace):
    """
    Test that all subclasses of HasRevisions are mapped by the
    transaction_objects() function.

    """
    assert set(HasRevisions.__subclasses__()).issubset(transaction_objects().values())


def test_accounttransactions(db, default_namespace):
    account = default_namespace.account

    transaction = get_latest_transaction(
        db.session, "account", default_namespace.account.id, default_namespace.id
    )
    assert transaction.command == "insert"
    transaction_id = transaction.id

    # Verify an AccountTransaction is created
    accounttransactions = (
        db.session.query(AccountTransaction)
        .filter(AccountTransaction.namespace_id == default_namespace.id)
        .all()
    )
    assert len(accounttransactions) == 1
    accounttransaction = accounttransactions[0]
    assert accounttransaction.namespace_id == default_namespace.id
    assert accounttransaction.command == "insert"
    assert accounttransaction.object_type == "account"
    assert accounttransaction.record_id == default_namespace.account.id
    accounttransaction_id = accounttransaction.id

    with db.session.no_autoflush:
        # No Transaction or AccountTransaction records created

        account.last_synced_events = datetime.utcnow()
        db.session.commit()
        transaction = get_latest_transaction(
            db.session, "account", default_namespace.account.id, default_namespace.id
        )
        assert transaction.id == transaction_id
        accounttransactions = (
            db.session.query(AccountTransaction)
            .filter(AccountTransaction.namespace_id == default_namespace.id)
            .all()
        )
        assert len(accounttransactions) == 1
        assert accounttransactions[0].id == accounttransaction_id

        # Only Transaction record created

        thread = add_fake_thread(db.session, default_namespace.id)
        transaction = get_latest_transaction(
            db.session, "thread", thread.id, default_namespace.id
        )
        assert transaction.id > transaction_id
        accounttransactions = (
            db.session.query(AccountTransaction)
            .filter(AccountTransaction.namespace_id == default_namespace.id)
            .all()
        )
        assert len(accounttransactions) == 1
        assert accounttransactions[0].id == accounttransaction_id

        # Both Transaction or AccountTransaction records created

        account.sync_state = "invalid"
        db.session.commit()
        transaction = get_latest_transaction(
            db.session, "account", default_namespace.account.id, default_namespace.id
        )
        assert transaction.id > transaction_id
        assert transaction.command == "update"
        accounttransactions = (
            db.session.query(AccountTransaction)
            .filter(AccountTransaction.namespace_id == default_namespace.id)
            .all()
        )
        assert len(accounttransactions) == 2
        assert accounttransactions[1].id != accounttransaction_id
        assert accounttransactions[1].command == "update"
