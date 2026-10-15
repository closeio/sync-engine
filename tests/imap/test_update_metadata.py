import datetime
import json
from unittest import mock

import pytest

from inbox.api.update import update_thread
from inbox.crispin import Flags, GmailFlags
from inbox.mailsync.backends.imap import common
from inbox.mailsync.backends.imap.common import (
    PENDING_LABEL_CHANGE_MAX_AGE,
    update_message_metadata,
    update_metadata,
)
from inbox.models.action_log import ActionLog, schedule_action
from inbox.models.backends.imap import ImapUid
from inbox.models.folder import Folder
from inbox.models.message import MessageCategory
from inbox.models.session import session_scope
from tests.util.base import (
    add_fake_folder,
    add_fake_imapuid,
    add_fake_message,
    add_fake_thread,
    delete_imapuids,
    delete_messages,
    delete_threads,
)


def test_gmail_label_sync(
    db, default_account, message, folder, imapuid, default_namespace
) -> None:
    # Note that IMAPClient parses numeric labels into integer types. We have to
    # correctly handle those too.
    new_flags = {
        imapuid.msg_uid: GmailFlags(
            (), ("\\Important", "\\Starred", "foo", 42), None
        )
    }
    update_metadata(
        default_namespace.account.id,
        folder.id,
        folder.canonical_name,
        new_flags,
        db.session,
    )
    category_canonical_names = {c.name for c in message.categories}
    category_display_names = {c.display_name for c in message.categories}
    assert "important" in category_canonical_names
    assert {"foo", "42"}.issubset(category_display_names)


def test_gmail_drafts_flag_constrained_by_folder(
    db, default_account, message, imapuid, folder
) -> None:
    new_flags = {imapuid.msg_uid: GmailFlags((), ("\\Draft",), None)}
    update_metadata(
        default_account.id, folder.id, "all", new_flags, db.session
    )
    assert message.is_draft
    update_metadata(
        default_account.id, folder.id, "trash", new_flags, db.session
    )
    assert not message.is_draft


@pytest.mark.parametrize("folder_role", ["drafts", "trash", "archive"])
def test_generic_drafts_flag_constrained_by_folder(
    db, generic_account, folder_role
) -> None:
    msg_uid = 22
    thread = add_fake_thread(db.session, generic_account.namespace.id)
    message = add_fake_message(
        db.session, generic_account.namespace.id, thread
    )
    folder = add_fake_folder(db.session, generic_account)
    add_fake_imapuid(db.session, generic_account.id, message, folder, msg_uid)

    new_flags = {msg_uid: Flags((b"\\Draft",), None)}
    update_metadata(
        generic_account.id, folder.id, folder_role, new_flags, db.session
    )
    assert message.is_draft == (folder_role == "drafts")


def test_categories_follow_imap_labels_without_label_change(
    db, default_account, message, imapuid
) -> None:
    message.categories_changes = True
    db.session.commit()
    update_message_metadata(db.session, imapuid.account, message, False)
    assert message.categories == {imapuid.folder.category}


OLD_MESSAGE_COUNT = 3
NEW_MESSAGE_UID = OLD_MESSAGE_COUNT + 1


@pytest.fixture
def archived_new_message(db, default_account, thread, folder):
    """
    Return the new message of a Gmail thread that the API archived.

    The thread has `OLD_MESSAGE_COUNT` read messages without `inbox`, and one
    new unread message with `inbox`. The API removed `inbox` from the thread,
    so each message has a pending `change_labels` action.
    """
    namespace_id = default_account.namespace.id
    for msg_uid in range(1, OLD_MESSAGE_COUNT + 1):
        message = add_fake_message(db.session, namespace_id, thread)
        imapuid = add_fake_imapuid(
            db.session, default_account.id, message, folder, msg_uid
        )
        imapuid.update_flags([b"\\Seen"])
        update_message_metadata(db.session, default_account, message, False)

    new_message = add_fake_message(db.session, namespace_id, thread)
    new_imapuid = add_fake_imapuid(
        db.session, default_account.id, new_message, folder, NEW_MESSAGE_UID
    )
    new_imapuid.update_labels(["\\Inbox"])
    update_message_metadata(db.session, default_account, new_message, False)
    db.session.commit()
    assert "inbox" in {category.name for category in new_message.categories}
    assert not new_message.is_read

    update_thread(
        thread,
        {"label_ids": [folder.category.public_id]},
        db.session,
        optimistic=False,
    )
    db.session.commit()
    pending_label_changes = db.session.query(ActionLog).filter(
        ActionLog.namespace_id == namespace_id,
        ActionLog.action == "change_labels",
        ActionLog.status == "pending",
    )
    assert pending_label_changes.count() == len(thread.messages)

    yield new_message

    db.session.rollback()
    db.session.query(ActionLog).delete(synchronize_session=False)
    db.session.commit()
    delete_imapuids(db.session)


def save_poll_of_new_message(db, account, folder) -> None:
    """
    Save a Gmail poll that reports the new message as read and in the inbox.
    """
    new_flags = {NEW_MESSAGE_UID: GmailFlags((b"\\Seen",), ["\\Inbox"], None)}
    update_metadata(
        account.id, folder.id, folder.canonical_name, new_flags, db.session
    )


def test_categories_kept_during_pending_label_change(
    db, default_account, folder, archived_new_message
) -> None:
    message_category_ids = {
        message_category.id
        for message_category in archived_new_message.messagecategories
    }

    save_poll_of_new_message(db, default_account, folder)

    assert "inbox" not in {
        category.name for category in archived_new_message.categories
    }
    assert {
        message_category.id
        for message_category in archived_new_message.messagecategories
    } == message_category_ids
    assert archived_new_message.is_read
    assert "inbox" in {
        category.name
        for category in archived_new_message.imapuids[0].categories
    }


def test_kept_categories_are_logged(
    db, default_account, folder, archived_new_message, monkeypatch
) -> None:
    mock_log = mock.Mock()
    monkeypatch.setattr(common, "log", mock_log)

    save_poll_of_new_message(db, default_account, folder)

    mock_log.info.assert_any_call(
        "Kept message categories during a pending label change",
        account_id=default_account.id,
        message_id=archived_new_message.id,
        imap_only_category_names=["inbox"],
        local_only_category_names=[],
    )


def test_categories_read_before_pending_label_change_query(
    db, default_account, message, imapuid, monkeypatch
) -> None:
    imapuid.update_labels(["\\Inbox"])
    update_message_metadata(db.session, default_account, message, False)
    db.session.commit()
    namespace_id = message.namespace_id
    inbox_message_category_id = next(
        message_category.id
        for message_category in message.messagecategories
        if message_category.category.name == "inbox"
    )
    # Make the call below read the categories from the database.
    db.session.expire(message)

    def remove_inbox_after_query(_session, _message) -> bool:
        # Commit the removal of `inbox` from another session, after a query
        # that found no pending action.
        with session_scope(namespace_id) as other_session:
            other_session.query(MessageCategory).filter(
                MessageCategory.id == inbox_message_category_id
            ).delete(synchronize_session=False)
            other_session.commit()
        return False

    monkeypatch.setattr(
        common, "_has_pending_label_change", remove_inbox_after_query
    )
    update_message_metadata(db.session, default_account, message, False)
    db.session.commit()

    assert "inbox" not in {category.name for category in message.categories}


def test_categories_follow_imap_labels_with_old_pending_label_change(
    db, default_account, folder, archived_new_message
) -> None:
    old_created_at = (
        datetime.datetime.utcnow()
        - PENDING_LABEL_CHANGE_MAX_AGE
        - datetime.timedelta(minutes=1)
    )
    db.session.query(ActionLog).filter(
        ActionLog.record_id == archived_new_message.id,
        ActionLog.action == "change_labels",
    ).update({"created_at": old_created_at}, synchronize_session=False)
    db.session.commit()

    save_poll_of_new_message(db, default_account, folder)

    assert "inbox" in {
        category.name for category in archived_new_message.categories
    }


def test_categories_follow_imap_labels_after_label_change(
    db, default_account, folder, archived_new_message
) -> None:
    db.session.query(ActionLog).filter(
        ActionLog.namespace_id == default_account.namespace.id,
        ActionLog.action == "change_labels",
    ).update({"status": "successful"}, synchronize_session=False)
    db.session.commit()

    save_poll_of_new_message(db, default_account, folder)

    assert "inbox" in {
        category.name for category in archived_new_message.categories
    }


def test_categories_follow_imap_labels_without_own_pending_label_change(
    db, default_account, folder, archived_new_message
) -> None:
    # The actions of the other messages of the thread stay pending.
    db.session.query(ActionLog).filter(
        ActionLog.record_id == archived_new_message.id,
        ActionLog.action == "change_labels",
    ).update({"status": "failed"}, synchronize_session=False)
    db.session.commit()

    save_poll_of_new_message(db, default_account, folder)

    assert "inbox" in {
        category.name for category in archived_new_message.categories
    }


def test_categories_follow_imap_labels_with_pending_mark_unread(
    db, default_account, folder, archived_new_message
) -> None:
    db.session.query(ActionLog).filter(
        ActionLog.namespace_id == default_account.namespace.id,
        ActionLog.action == "change_labels",
    ).update({"status": "successful"}, synchronize_session=False)
    schedule_action(
        "mark_unread",
        archived_new_message,
        archived_new_message.namespace_id,
        db.session,
        unread=False,
    )
    db.session.commit()

    save_poll_of_new_message(db, default_account, folder)

    assert "inbox" in {
        category.name for category in archived_new_message.categories
    }


@pytest.fixture
def generic_inbox_message(db, generic_account):
    """
    Return a message of a generic IMAP account in the inbox folder.
    """
    namespace_id = generic_account.namespace.id
    thread = add_fake_thread(db.session, namespace_id)
    message = add_fake_message(db.session, namespace_id, thread)
    inbox_folder = Folder.find_or_create(
        db.session, generic_account, "Inbox", "inbox"
    )
    add_fake_imapuid(
        db.session, generic_account.id, message, inbox_folder, 2222
    )
    update_message_metadata(db.session, generic_account, message, False)
    db.session.commit()

    yield message

    db.session.rollback()
    db.session.query(ActionLog).delete(synchronize_session=False)
    db.session.commit()
    delete_imapuids(db.session)
    delete_threads(db.session)


@pytest.mark.parametrize("action", ["move", "change_labels"])
def test_folder_account_categories_follow_imap_during_pending_action(
    db, generic_account, generic_inbox_message, action
) -> None:
    schedule_action(
        action,
        generic_inbox_message,
        generic_inbox_message.namespace_id,
        db.session,
    )
    generic_inbox_message.imapuids[0].folder = Folder.find_or_create(
        db.session, generic_account, "Archive", "archive"
    )
    db.session.commit()

    update_message_metadata(
        db.session, generic_account, generic_inbox_message, False
    )

    assert {
        category.name for category in generic_inbox_message.categories
    } == {"archive"}


@pytest.mark.parametrize(
    ("folder_roles", "categories"),
    [
        ([], set()),
        (["inbox"], {"inbox"}),
        (["inbox", "archive"], {"archive"}),
        (["inbox", "trash"], {"trash"}),
        (["inbox", "archive", "trash"], {"trash"}),
    ],
)
def test_categories_from_multiple_imap_folders(
    db, generic_account, folder_roles, categories
) -> None:
    """
    This tests that if we somehow think that a message is inside
    many folders simultanously, we should categorize it with the one
    it was added to last.

    This should not happen in practice as with generic IMAP a message will always be
    in a single folder but it seems that for some on-prem servers we are not
    able to reliably detect when a message is moved between folders and we end
    up with many folders in our MySQL. Such message used to undeterministically
    appear in one of those folders depending on the order they were returned
    from the database. This makes it deterministic and more-correct because a message
    is likely in a folder it was added to last.
    """  # noqa: D404
    thread = add_fake_thread(db.session, generic_account.namespace.id)
    message = add_fake_message(
        db.session, generic_account.namespace.id, thread
    )
    for delay, folder_role in enumerate(folder_roles):
        folder = Folder.find_or_create(
            db.session, generic_account, folder_role, folder_role
        )
        imapuid = add_fake_imapuid(
            db.session, generic_account.id, message, folder, 2222
        )
        # Simulate that time passed since those timestamps have second resolution
        # and this executes fast enough that all of them would be the same otherwise
        imapuid.updated_at = imapuid.updated_at + datetime.timedelta(
            seconds=delay
        )
        db.session.commit()

    update_message_metadata(db.session, generic_account, message, False)
    assert {category.name for category in message.categories} == categories

    delete_imapuids(db.session)
    delete_messages(db.session)
    delete_threads(db.session)


def test_truncate_imapuid_extra_flags(
    db, default_account, message, folder
) -> None:
    imapuid = ImapUid(
        message=message,
        account_id=default_account.id,
        msg_uid=2222,
        folder=folder,
    )
    imapuid.update_flags([
        b"We",
        b"the",
        b"People",
        b"of",
        b"the",
        b"United",
        b"States",
        b"in",
        b"Order",
        b"to",
        b"form",
        b"a",
        b"more",
        b"perfect",
        b"Union",
        b"establish",
        b"Justice",
        b"insure",
        b"domestic",
        b"Tranquility",
        b"provide",
        b"for",
        b"the",
        b"common",
        b"defence",
        b"promote",
        b"the",
        b"general",
        b"Welfare",
        b"and",
        b"secure",
        b"the",
        b"Blessings",
        b"of",
        b"Liberty",
        b"to",
        b"ourselves",
        b"and",
        b"our",
        b"Posterity",
        b"do",
        b"ordain",
        b"and",
        b"establish",
        b"this",
        b"Constitution",
        b"for",
        b"the",
        b"United",
        b"States",
        b"of",
        b"America",
    ])

    assert len(json.dumps(imapuid.extra_flags)) < 255
