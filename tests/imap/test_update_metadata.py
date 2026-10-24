import datetime
import json
from unittest import mock

import pytest

from inbox.api.update import update_thread
from inbox.crispin import Flags, GmailFlags
from inbox.mailsync.backends.imap import common
from inbox.mailsync.backends.imap.common import (
    LABEL_CHANGE_MODSEQ_MAX_AGE,
    PENDING_LABEL_CHANGE_MAX_AGE,
    update_message_metadata,
    update_metadata,
)
from inbox.models.action_log import (
    CHANGE_LABELS_MODSEQ_KEY,
    ActionLog,
    FolderModseq,
    schedule_action,
)
from inbox.models.backends.imap import ImapFolderInfo, ImapUid
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


def save_poll_of_new_message(db, account, folder, modseq=None) -> None:
    """
    Save a Gmail poll that reports the new message as read and in the inbox,
    with `modseq`.
    """
    new_flags = {
        NEW_MESSAGE_UID: GmailFlags((b"\\Seen",), ["\\Inbox"], modseq)
    }
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
        "Kept message categories during a label change",
        account_id=default_account.id,
        message_id=archived_new_message.id,
        reason="pending_label_change",
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
    # Give the IMAP labels a category that the message does not have. Thus,
    # the call below runs the query for a pending action.
    imapuid.update_labels(["\\Inbox", "\\Important"])
    db.session.commit()
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

    category_names = {category.name for category in message.categories}
    assert "inbox" not in category_names
    assert "important" in category_names


def test_label_change_queries_skipped_with_imap_labels_as_categories(
    db, default_account, message, imapuid, monkeypatch
) -> None:
    update_message_metadata(db.session, default_account, message, False)
    db.session.commit()
    has_pending_label_change = mock.Mock(return_value=False)
    is_older_than_label_change = mock.Mock(return_value=False)
    monkeypatch.setattr(
        common, "_has_pending_label_change", has_pending_label_change
    )
    monkeypatch.setattr(
        common, "_is_older_than_label_change", is_older_than_label_change
    )

    update_message_metadata(
        db.session,
        default_account,
        message,
        False,
        fetched_folder_id=imapuid.folder_id,
        fetched_modseq=1,
    )

    has_pending_label_change.assert_not_called()
    is_older_than_label_change.assert_not_called()
    assert message.categories == {imapuid.folder.category}


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


SAVED_MODSEQ = 100
FOLDER_UIDVALIDITY = 2


@pytest.fixture
def folder_info(db, default_account, folder):
    """Save `FOLDER_UIDVALIDITY` as the UIDVALIDITY of the folder."""
    folder_info = ImapFolderInfo(
        account_id=default_account.id,
        folder_id=folder.id,
        uidvalidity=FOLDER_UIDVALIDITY,
    )
    db.session.add(folder_info)
    db.session.commit()

    yield folder_info

    db.session.rollback()
    db.session.query(ImapFolderInfo).filter(
        ImapFolderInfo.folder_id == folder.id
    ).delete(synchronize_session=False)
    db.session.commit()


def saved_folder_modseqs(folder_id, uidvalidity=FOLDER_UIDVALIDITY):
    """
    Return the values that a `change_labels` action saves for `SAVED_MODSEQ`
    in the folder with the ID `folder_id`, under `uidvalidity`.
    """
    return {
        str(folder_id): FolderModseq(
            uidvalidity=uidvalidity, modseq=SAVED_MODSEQ
        )
    }


def finish_label_changes(db, account, message, folder_modseqs) -> None:
    """
    Mark the `change_labels` actions as successful. The action of `message`
    saves `folder_modseqs`.
    """
    for action_log_entry in db.session.query(ActionLog).filter(
        ActionLog.namespace_id == account.namespace.id,
        ActionLog.action == "change_labels",
    ):
        action_log_entry.status = "successful"
        if action_log_entry.record_id == message.id:
            action_log_entry.extra_args = {
                **action_log_entry.extra_args,
                CHANGE_LABELS_MODSEQ_KEY: folder_modseqs,
            }
    db.session.commit()


def test_categories_kept_with_imap_labels_older_than_label_change(
    db, default_account, folder, folder_info, archived_new_message
) -> None:
    finish_label_changes(
        db,
        default_account,
        archived_new_message,
        saved_folder_modseqs(folder.id),
    )

    save_poll_of_new_message(db, default_account, folder, SAVED_MODSEQ - 10)

    assert "inbox" not in {
        category.name for category in archived_new_message.categories
    }
    assert archived_new_message.is_read
    assert "inbox" in {
        category.name
        for category in archived_new_message.imapuids[0].categories
    }


def test_stale_imap_labels_are_logged(
    db, default_account, folder, folder_info, archived_new_message, monkeypatch
) -> None:
    finish_label_changes(
        db,
        default_account,
        archived_new_message,
        saved_folder_modseqs(folder.id),
    )
    mock_log = mock.Mock()
    monkeypatch.setattr(common, "log", mock_log)

    save_poll_of_new_message(db, default_account, folder, SAVED_MODSEQ - 10)

    mock_log.info.assert_any_call(
        "Kept message categories during a label change",
        account_id=default_account.id,
        message_id=archived_new_message.id,
        reason="stale_imap_data",
        imap_only_category_names=["inbox"],
        local_only_category_names=[],
    )


def test_categories_follow_imap_labels_as_new_as_label_change(
    db, default_account, folder, folder_info, archived_new_message
) -> None:
    finish_label_changes(
        db,
        default_account,
        archived_new_message,
        saved_folder_modseqs(folder.id),
    )

    save_poll_of_new_message(db, default_account, folder, SAVED_MODSEQ)

    assert "inbox" in {
        category.name for category in archived_new_message.categories
    }


def test_categories_follow_imap_labels_with_modseq_of_other_folder(
    db, default_account, folder, folder_info, archived_new_message
) -> None:
    finish_label_changes(
        db,
        default_account,
        archived_new_message,
        saved_folder_modseqs(folder.id + 1),
    )

    save_poll_of_new_message(db, default_account, folder, SAVED_MODSEQ - 10)

    assert "inbox" in {
        category.name for category in archived_new_message.categories
    }


def test_categories_follow_imap_labels_with_modseq_of_other_message(
    db, default_account, folder, folder_info, archived_new_message
) -> None:
    other_message = next(
        message
        for message in archived_new_message.thread.messages
        if message.id != archived_new_message.id
    )
    finish_label_changes(
        db,
        default_account,
        other_message,
        saved_folder_modseqs(folder.id),
    )

    save_poll_of_new_message(db, default_account, folder, SAVED_MODSEQ - 10)

    assert "inbox" in {
        category.name for category in archived_new_message.categories
    }


def test_categories_follow_imap_labels_with_modseq_of_old_uidvalidity(
    db, default_account, folder, folder_info, archived_new_message
) -> None:
    # The action saved its MODSEQ before a UIDVALIDITY change of the folder.
    finish_label_changes(
        db,
        default_account,
        archived_new_message,
        saved_folder_modseqs(folder.id, uidvalidity=FOLDER_UIDVALIDITY - 1),
    )

    save_poll_of_new_message(db, default_account, folder, SAVED_MODSEQ - 10)

    assert "inbox" in {
        category.name for category in archived_new_message.categories
    }


def test_categories_follow_imap_labels_without_folder_uidvalidity(
    db, default_account, folder, archived_new_message
) -> None:
    # The folder has no `ImapFolderInfo` row, so its current UIDVALIDITY is
    # unknown.
    finish_label_changes(
        db,
        default_account,
        archived_new_message,
        saved_folder_modseqs(folder.id),
    )

    save_poll_of_new_message(db, default_account, folder, SAVED_MODSEQ - 10)

    assert "inbox" in {
        category.name for category in archived_new_message.categories
    }


def test_categories_follow_imap_labels_after_old_label_change(
    db, default_account, folder, folder_info, archived_new_message
) -> None:
    finish_label_changes(
        db,
        default_account,
        archived_new_message,
        saved_folder_modseqs(folder.id),
    )
    old_updated_at = (
        datetime.datetime.utcnow()
        - LABEL_CHANGE_MODSEQ_MAX_AGE
        - datetime.timedelta(minutes=1)
    )
    db.session.query(ActionLog).filter(
        ActionLog.record_id == archived_new_message.id,
        ActionLog.action == "change_labels",
    ).update({"updated_at": old_updated_at}, synchronize_session=False)
    db.session.commit()

    save_poll_of_new_message(db, default_account, folder, SAVED_MODSEQ - 10)

    assert "inbox" in {
        category.name for category in archived_new_message.categories
    }


def test_categories_follow_imap_labels_after_label_change_without_modseqs(
    db, default_account, folder, folder_info, archived_new_message
) -> None:
    # The successful actions saved no MODSEQ values.
    db.session.query(ActionLog).filter(
        ActionLog.namespace_id == default_account.namespace.id,
        ActionLog.action == "change_labels",
    ).update({"status": "successful"}, synchronize_session=False)
    db.session.commit()

    save_poll_of_new_message(db, default_account, folder, SAVED_MODSEQ - 10)

    assert "inbox" in {
        category.name for category in archived_new_message.categories
    }


def test_categories_follow_imap_labels_without_fetched_modseq(
    db, default_account, folder, folder_info, archived_new_message
) -> None:
    finish_label_changes(
        db,
        default_account,
        archived_new_message,
        saved_folder_modseqs(folder.id),
    )

    save_poll_of_new_message(db, default_account, folder)

    assert "inbox" in {
        category.name for category in archived_new_message.categories
    }


def test_categories_kept_after_newer_label_change_without_modseqs(
    db, default_account, folder, folder_info, archived_new_message
) -> None:
    finish_label_changes(
        db,
        default_account,
        archived_new_message,
        saved_folder_modseqs(folder.id),
    )
    # A newer successful action for the message saved no MODSEQ values. The
    # IMAP data is still older than the earlier action.
    schedule_action(
        "change_labels",
        archived_new_message,
        archived_new_message.namespace_id,
        db.session,
        removed_labels=["\\Inbox"],
        added_labels=[],
    )
    db.session.commit()
    newer_action_log_entry = (
        db.session.query(ActionLog)
        .filter(
            ActionLog.record_id == archived_new_message.id,
            ActionLog.action == "change_labels",
        )
        .order_by(ActionLog.id.desc())
        .first()
    )
    newer_action_log_entry.status = "successful"
    newer_action_log_entry.extra_args = {
        **newer_action_log_entry.extra_args,
        CHANGE_LABELS_MODSEQ_KEY: {},
    }
    db.session.commit()

    save_poll_of_new_message(db, default_account, folder, SAVED_MODSEQ - 10)

    assert "inbox" not in {
        category.name for category in archived_new_message.categories
    }


def make_label_change_finish_after_pending_query(
    monkeypatch, message, folder_modseqs
) -> None:
    """
    Patch the query for a pending action, so that the `change_labels` action
    of `message` becomes successful right after the query, and saves
    `folder_modseqs`.
    """
    namespace_id = message.namespace_id
    message_id = message.id

    def finish_label_change_after_query(_session, _message) -> bool:
        # Commit the end of the action from another session, after a query
        # that found no pending action.
        with session_scope(namespace_id) as other_session:
            action_log_entry = (
                other_session.query(ActionLog)
                .filter(
                    ActionLog.record_id == message_id,
                    ActionLog.action == "change_labels",
                )
                .one()
            )
            action_log_entry.status = "successful"
            action_log_entry.extra_args = {
                **action_log_entry.extra_args,
                CHANGE_LABELS_MODSEQ_KEY: folder_modseqs,
            }
            other_session.commit()
        return False

    monkeypatch.setattr(
        common, "_has_pending_label_change", finish_label_change_after_query
    )


def test_successful_label_change_query_after_pending_query(
    db, default_account, folder, folder_info, archived_new_message, monkeypatch
) -> None:
    make_label_change_finish_after_pending_query(
        monkeypatch, archived_new_message, saved_folder_modseqs(folder.id)
    )

    save_poll_of_new_message(db, default_account, folder, SAVED_MODSEQ - 10)

    assert "inbox" not in {
        category.name for category in archived_new_message.categories
    }


def save_label_change_rows_of_new_message(db, new_message) -> None:
    """
    Save the IMAP rows that the `change_labels` action of the new message
    fetched after it removed the inbox label. The message is read at that
    time.
    """
    new_imapuid = new_message.imapuids[0]
    new_imapuid.update_flags([b"\\Seen"])
    new_imapuid.update_labels([])
    db.session.commit()


def save_unread_poll_of_new_message(db, account, folder, modseq=None) -> None:
    """
    Save a Gmail poll that reports the new message as unread and in the
    inbox, with `modseq`.
    """
    new_flags = {NEW_MESSAGE_UID: GmailFlags((), ["\\Inbox"], modseq)}
    update_metadata(
        account.id, folder.id, folder.canonical_name, new_flags, db.session
    )


def imap_category_names(message) -> set[str]:
    """Return the names of the categories from the IMAP rows of `message`."""
    return {
        category.name
        for imapuid in message.imapuids
        for category in imapuid.categories
    }


def test_imap_rows_kept_with_imap_labels_older_than_label_change(
    db, default_account, folder, folder_info, archived_new_message
) -> None:
    save_label_change_rows_of_new_message(db, archived_new_message)
    finish_label_changes(
        db,
        default_account,
        archived_new_message,
        saved_folder_modseqs(folder.id),
    )

    save_poll_of_new_message(db, default_account, folder, SAVED_MODSEQ - 10)

    assert "inbox" not in {
        category.name for category in archived_new_message.categories
    }
    assert "inbox" not in imap_category_names(archived_new_message)
    assert archived_new_message.is_read


def test_imap_flags_kept_with_imap_labels_older_than_label_change(
    db, default_account, folder, folder_info, archived_new_message
) -> None:
    save_label_change_rows_of_new_message(db, archived_new_message)
    finish_label_changes(
        db,
        default_account,
        archived_new_message,
        saved_folder_modseqs(folder.id),
    )

    # The poll fetched the new message before the read change.
    save_unread_poll_of_new_message(
        db, default_account, folder, SAVED_MODSEQ - 10
    )

    assert archived_new_message.imapuids[0].is_seen
    assert archived_new_message.is_read


def test_imap_rows_follow_imap_data_during_pending_label_change(
    db, default_account, folder, archived_new_message
) -> None:
    # Gmail reports the new message as read, and with a label that its IMAP
    # rows do not have. For example, the user added the label in Gmail.
    new_flags = {
        NEW_MESSAGE_UID: GmailFlags(
            (b"\\Seen",), ["\\Inbox", "Foo"], SAVED_MODSEQ
        )
    }

    update_metadata(
        default_account.id,
        folder.id,
        folder.canonical_name,
        new_flags,
        db.session,
    )

    new_imapuid = archived_new_message.imapuids[0]
    assert new_imapuid.is_seen
    assert {label.name for label in new_imapuid.labels} == {"inbox", "Foo"}
    assert archived_new_message.is_read
    # The pending action still keeps the categories.
    assert {
        category.name or category.display_name
        for category in archived_new_message.categories
    } == {"all"}


def test_imap_rows_kept_with_older_imap_data_during_pending_label_change(
    db, default_account, folder, folder_info, archived_new_message
) -> None:
    save_label_change_rows_of_new_message(db, archived_new_message)
    finish_label_changes(
        db,
        default_account,
        archived_new_message,
        saved_folder_modseqs(folder.id),
    )
    # A newer action for the message is pending. Use other labels, so that
    # the action is not a duplicate of the finished one.
    schedule_action(
        "change_labels",
        archived_new_message,
        archived_new_message.namespace_id,
        db.session,
        removed_labels=["\\Important"],
        added_labels=[],
    )
    db.session.commit()

    save_poll_of_new_message(db, default_account, folder, SAVED_MODSEQ - 10)

    assert "inbox" not in imap_category_names(archived_new_message)
    assert "inbox" not in {
        category.name for category in archived_new_message.categories
    }


def test_imap_rows_follow_imap_labels_as_new_as_label_change(
    db, default_account, folder, folder_info, archived_new_message, monkeypatch
) -> None:
    save_label_change_rows_of_new_message(db, archived_new_message)
    finish_label_changes(
        db,
        default_account,
        archived_new_message,
        saved_folder_modseqs(folder.id),
    )
    has_pending_label_change = mock.Mock(
        wraps=common._has_pending_label_change
    )
    is_older_than_label_change = mock.Mock(
        wraps=common._is_older_than_label_change
    )
    monkeypatch.setattr(
        common, "_has_pending_label_change", has_pending_label_change
    )
    monkeypatch.setattr(
        common, "_is_older_than_label_change", is_older_than_label_change
    )

    save_poll_of_new_message(db, default_account, folder, SAVED_MODSEQ)

    assert "inbox" in imap_category_names(archived_new_message)
    assert "inbox" in {
        category.name for category in archived_new_message.categories
    }
    # The message update reuses the check of `update_metadata`.
    assert has_pending_label_change.call_count == 1
    assert is_older_than_label_change.call_count == 1


def test_imap_rows_kept_with_successful_label_change_after_pending_query(
    db, default_account, folder, folder_info, archived_new_message, monkeypatch
) -> None:
    save_label_change_rows_of_new_message(db, archived_new_message)
    make_label_change_finish_after_pending_query(
        monkeypatch, archived_new_message, saved_folder_modseqs(folder.id)
    )

    save_poll_of_new_message(db, default_account, folder, SAVED_MODSEQ - 10)

    assert "inbox" not in {
        category.name for category in archived_new_message.categories
    }
    assert "inbox" not in imap_category_names(archived_new_message)


def test_label_change_check_left_to_message_update_with_saved_labels(
    db, default_account, folder, archived_new_message, monkeypatch
) -> None:
    # The IMAP rows of the new message already have the labels of the poll.
    message_update = mock.Mock(wraps=update_message_metadata)
    monkeypatch.setattr(common, "update_message_metadata", message_update)

    save_poll_of_new_message(db, default_account, folder)

    assert message_update.call_args.kwargs["label_change_check"] is None
    # The message update still keeps the categories.
    assert "inbox" not in {
        category.name for category in archived_new_message.categories
    }


@pytest.mark.parametrize(
    ("has_newer_pending_label_change", "reason"),
    [(False, "stale_imap_data"), (True, "pending_label_change")],
)
def test_kept_imap_labels_are_logged(
    db,
    default_account,
    folder,
    folder_info,
    archived_new_message,
    monkeypatch,
    has_newer_pending_label_change,
    reason,
) -> None:
    save_label_change_rows_of_new_message(db, archived_new_message)
    finish_label_changes(
        db,
        default_account,
        archived_new_message,
        saved_folder_modseqs(folder.id),
    )
    if has_newer_pending_label_change:
        # Use other labels, so that the action is not a duplicate of the
        # finished one.
        schedule_action(
            "change_labels",
            archived_new_message,
            archived_new_message.namespace_id,
            db.session,
            removed_labels=["\\Important"],
            added_labels=[],
        )
        db.session.commit()
    mock_log = mock.Mock()
    monkeypatch.setattr(common, "log", mock_log)

    save_poll_of_new_message(db, default_account, folder, SAVED_MODSEQ - 10)

    mock_log.info.assert_any_call(
        "Kept IMAP rows newer than the fetched data",
        account_id=default_account.id,
        message_id=archived_new_message.id,
        folder_id=folder.id,
        reason=reason,
        fetched_only_label_names=["inbox"],
        saved_only_label_names=[],
    )
    # The kept IMAP rows give the current categories. Thus, only the line
    # above shows the stale `\Inbox`.
    logged_messages = [call.args[0] for call in mock_log.info.call_args_list]
    assert (
        "Kept message categories during a label change" not in logged_messages
    )


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
