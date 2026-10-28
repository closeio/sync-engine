import imaplib
import time
from datetime import datetime, timedelta
from unittest import mock

import pytest
from flanker import mime
from imapclient.exceptions import LoginError

from inbox import interruptible_threading
from inbox.actions.backends.gmail import (
    LabelChangeResult,
    save_fetched_imap_rows,
)
from inbox.actions.base import (
    change_labels,
    create_folder,
    create_label,
    delete_draft,
    delete_folder,
    delete_label,
    mark_starred,
    mark_unread,
    move,
    save_draft,
    update_draft,
    update_folder,
    update_label,
)
from inbox.api.update import update_thread
from inbox.crispin import (
    CrispinConnectionPool,
    GmailFlags,
    writable_connection_pool,
)
from inbox.interruptible_threading import InterruptibleThreadTimeout
from inbox.mailsync.backends.imap.common import (
    update_message_metadata,
    update_metadata,
)
from inbox.models import ActionLog, Category, Label, Transaction
from inbox.models.action_log import (
    CHANGE_LABELS_MODSEQ_KEY,
    FolderModseq,
    schedule_action,
)
from inbox.models.backends.imap import ImapUid, ImapUidState
from inbox.models.session import session_scope
from inbox.sendmail.base import create_message_from_json
from inbox.sendmail.base import update_draft as sendmail_update_draft
from inbox.transactions.actions import (
    ACTION_MAX_NR_OF_RETRIES,
    SyncbackService,
    SyncbackWorker,
)
from tests.util.base import (
    add_fake_category,
    add_fake_folder,
    add_fake_imapuid,
    add_fake_message,
    delete_imapuids,
)


def test_draft_updates(db, default_account, mock_imapclient) -> None:
    # Set up folder list
    mock_imapclient._data["Drafts"] = {}
    mock_imapclient._data["Trash"] = {}
    mock_imapclient._data["Sent Mail"] = {}
    mock_imapclient.list_folders = lambda: [
        ((b"\\HasNoChildren", b"\\Drafts"), b"/", "Drafts"),
        ((b"\\HasNoChildren", b"\\Trash"), b"/", "Trash"),
        ((b"\\HasNoChildren", b"\\Sent"), b"/", "Sent Mail"),
    ]

    pool = writable_connection_pool(default_account.id)

    draft = create_message_from_json(
        {"subject": "Test draft"}, default_account.namespace, db.session, True
    )
    draft.is_draft = True
    draft.version = 0
    db.session.commit()
    with pool.get() as conn:
        save_draft(conn, default_account.id, draft.id, {"version": 0})
        conn.select_folder("Drafts", lambda *args: True)
        assert len(list(conn.all_uids())) == 1

        # Check that draft is not resaved if already synced.
        update_draft(conn, default_account.id, draft.id, {"version": 0})
        conn.select_folder("Drafts", lambda *args: True)
        assert len(list(conn.all_uids())) == 1

        # Check that an older version is deleted
        draft.version = 4
        sendmail_update_draft(
            db.session,
            default_account,
            draft,
            from_addr=draft.from_addr,
            subject="New subject",
            blocks=[],
        )
        db.session.commit()

        update_draft(conn, default_account.id, draft.id, {"version": 5})

        conn.select_folder("Drafts", lambda *args: True)
        all_uids = list(conn.all_uids())
        assert len(all_uids) == 1
        data = conn.uids(all_uids)[0]
        parsed = mime.from_string(data.body)
        expected_message_id = (
            f"<{draft.public_id}-{draft.version}@mailer.nylas.com>"
        )
        assert parsed.headers.get("Message-Id") == expected_message_id

        # We're testing the draft deletion with Gmail here. However,
        # because of a race condition in Gmail's reconciliation algorithm,
        # we need to check if the sent mail has been created in the sent
        # folder. Since we're mocking everything, we have to create it
        # ourselves.
        mock_imapclient.append(
            "Sent Mail", data.body, None, None, x_gm_msgid=4323
        )

        delete_draft(
            conn,
            default_account.id,
            draft.id,
            {
                "message_id_header": draft.message_id_header,
                "nylas_uid": draft.nylas_uid,
                "version": 5,
            },
        )

        conn.select_folder("Drafts", lambda *args: True)
        all_uids = list(conn.all_uids())
        assert len(all_uids) == 0


def test_change_flags(
    db, default_account, message, folder, mock_imapclient
) -> None:
    mock_imapclient.add_folder_data(folder.name, {})
    mock_imapclient.add_flags = mock.Mock()
    mock_imapclient.remove_flags = mock.Mock()
    add_fake_imapuid(db.session, default_account.id, message, folder, 22)
    with writable_connection_pool(default_account.id).get() as crispin_client:
        mark_unread(
            crispin_client, default_account.id, message.id, {"unread": False}
        )
        mock_imapclient.add_flags.assert_called_with(
            [22], ["\\Seen"], silent=True
        )

        mark_unread(
            crispin_client, default_account.id, message.id, {"unread": True}
        )
        mock_imapclient.remove_flags.assert_called_with(
            [22], ["\\Seen"], silent=True
        )

        mark_starred(
            crispin_client, default_account.id, message.id, {"starred": True}
        )
        mock_imapclient.add_flags.assert_called_with(
            [22], ["\\Flagged"], silent=True
        )

        mark_starred(
            crispin_client, default_account.id, message.id, {"starred": False}
        )
        mock_imapclient.remove_flags.assert_called_with(
            [22], ["\\Flagged"], silent=True
        )


def count_message_transactions(db, message) -> int:
    """Return the number of transactions of `message`."""
    return (
        db.session.query(Transaction)
        .filter(
            Transaction.namespace_id == message.namespace_id,
            Transaction.object_type == "message",
            Transaction.record_id == message.id,
        )
        .count()
    )


def label_names(imapuid) -> set[str]:
    """Return the names of the labels in the IMAP rows of `imapuid`."""
    return {label.name for label in imapuid.labels}


def saved_states(*imapuids) -> dict[tuple[int, int], ImapUidState]:
    """
    Return the state in the IMAP rows of each UID in `imapuids`, by folder ID
    and UID.
    """
    return {
        (imapuid.folder_id, imapuid.msg_uid): imapuid.saved_state()
        for imapuid in imapuids
    }


def test_change_labels(
    db, default_account, message, folder, mock_imapclient
) -> None:
    mock_imapclient.add_folder_data(
        folder.name,
        {
            22: {
                b"FLAGS": (b"\\Seen",),
                b"X-GM-LABELS": (
                    b"mot&APY-rhead",
                    b"&A7wDtQPEA6wDvQO,A7kDsQ-",
                ),
                b"MODSEQ": (123,),
            }
        },
    )
    mock_imapclient.uidvalidity = 7
    mock_imapclient.add_gmail_labels = mock.Mock()
    mock_imapclient.remove_gmail_labels = mock.Mock()
    # The mock returns `MODSEQ` also if the fetch does not ask for it. Thus,
    # check the fetched items below.
    mock_imapclient.fetch = mock.Mock(wraps=mock_imapclient.fetch)
    imapuid = add_fake_imapuid(
        db.session, default_account.id, message, folder, 22
    )
    imapuid.update_labels(["\\Inbox"])
    db.session.commit()

    with writable_connection_pool(default_account.id).get() as crispin_client:
        result = change_labels(
            crispin_client,
            default_account.id,
            [message.id],
            {
                "removed_labels": ["\\Inbox"],
                "added_labels": ["motörhead", "μετάνοια"],
            },
        )
        mock_imapclient.add_gmail_labels.assert_called_with(
            [22], [b"mot&APY-rhead", b"&A7wDtQPEA6wDvQO,A7kDsQ-"], silent=True
        )
        mock_imapclient.remove_gmail_labels.assert_called_with(
            [22], [b"\\Inbox"], silent=True
        )
        mock_imapclient.fetch.assert_called_once_with(
            [22], ["FLAGS", "X-GM-LABELS", "MODSEQ"]
        )
    assert result.modseqs == {
        message.id: {str(folder.id): FolderModseq(uidvalidity=7, modseq=123)}
    }
    assert result.uids_by_message_id == {message.id: {(folder.id, 22)}}
    assert result.states_before_change == {
        (folder.id, 22): ImapUidState.from_flags_and_labels((), ["\\Inbox"])
    }
    assert result.fetched_flags == {
        (folder.id, 22): GmailFlags(
            (b"\\Seen",), ["motörhead", "μετάνοια"], 123
        )
    }
    # End the transaction, and read the IMAP rows. The action does not save
    # them.
    db.session.commit()
    assert not imapuid.is_seen
    assert label_names(imapuid) == {"inbox"}


def test_change_labels_reads_imap_rows_before_change(
    db, default_account, message, folder, mock_imapclient
) -> None:
    mock_imapclient.add_folder_data(
        folder.name,
        {22: {b"FLAGS": (), b"X-GM-LABELS": (), b"MODSEQ": (123,)}},
    )
    imapuid = add_fake_imapuid(
        db.session, default_account.id, message, folder, 22
    )
    imapuid.update_labels(["\\Inbox"])
    db.session.commit()
    account_id = default_account.id
    imapuid_id = imapuid.id

    def save_imap_rows(*args, **kwargs):
        # Mail sync saves other IMAP rows during the change.
        with session_scope(account_id) as other_session:
            other_imapuid = other_session.query(ImapUid).get(imapuid_id)
            other_imapuid.update_flags([b"\\Seen"])
            other_imapuid.update_labels([])
            other_session.commit()

    mock_imapclient.remove_gmail_labels = mock.Mock(side_effect=save_imap_rows)

    with writable_connection_pool(default_account.id).get() as crispin_client:
        result = change_labels(
            crispin_client,
            default_account.id,
            [message.id],
            {"removed_labels": ["\\Inbox"], "added_labels": []},
        )

    mock_imapclient.remove_gmail_labels.assert_called_once()
    assert result.states_before_change == {
        (folder.id, 22): ImapUidState.from_flags_and_labels((), ["\\Inbox"])
    }


def test_change_labels_returns_modseqs_by_message_and_folder(
    db, default_account, message, folder, mock_imapclient
) -> None:
    other_message = add_fake_message(
        db.session, default_account.namespace.id, message.thread
    )
    other_folder = add_fake_folder(
        db.session, default_account, "[Gmail]/Trash", "trash"
    )
    mock_imapclient.add_folder_data(
        folder.name,
        {
            22: {b"FLAGS": (), b"X-GM-LABELS": (), b"MODSEQ": (123,)},
            23: {b"FLAGS": (), b"X-GM-LABELS": (), b"MODSEQ": (124,)},
        },
    )
    mock_imapclient.add_folder_data(
        other_folder.name,
        {30: {b"FLAGS": (), b"X-GM-LABELS": (), b"MODSEQ": (125,)}},
    )
    mock_imapclient.remove_gmail_labels = mock.Mock()
    add_fake_imapuid(db.session, default_account.id, message, folder, 22)
    add_fake_imapuid(db.session, default_account.id, other_message, folder, 23)
    add_fake_imapuid(db.session, default_account.id, message, other_folder, 30)

    with writable_connection_pool(default_account.id).get() as crispin_client:
        result = change_labels(
            crispin_client,
            default_account.id,
            [message.id, other_message.id],
            {"removed_labels": ["\\Inbox"], "added_labels": []},
        )

    uidvalidity = mock_imapclient.uidvalidity
    assert result.modseqs == {
        message.id: {
            str(folder.id): FolderModseq(uidvalidity=uidvalidity, modseq=123),
            str(other_folder.id): FolderModseq(
                uidvalidity=uidvalidity, modseq=125
            ),
        },
        other_message.id: {
            str(folder.id): FolderModseq(uidvalidity=uidvalidity, modseq=124)
        },
    }


def test_change_labels_skips_uids_without_modseq(
    db, default_account, message, folder, mock_imapclient
) -> None:
    mock_imapclient.add_folder_data(
        folder.name, {22: {b"FLAGS": (), b"X-GM-LABELS": ()}}
    )
    mock_imapclient.remove_gmail_labels = mock.Mock()
    add_fake_imapuid(db.session, default_account.id, message, folder, 22)

    with writable_connection_pool(default_account.id).get() as crispin_client:
        result = change_labels(
            crispin_client,
            default_account.id,
            [message.id],
            {"removed_labels": ["\\Inbox"], "added_labels": []},
        )

    assert result.modseqs == {}


def test_change_labels_returns_lowest_modseq_of_message_in_folder(
    db, default_account, message, folder, mock_imapclient
) -> None:
    # The UID with the higher MODSEQ comes last in the fetch response.
    mock_imapclient.add_folder_data(
        folder.name,
        {
            22: {b"FLAGS": (), b"X-GM-LABELS": (), b"MODSEQ": (123,)},
            23: {b"FLAGS": (), b"X-GM-LABELS": (), b"MODSEQ": (124,)},
        },
    )
    mock_imapclient.remove_gmail_labels = mock.Mock()
    add_fake_imapuid(db.session, default_account.id, message, folder, 22)
    add_fake_imapuid(db.session, default_account.id, message, folder, 23)

    with writable_connection_pool(default_account.id).get() as crispin_client:
        result = change_labels(
            crispin_client,
            default_account.id,
            [message.id],
            {"removed_labels": ["\\Inbox"], "added_labels": []},
        )

    assert result.modseqs == {
        message.id: {
            str(folder.id): FolderModseq(
                uidvalidity=mock_imapclient.uidvalidity, modseq=123
            )
        }
    }


def test_change_labels_skips_folder_with_failed_modseq_fetch(
    db, default_account, message, folder, mock_imapclient, monkeypatch
) -> None:
    other_folder = add_fake_folder(
        db.session, default_account, "[Gmail]/Trash", "trash"
    )
    mock_imapclient.add_folder_data(
        folder.name,
        {22: {b"FLAGS": (), b"X-GM-LABELS": (), b"MODSEQ": (123,)}},
    )
    mock_imapclient.add_folder_data(
        other_folder.name,
        {30: {b"FLAGS": (), b"X-GM-LABELS": (), b"MODSEQ": (125,)}},
    )
    fetch = mock_imapclient.fetch

    def fail_fetch_in_folder(*args, **kwargs):
        if mock_imapclient.selected_folder == folder.name:
            raise imaplib.IMAP4.error("FETCH failed")
        return fetch(*args, **kwargs)

    mock_imapclient.fetch = fail_fetch_in_folder
    mock_imapclient.remove_gmail_labels = mock.Mock()
    add_fake_imapuid(db.session, default_account.id, message, folder, 22)
    add_fake_imapuid(db.session, default_account.id, message, other_folder, 30)
    mock_log = mock.Mock()
    monkeypatch.setattr("inbox.actions.backends.gmail.log", mock_log)

    with writable_connection_pool(default_account.id).get() as crispin_client:
        result = change_labels(
            crispin_client,
            default_account.id,
            [message.id],
            {"removed_labels": ["\\Inbox"], "added_labels": []},
        )

    assert mock_imapclient.remove_gmail_labels.call_count == 2
    assert result.modseqs == {
        message.id: {
            str(other_folder.id): FolderModseq(
                uidvalidity=mock_imapclient.uidvalidity, modseq=125
            )
        }
    }
    mock_log.warning.assert_called_once_with(
        "Could not fetch MODSEQ after label change",
        account_id=default_account.id,
        folder_name=folder.name,
        message_ids=[message.id],
        exc_info=True,
    )


def test_change_labels_raises_connection_error_of_modseq_fetch(
    db, default_account, message, folder, mock_imapclient
) -> None:
    mock_imapclient.add_folder_data(
        folder.name,
        {22: {b"FLAGS": (), b"X-GM-LABELS": (), b"MODSEQ": (123,)}},
    )
    mock_imapclient.fetch = mock.Mock(side_effect=ConnectionResetError())
    mock_imapclient.remove_gmail_labels = mock.Mock()
    add_fake_imapuid(db.session, default_account.id, message, folder, 22)

    with (
        pytest.raises(ConnectionResetError),
        writable_connection_pool(default_account.id).get() as crispin_client,
    ):
        change_labels(
            crispin_client,
            default_account.id,
            [message.id],
            {"removed_labels": ["\\Inbox"], "added_labels": []},
        )


def test_change_labels_returns_fetched_flags_of_folders_with_fetched_data(
    db, default_account, message, folder, mock_imapclient
) -> None:
    other_message = add_fake_message(
        db.session, default_account.namespace.id, message.thread
    )
    other_folder = add_fake_folder(
        db.session, default_account, "[Gmail]/Trash", "trash"
    )
    mock_imapclient.add_folder_data(
        folder.name,
        {22: {b"FLAGS": (), b"X-GM-LABELS": (), b"MODSEQ": (123,)}},
    )
    fetched_data = {
        b"FLAGS": (b"\\Seen",),
        b"X-GM-LABELS": (b"Foo",),
        b"MODSEQ": (125,),
    }
    mock_imapclient.add_folder_data(
        other_folder.name, {30: fetched_data, 31: fetched_data}
    )
    fetch = mock_imapclient.fetch

    def fail_fetch_in_folder(*args, **kwargs):
        if mock_imapclient.selected_folder == folder.name:
            raise imaplib.IMAP4.error("FETCH failed")
        return fetch(*args, **kwargs)

    mock_imapclient.fetch = fail_fetch_in_folder
    mock_imapclient.remove_gmail_labels = mock.Mock()
    # `message` has UIDs in both folders, and `other_message` only in the
    # folder whose fetch works.
    add_fake_imapuid(db.session, default_account.id, message, folder, 22)
    add_fake_imapuid(db.session, default_account.id, message, other_folder, 30)
    add_fake_imapuid(
        db.session, default_account.id, other_message, other_folder, 31
    )

    with writable_connection_pool(default_account.id).get() as crispin_client:
        result = change_labels(
            crispin_client,
            default_account.id,
            [message.id, other_message.id],
            {"removed_labels": ["\\Inbox"], "added_labels": []},
        )

    assert result.uids_by_message_id == {
        message.id: {(folder.id, 22), (other_folder.id, 30)},
        other_message.id: {(other_folder.id, 31)},
    }
    fetched_flags = GmailFlags((b"\\Seen",), ["Foo"], 125)
    assert result.fetched_flags == {
        (other_folder.id, 30): fetched_flags,
        (other_folder.id, 31): fetched_flags,
    }


def test_save_fetched_imap_rows(db, default_account, message, folder) -> None:
    other_message = add_fake_message(
        db.session, default_account.namespace.id, message.thread
    )
    imapuid = add_fake_imapuid(
        db.session, default_account.id, message, folder, 22
    )
    imapuid.update_labels(["\\Inbox"])
    other_imapuid = add_fake_imapuid(
        db.session, default_account.id, other_message, folder, 23
    )
    other_imapuid_without_data = add_fake_imapuid(
        db.session, default_account.id, other_message, folder, 24
    )
    other_imapuid_without_data.update_labels(["\\Inbox"])
    db.session.commit()
    # The fetch has no data for UID 24, for example because the UID left the
    # folder.
    result = LabelChangeResult(
        modseqs={},
        uids_by_message_id={
            message.id: {(folder.id, 22)},
            other_message.id: {(folder.id, 23), (folder.id, 24)},
        },
        states_before_change=saved_states(
            imapuid, other_imapuid, other_imapuid_without_data
        ),
        fetched_flags={
            (folder.id, 22): GmailFlags((b"\\Seen",), ["Foo"], 123),
            (folder.id, 23): GmailFlags((b"\\Seen",), ["Foo"], 124),
        },
    )

    refreshed_message_ids = save_fetched_imap_rows(
        db.session, default_account.id, result
    )

    assert refreshed_message_ids == {message.id}
    db.session.commit()
    for fetched_imapuid in (imapuid, other_imapuid):
        assert fetched_imapuid.is_seen
        assert label_names(fetched_imapuid) == {"Foo"}
    assert not other_imapuid_without_data.is_seen
    assert label_names(other_imapuid_without_data) == {"inbox"}


def test_save_fetched_imap_rows_skips_message_without_imap_row(
    db, default_account, message, folder
) -> None:
    # The IMAP row of the UID is gone, for example because mail sync deleted
    # it after the fetch.
    result = LabelChangeResult(
        modseqs={},
        uids_by_message_id={message.id: {(folder.id, 22)}},
        states_before_change={
            (folder.id, 22): ImapUidState.from_flags_and_labels(
                (), ["\\Inbox"]
            )
        },
        fetched_flags={
            (folder.id, 22): GmailFlags((b"\\Seen",), ["Foo"], 123)
        },
    )

    refreshed_message_ids = save_fetched_imap_rows(
        db.session, default_account.id, result
    )

    assert refreshed_message_ids == set()


def test_save_fetched_imap_rows_skips_imap_row_of_other_message(
    db, default_account, message, folder
) -> None:
    other_message = add_fake_message(
        db.session, default_account.namespace.id, message.thread
    )
    # A resync of the folder gave the UID to another message after the label
    # change read the UIDs.
    other_imapuid = add_fake_imapuid(
        db.session, default_account.id, other_message, folder, 22
    )
    other_imapuid.update_labels(["\\Inbox"])
    db.session.commit()
    result = LabelChangeResult(
        modseqs={},
        uids_by_message_id={message.id: {(folder.id, 22)}},
        states_before_change={
            (folder.id, 22): ImapUidState.from_flags_and_labels(
                (), ["\\Inbox"]
            )
        },
        fetched_flags={
            (folder.id, 22): GmailFlags((b"\\Seen",), ["Foo"], 123)
        },
    )

    refreshed_message_ids = save_fetched_imap_rows(
        db.session, default_account.id, result
    )

    assert refreshed_message_ids == set()
    db.session.commit()
    assert not other_imapuid.is_seen
    assert label_names(other_imapuid) == {"inbox"}


def test_save_fetched_imap_rows_creates_one_row_of_new_label(
    db, default_account, message, folder
) -> None:
    # The message has two UIDs. For both UIDs, the fetch reports a label that
    # has no row yet.
    msg_uids = [22, 23]
    for msg_uid in msg_uids:
        add_fake_imapuid(
            db.session, default_account.id, message, folder, msg_uid
        )
    db.session.commit()
    labels_of_account = db.session.query(Label).filter(
        Label.account_id == default_account.id, Label.name == "Foo"
    )
    assert labels_of_account.count() == 0
    result = LabelChangeResult(
        modseqs={},
        uids_by_message_id={
            message.id: {(folder.id, msg_uid) for msg_uid in msg_uids}
        },
        states_before_change=saved_states(*message.imapuids),
        fetched_flags={
            (folder.id, msg_uid): GmailFlags((), ["Foo"], 123)
            for msg_uid in msg_uids
        },
    )

    refreshed_message_ids = save_fetched_imap_rows(
        db.session, default_account.id, result
    )

    assert refreshed_message_ids == {message.id}
    db.session.commit()
    assert labels_of_account.count() == 1
    for imapuid in message.imapuids:
        assert label_names(imapuid) == {"Foo"}


@pytest.mark.parametrize(
    ("flags", "labels", "saved_only_label_names"),
    [
        # After the fetch, a label is added in Gmail.
        ([], ["Bar"], ["Bar"]),
        # After the fetch, the message is marked as read in Gmail.
        ([b"\\Seen"], [], []),
        # After the fetch, the message is starred in Gmail.
        ([b"\\Flagged"], [], []),
    ],
)
def test_save_fetched_imap_rows_keeps_imap_row_changed_after_read(
    db,
    default_account,
    message,
    folder,
    monkeypatch,
    flags,
    labels,
    saved_only_label_names,
) -> None:
    imapuid = add_fake_imapuid(
        db.session, default_account.id, message, folder, 22
    )
    imapuid.update_labels(["\\Inbox"])
    db.session.commit()
    states_before_change = saved_states(imapuid)
    # Mail sync saves the change in Gmail after the label change read the
    # IMAP rows.
    imapuid.update_flags(flags)
    imapuid.update_labels(labels)
    db.session.commit()
    result = LabelChangeResult(
        modseqs={},
        uids_by_message_id={message.id: {(folder.id, 22)}},
        states_before_change=states_before_change,
        fetched_flags={(folder.id, 22): GmailFlags((), [], 123)},
    )
    mock_log = mock.Mock()
    monkeypatch.setattr("inbox.actions.backends.gmail.log", mock_log)

    refreshed_message_ids = save_fetched_imap_rows(
        db.session, default_account.id, result
    )

    assert refreshed_message_ids == set()
    db.session.commit()
    assert imapuid.saved_state() == ImapUidState.from_flags_and_labels(
        flags, labels
    )
    mock_log.info.assert_called_once_with(
        "Kept IMAP rows that changed during the label change",
        account_id=default_account.id,
        message_id=message.id,
        folder_id=folder.id,
        msg_uid=22,
        fetched_only_label_names=[],
        saved_only_label_names=saved_only_label_names,
        saved_is_seen=b"\\Seen" in flags,
        fetched_is_seen=False,
        saved_is_flagged=b"\\Flagged" in flags,
        fetched_is_flagged=False,
        before_change_label_names=["inbox"],
        before_change_is_seen=False,
        before_change_is_flagged=False,
    )


def test_save_fetched_imap_rows_skips_message_with_kept_imap_row(
    db, default_account, message, folder
) -> None:
    # The message has two UIDs.
    imapuid = add_fake_imapuid(
        db.session, default_account.id, message, folder, 22
    )
    imapuid.update_labels(["\\Inbox"])
    kept_imapuid = add_fake_imapuid(
        db.session, default_account.id, message, folder, 23
    )
    kept_imapuid.update_labels(["\\Inbox"])
    db.session.commit()
    states_before_change = saved_states(imapuid, kept_imapuid)
    # After the label change read the IMAP rows, mail sync saves a label that
    # Gmail added to UID 23 after the fetch.
    kept_imapuid.update_labels(["Bar"])
    db.session.commit()
    result = LabelChangeResult(
        modseqs={},
        uids_by_message_id={message.id: {(folder.id, 22), (folder.id, 23)}},
        states_before_change=states_before_change,
        fetched_flags={
            (folder.id, 22): GmailFlags((b"\\Seen",), ["Foo"], 123),
            (folder.id, 23): GmailFlags((b"\\Seen",), ["Foo"], 123),
        },
    )

    refreshed_message_ids = save_fetched_imap_rows(
        db.session, default_account.id, result
    )

    # The message has a kept IMAP row, so it does not count as refreshed. The
    # function still saves the fetched data in the other IMAP row.
    assert refreshed_message_ids == set()
    db.session.commit()
    assert imapuid.is_seen
    assert label_names(imapuid) == {"Foo"}
    assert not kept_imapuid.is_seen
    assert label_names(kept_imapuid) == {"Bar"}


def test_save_fetched_imap_rows_counts_imap_row_with_state_of_fetched_data(
    db, default_account, message, folder
) -> None:
    imapuid = add_fake_imapuid(
        db.session, default_account.id, message, folder, 22
    )
    imapuid.update_labels(["\\Inbox"])
    db.session.commit()
    states_before_change = saved_states(imapuid)
    # After the label change read the IMAP rows, mail sync saves newer data.
    # The data has the state of the fetched data, and also an answered flag.
    imapuid.update_flags([b"\\Seen", b"\\Answered"])
    imapuid.update_labels(["Foo"])
    db.session.commit()
    result = LabelChangeResult(
        modseqs={},
        uids_by_message_id={message.id: {(folder.id, 22)}},
        states_before_change=states_before_change,
        fetched_flags={
            (folder.id, 22): GmailFlags((b"\\Seen",), ["Foo"], 123)
        },
    )

    refreshed_message_ids = save_fetched_imap_rows(
        db.session, default_account.id, result
    )

    # The IMAP rows count as saved, and keep the newer data.
    assert refreshed_message_ids == {message.id}
    db.session.commit()
    assert imapuid.is_answered


def test_change_labels_leaves_out_message_without_imap_uids(
    db, default_account, message, mock_imapclient
) -> None:
    mock_imapclient.remove_gmail_labels = mock.Mock()

    with writable_connection_pool(default_account.id).get() as crispin_client:
        result = change_labels(
            crispin_client,
            default_account.id,
            [message.id],
            {"removed_labels": ["\\Inbox"], "added_labels": []},
        )

    mock_imapclient.remove_gmail_labels.assert_not_called()
    assert result == LabelChangeResult(
        modseqs={},
        uids_by_message_id={},
        states_before_change={},
        fetched_flags={},
    )


@pytest.mark.parametrize("obj_type", ["folder", "label"])
def test_folder_crud(db, default_account, mock_imapclient, obj_type) -> None:
    mock_imapclient.create_folder = mock.Mock()
    mock_imapclient.rename_folder = mock.Mock()
    mock_imapclient.delete_folder = mock.Mock()
    cat = add_fake_category(
        db.session, default_account.namespace.id, "MyFolder"
    )
    with writable_connection_pool(default_account.id).get() as crispin_client:
        if obj_type == "folder":
            create_folder(crispin_client, default_account.id, cat.id)
        else:
            create_label(crispin_client, default_account.id, cat.id)
        mock_imapclient.create_folder.assert_called_with("MyFolder")

        cat.display_name = "MyRenamedFolder"
        db.session.commit()
        if obj_type == "folder":
            update_folder(
                crispin_client,
                default_account.id,
                cat.id,
                {"old_name": "MyFolder", "new_name": "MyRenamedFolder"},
            )
        else:
            update_label(
                crispin_client,
                default_account.id,
                cat.id,
                {"old_name": "MyFolder", "new_name": "MyRenamedFolder"},
            )
        mock_imapclient.rename_folder.assert_called_with(
            "MyFolder", "MyRenamedFolder"
        )

        category_id = cat.id
        if obj_type == "folder":
            delete_folder(crispin_client, default_account.id, cat.id)
        else:
            delete_label(crispin_client, default_account.id, cat.id)
    mock_imapclient.delete_folder.assert_called_with("MyRenamedFolder")
    db.session.commit()
    assert db.session.query(Category).get(category_id) is None


@pytest.fixture
def patched_syncback_task(monkeypatch):
    # Ensures 'create_event' actions fail and all others succeed
    def function_for_action(name):
        def func(*args):
            if name == "create_event":
                raise Exception("Failed to create remote event")

        return func

    monkeypatch.setattr(
        "inbox.transactions.actions.function_for_action", function_for_action
    )
    monkeypatch.setattr(
        "inbox.transactions.actions.ACTION_MAX_NR_OF_RETRIES", 1
    )
    yield
    monkeypatch.undo()


# Test that failing to create a remote copy of an event marks all pending actions
# for that event as failed.
def test_failed_event_creation(
    db, patched_syncback_task, default_account, event
) -> None:
    schedule_action(
        "create_event", event, default_account.namespace.id, db.session
    )
    schedule_action(
        "update_event", event, default_account.namespace.id, db.session
    )
    schedule_action(
        "update_event", event, default_account.namespace.id, db.session
    )
    schedule_action(
        "delete_event", event, default_account.namespace.id, db.session
    )
    db.session.commit()

    NUM_WORKERS = 2  # noqa: N806
    service = SyncbackService(
        syncback_id=0,
        process_number=0,
        total_processes=NUM_WORKERS,
        num_workers=NUM_WORKERS,
    )
    service._restart_workers()
    service._process_log()

    while not service.task_queue.empty():
        time.sleep(0.1)

    # This has to be a separate while-loop because there's a brief moment where
    # the task queue is empty, but num_idle_workers hasn't been updated yet.
    # On slower systems, we might need to sleep a bit between the while-loops.
    while service.num_idle_workers != NUM_WORKERS:
        time.sleep(0.1)

    q = db.session.query(ActionLog).filter_by(record_id=event.id).all()
    assert all(a.status == "failed" for a in q)

    service.stop()


def test_failed_event_creation_keeps_actions_of_other_tables(
    db, patched_syncback_task, default_account, event
) -> None:
    create_event_action = schedule_and_fetch_action(
        db, default_account, "create_event", event
    )
    # A message can have the same ID as the event.
    message_action = ActionLog.create(
        action="change_labels",
        table_name="message",
        record_id=event.id,
        namespace_id=default_account.namespace.id,
        extra_args={"added_labels": [], "removed_labels": ["\\Inbox"]},
    )
    db.session.add(message_action)
    db.session.commit()
    service = make_syncback_service()
    batch_task = service._batch_log_entries(db.session, [create_event_action])

    batch_task.execute()

    db.session.commit()
    assert create_event_action.status == "failed"
    assert message_action.status == "pending"


@pytest.fixture
def failing_connection_pool(monkeypatch):
    """
    Patch the writable connection pool of syncback, so that each connection
    fails with a Gmail login error.
    """
    connection_pool = mock.MagicMock()
    connection_pool.get.return_value.__enter__.side_effect = LoginError(
        "[ALERT] Account exceeded command or bandwidth limits. (Failure)"
    )
    monkeypatch.setattr(
        "inbox.transactions.actions.writable_connection_pool",
        lambda account_id: connection_pool,
    )
    return connection_pool


def schedule_and_fetch_action(
    db, account, action, record, **extra_args
) -> ActionLog:
    """Schedule `action` for `record`, and return its `ActionLog`."""
    schedule_action(
        action, record, account.namespace.id, db.session, **extra_args
    )
    db.session.commit()
    return (
        db.session.query(ActionLog)
        .filter_by(
            namespace_id=account.namespace.id,
            record_id=record.id,
            action=action,
        )
        .one()
    )


def schedule_mark_as_read(db, account, message) -> ActionLog:
    """Schedule a `mark_unread` action that marks `message` as read."""
    return schedule_and_fetch_action(
        db, account, "mark_unread", message, unread=False
    )


def make_syncback_service() -> SyncbackService:
    return SyncbackService(
        syncback_id=0, process_number=0, total_processes=1, num_workers=1
    )


def make_actions_run(monkeypatch, func) -> None:
    """Patch syncback, so that each action calls `func`."""
    monkeypatch.setattr(
        "inbox.transactions.actions.function_for_action", lambda name: func
    )


def make_actions_raise(monkeypatch, error: BaseException) -> None:
    """Patch syncback, so that each action raises `error`."""

    def func(*args):
        raise error

    make_actions_run(monkeypatch, func)


def make_label_changes_return(
    monkeypatch,
    modseqs,
    uids_by_message_id=None,
    fetched_flags=None,
    states_before_change=None,
) -> None:
    """
    Patch syncback, so that each action returns a `LabelChangeResult` with
    `modseqs`, `uids_by_message_id`, `fetched_flags`, and
    `states_before_change`.
    """
    make_actions_run(
        monkeypatch,
        lambda *args: LabelChangeResult(
            modseqs=modseqs,
            uids_by_message_id=dict(uids_by_message_id or {}),
            states_before_change=dict(states_before_change or {}),
            fetched_flags=dict(fetched_flags or {}),
        ),
    )


def make_imap_row_saves_return(monkeypatch, refreshed_message_ids) -> None:
    """
    Patch syncback, so that the save of the IMAP rows changes no rows, and
    returns `refreshed_message_ids`. Thus, the IMAP rows differ from the
    fetched data, as if mail sync changed them after the save.
    """
    monkeypatch.setattr(
        "inbox.transactions.actions.save_fetched_imap_rows",
        lambda *args: set(refreshed_message_ids),
    )


@pytest.fixture
def syncback_crispin_client():
    return mock.Mock()


@pytest.fixture
def syncback_connection_pool(
    default_account, syncback_crispin_client, monkeypatch
):
    """
    Patch the writable connection pool of syncback with a real pool, whose
    only client is `syncback_crispin_client`.
    """
    connection_pool = CrispinConnectionPool(
        default_account.id, num_connections=1, readonly=False
    )
    monkeypatch.setattr(
        connection_pool, "_new_connection", lambda: syncback_crispin_client
    )
    monkeypatch.setattr(
        "inbox.transactions.actions.writable_connection_pool",
        lambda account_id: connection_pool,
    )
    return connection_pool


def test_connection_error_counts_as_failure_of_first_task(
    db, default_account, message, failing_connection_pool
) -> None:
    action_log_entry = schedule_mark_as_read(db, default_account, message)
    other_action_log_entry = schedule_and_fetch_action(
        db, default_account, "mark_starred", message, starred=True
    )
    service = make_syncback_service()
    batch_task = service._batch_log_entries(
        db.session, [action_log_entry, other_action_log_entry]
    )
    assert len(batch_task.tasks) == 2

    for retries in range(1, ACTION_MAX_NR_OF_RETRIES):
        batch_task.execute()
        # Commit to end the transaction, and read the new state of the action.
        db.session.commit()
        assert action_log_entry.retries == retries
        assert action_log_entry.status == "pending"

    batch_task.execute()
    db.session.commit()
    assert action_log_entry.retries == ACTION_MAX_NR_OF_RETRIES
    assert action_log_entry.status == "failed"
    assert other_action_log_entry.retries == 0
    assert other_action_log_entry.status == "pending"
    assert failing_connection_pool.get.call_count == ACTION_MAX_NR_OF_RETRIES


def test_connection_error_counts_as_failure_of_first_imap_task(
    db, default_account, message, event, failing_connection_pool
) -> None:
    event_action_log_entry = schedule_and_fetch_action(
        db, default_account, "update_event", event
    )
    mail_action_log_entry = schedule_mark_as_read(db, default_account, message)
    service = make_syncback_service()
    batch_task = service._batch_log_entries(
        db.session, [event_action_log_entry, mail_action_log_entry]
    )
    assert batch_task.tasks[0].action_name == "update_event"

    batch_task.execute()
    db.session.commit()

    assert event_action_log_entry.retries == 0
    assert mail_action_log_entry.retries == 1


def test_connection_error_skips_task_without_pending_actions(
    db, default_account, message, failing_connection_pool
) -> None:
    action_log_entry = schedule_mark_as_read(db, default_account, message)
    other_action_log_entry = schedule_and_fetch_action(
        db, default_account, "mark_starred", message, starred=True
    )
    service = make_syncback_service()
    batch_task = service._batch_log_entries(
        db.session, [action_log_entry, other_action_log_entry]
    )
    action_log_entry.status = "successful"
    db.session.commit()

    batch_task.execute()
    db.session.commit()

    assert action_log_entry.retries == 0
    assert other_action_log_entry.retries == 1


def test_connection_error_delays_next_attempt(
    db, default_account, message, failing_connection_pool
) -> None:
    action_log_entry = schedule_mark_as_read(db, default_account, message)
    service = make_syncback_service()
    batch_task = service._batch_log_entries(db.session, [action_log_entry])
    batch_task.execute()
    service.notify_worker_finished(batch_task.action_log_ids)
    db.session.commit()

    assert service._batch_log_entries(db.session, [action_log_entry]) is None

    action_log_entry.updated_at = datetime.utcnow() - timedelta(
        seconds=service.retry_interval + 1
    )
    db.session.commit()
    assert (
        service._batch_log_entries(db.session, [action_log_entry]) is not None
    )


def test_connection_timeout_counts_as_failure(
    db, default_account, message, failing_connection_pool, monkeypatch
) -> None:
    failing_connection_pool.get.return_value.__enter__.side_effect = (
        InterruptibleThreadTimeout()
    )
    syncback_logger = mock.MagicMock()
    monkeypatch.setattr("inbox.transactions.actions.logger", syncback_logger)
    action_log_entry = schedule_mark_as_read(db, default_account, message)
    service = make_syncback_service()
    batch_task = service._batch_log_entries(db.session, [action_log_entry])

    with pytest.raises(InterruptibleThreadTimeout):
        batch_task.execute()

    db.session.commit()
    assert action_log_entry.retries == 1
    assert action_log_entry.status == "pending"
    syncback_logger.new.return_value.warning.assert_called_once_with(
        "Syncback connection timed out",
        account_id=default_account.id,
        exc_info=True,
    )


def test_slow_connection_counts_as_failure(
    db, default_account, message, monkeypatch
) -> None:
    monkeypatch.setattr(
        "inbox.transactions.actions.writable_connection_pool",
        lambda account_id: mock.MagicMock(),
    )
    action_log_entry = schedule_mark_as_read(db, default_account, message)
    service = make_syncback_service()
    service.task_queue.put(
        service._batch_log_entries(db.session, [action_log_entry])
    )
    # With a timeout of 0 per task, the connection step uses up the deadline
    # of the batch, like a slow login.
    worker = SyncbackWorker(service, task_timeout=0)
    service.workers.append(worker)
    worker.start()
    try:
        assert service.worker_did_finish.wait(timeout=5)
    finally:
        service.stop()

    db.session.commit()
    assert action_log_entry.retries == 1
    assert action_log_entry.status == "pending"


def test_timeout_counts_as_failure_of_task(
    db, default_account, message, monkeypatch
) -> None:
    make_actions_raise(monkeypatch, InterruptibleThreadTimeout())
    monkeypatch.setattr(
        "inbox.transactions.actions.writable_connection_pool",
        lambda account_id: mock.MagicMock(),
    )
    syncback_logger = mock.MagicMock()
    monkeypatch.setattr("inbox.transactions.actions.logger", syncback_logger)
    action_log_entry = schedule_mark_as_read(db, default_account, message)
    service = make_syncback_service()
    batch_task = service._batch_log_entries(db.session, [action_log_entry])

    with pytest.raises(InterruptibleThreadTimeout):
        batch_task.execute()

    db.session.commit()
    assert action_log_entry.retries == 1
    assert action_log_entry.status == "pending"
    syncback_logger.new.return_value.warning.assert_called_once_with(
        "Syncback action timed out",
        account_id=default_account.id,
        provider=default_account.verbose_provider,
        exc_info=True,
    )


def test_worker_logs_batch_timeout(default_account, monkeypatch) -> None:
    class TimedOutTask:
        account_id = default_account.id
        action_log_ids: list[int] = []

        def timeout(self, per_task_timeout):
            return 0

        def execute(self):
            interruptible_threading.check_interrupted()

    service = make_syncback_service()
    # Patch the logger after the service exists, so that only the worker
    # uses the mock.
    syncback_logger = mock.MagicMock()
    monkeypatch.setattr("inbox.transactions.actions.logger", syncback_logger)
    service.task_queue.put(TimedOutTask())
    service._restart_workers()
    try:
        assert service.worker_did_finish.wait(timeout=5)
    finally:
        service.stop()

    syncback_logger.new.return_value.warning.assert_called_once_with(
        "Syncback batch timed out", account_id=default_account.id
    )


@pytest.mark.parametrize(
    ("error", "connection_is_usable"),
    [
        (TimeoutError("The read operation timed out"), False),
        (imaplib.IMAP4.abort("socket error: EOF"), False),
        (
            imaplib.IMAP4.error(
                "move failed: [CANNOT] UID MOVE not allowed for same folder"
            ),
            True,
        ),
        (ValueError(), True),
    ],
)
def test_failed_task_logs_out_only_usable_connection(
    db,
    default_account,
    message,
    syncback_crispin_client,
    syncback_connection_pool,
    monkeypatch,
    error,
    connection_is_usable,
) -> None:
    make_actions_raise(monkeypatch, error)
    syncback_logger = mock.MagicMock()
    monkeypatch.setattr("inbox.transactions.actions.logger", syncback_logger)
    action_log_entry = schedule_mark_as_read(db, default_account, message)
    other_action_log_entry = schedule_and_fetch_action(
        db, default_account, "mark_starred", message, starred=True
    )
    service = make_syncback_service()
    batch_task = service._batch_log_entries(
        db.session, [action_log_entry, other_action_log_entry]
    )
    assert len(batch_task.tasks) == 2

    batch_task.execute()

    db.session.commit()
    assert action_log_entry.retries == 1
    assert action_log_entry.status == "pending"
    # The batch stops after the first failure.
    assert other_action_log_entry.retries == 0
    assert syncback_crispin_client.logout.called is connection_is_usable
    assert syncback_crispin_client.shutdown.called is not connection_is_usable
    assert syncback_connection_pool._queue.get_nowait() is None
    if connection_is_usable:
        expected_message = "Pausing syncback tasks due to error"
    else:
        expected_message = "Syncback connection unusable"
    syncback_logger.new.return_value.info.assert_any_call(
        expected_message, account_id=default_account.id
    )


def test_timeout_discards_connection(
    db,
    default_account,
    message,
    syncback_crispin_client,
    syncback_connection_pool,
    monkeypatch,
) -> None:
    make_actions_raise(monkeypatch, InterruptibleThreadTimeout())
    action_log_entry = schedule_mark_as_read(db, default_account, message)
    service = make_syncback_service()
    batch_task = service._batch_log_entries(db.session, [action_log_entry])

    with pytest.raises(InterruptibleThreadTimeout):
        batch_task.execute()

    db.session.commit()
    assert action_log_entry.retries == 1
    assert not syncback_crispin_client.logout.called
    assert syncback_crispin_client.shutdown.called
    assert syncback_connection_pool._queue.get_nowait() is None


def test_change_labels_task_saves_modseqs_with_status(
    db, default_account, message, syncback_connection_pool, monkeypatch
) -> None:
    folder_modseqs = {"7": FolderModseq(uidvalidity=1, modseq=123)}
    make_label_changes_return(monkeypatch, {message.id: folder_modseqs})
    action_log_entry = schedule_and_fetch_action(
        db,
        default_account,
        "change_labels",
        message,
        removed_labels=["\\Inbox"],
        added_labels=[],
    )
    service = make_syncback_service()
    batch_task = service._batch_log_entries(db.session, [action_log_entry])

    batch_task.execute()

    db.session.commit()
    assert action_log_entry.status == "successful"
    assert action_log_entry.extra_args == {
        "removed_labels": ["\\Inbox"],
        "added_labels": [],
        CHANGE_LABELS_MODSEQ_KEY: folder_modseqs,
    }


def test_change_labels_tasks_save_modseqs_of_own_message(
    db, default_account, message, syncback_connection_pool, monkeypatch
) -> None:
    other_message = add_fake_message(
        db.session, default_account.namespace.id, message.thread
    )
    folder_modseqs_by_message_id = {
        message.id: {"7": FolderModseq(uidvalidity=1, modseq=123)},
        other_message.id: {"7": FolderModseq(uidvalidity=1, modseq=124)},
    }
    make_label_changes_return(monkeypatch, folder_modseqs_by_message_id)
    action_log_entries = [
        schedule_and_fetch_action(
            db,
            default_account,
            "change_labels",
            record,
            removed_labels=["\\Inbox"],
            added_labels=[],
        )
        for record in [message, other_message]
    ]
    service = make_syncback_service()
    batch_task = service._batch_log_entries(db.session, action_log_entries)

    batch_task.execute()

    db.session.commit()
    assert [
        action_log_entry.extra_args[CHANGE_LABELS_MODSEQ_KEY]
        for action_log_entry in action_log_entries
    ] == [
        folder_modseqs_by_message_id[message.id],
        folder_modseqs_by_message_id[other_message.id],
    ]


def test_change_labels_task_saves_modseqs_on_each_action_of_message(
    db, default_account, message, syncback_connection_pool, monkeypatch
) -> None:
    folder_modseqs = {"7": FolderModseq(uidvalidity=1, modseq=123)}
    make_label_changes_return(monkeypatch, {message.id: folder_modseqs})
    # Use different labels, so that the second action is not a duplicate of
    # the first one.
    for removed_labels in (["\\Inbox"], ["\\Important"]):
        schedule_action(
            "change_labels",
            message,
            default_account.namespace.id,
            db.session,
            removed_labels=removed_labels,
            added_labels=[],
        )
    db.session.commit()
    action_log_entries = (
        db.session.query(ActionLog)
        .filter_by(
            namespace_id=default_account.namespace.id,
            record_id=message.id,
            action="change_labels",
        )
        .order_by(ActionLog.id)
        .all()
    )
    service = make_syncback_service()
    batch_task = service._batch_log_entries(db.session, action_log_entries)
    assert len(batch_task.tasks) == 1

    batch_task.execute()

    db.session.commit()
    assert [
        (
            action_log_entry.status,
            action_log_entry.extra_args[CHANGE_LABELS_MODSEQ_KEY],
        )
        for action_log_entry in action_log_entries
    ] == [("successful", folder_modseqs), ("successful", folder_modseqs)]


ARCHIVED_MESSAGE_UID = 22


def category_names(message) -> set[str]:
    """
    Return the names of the categories of `message`. A category of a custom
    label gives its display name.
    """
    return {
        category.name or category.display_name
        for category in message.categories
    }


@pytest.fixture
def archived_message(db, default_account, message, folder):
    """
    Return a read Gmail message that the API archived. Its IMAP rows still
    have the inbox label, and its `change_labels` action is pending.
    """
    imapuid = add_fake_imapuid(
        db.session, default_account.id, message, folder, ARCHIVED_MESSAGE_UID
    )
    imapuid.update_flags([b"\\Seen"])
    imapuid.update_labels(["\\Inbox"])
    update_message_metadata(db.session, default_account, message, False)
    db.session.commit()
    update_thread(
        message.thread,
        {"label_ids": [folder.category.public_id]},
        db.session,
        optimistic=False,
    )
    db.session.commit()
    assert category_names(message) == {"all"}

    yield message

    db.session.rollback()
    db.session.query(ActionLog).delete(synchronize_session=False)
    db.session.commit()
    delete_imapuids(db.session)


def report_after_label_change(mock_imapclient, folder, labels) -> None:
    """
    Make Gmail report the archived message as read, with `labels`, after the
    label change.
    """
    mock_imapclient.add_folder_data(
        folder.name,
        {
            ARCHIVED_MESSAGE_UID: {
                b"FLAGS": (b"\\Seen",),
                b"X-GM-LABELS": labels,
                b"MODSEQ": (123,),
            }
        },
    )
    mock_imapclient.remove_gmail_labels = mock.Mock()


def pending_label_change(db, message) -> ActionLog:
    """Return the pending `change_labels` action of `message`."""
    return (
        db.session.query(ActionLog)
        .filter_by(
            record_id=message.id, action="change_labels", status="pending"
        )
        .one()
    )


def test_change_labels_task_rebuilds_categories_from_saved_imap_rows(
    db, default_account, folder, archived_message, mock_imapclient
) -> None:
    # After the change, Gmail also reports a label that the local state does
    # not have.
    report_after_label_change(mock_imapclient, folder, (b"Foo",))
    action_log_entry = pending_label_change(db, archived_message)
    transaction_count = count_message_transactions(db, archived_message)
    service = make_syncback_service()
    batch_task = service._batch_log_entries(db.session, [action_log_entry])

    batch_task.execute()

    db.session.commit()
    assert action_log_entry.status == "successful"
    assert category_names(archived_message) == {"all", "Foo"}
    assert (
        count_message_transactions(db, archived_message)
        == transaction_count + 1
    )


def test_change_labels_tasks_of_batch_rebuild_own_message(
    db, default_account, message, folder, mock_imapclient
) -> None:
    other_message = add_fake_message(
        db.session, default_account.namespace.id, message.thread
    )
    thread_messages = [message, other_message]
    msg_uids = [22, 23]
    for thread_message, msg_uid in zip(thread_messages, msg_uids):
        imapuid = add_fake_imapuid(
            db.session, default_account.id, thread_message, folder, msg_uid
        )
        imapuid.update_labels(["\\Inbox"])
        update_message_metadata(
            db.session, default_account, thread_message, False
        )
    db.session.commit()
    update_thread(
        message.thread,
        {"label_ids": [folder.category.public_id]},
        db.session,
        optimistic=False,
    )
    db.session.commit()
    # After the change, Gmail also reports a label that the local state does
    # not have.
    mock_imapclient.add_folder_data(
        folder.name,
        {
            msg_uid: {
                b"FLAGS": (),
                b"X-GM-LABELS": (b"Foo",),
                b"MODSEQ": (123,),
            }
            for msg_uid in msg_uids
        },
    )
    mock_imapclient.remove_gmail_labels = mock.Mock()
    action_log_entries = [
        pending_label_change(db, thread_message)
        for thread_message in thread_messages
    ]
    transaction_counts = [
        count_message_transactions(db, thread_message)
        for thread_message in thread_messages
    ]
    service = make_syncback_service()
    batch_task = service._batch_log_entries(db.session, action_log_entries)
    # Each message has its own task.
    assert len(batch_task.tasks) == 2

    batch_task.execute()

    db.session.commit()
    assert [
        call.args[0]
        for call in mock_imapclient.remove_gmail_labels.call_args_list
    ] == [[22], [23]]
    # Each task rebuilds only its own message. A rebuild by the task of the
    # other message adds a second transaction.
    for thread_message, transaction_count in zip(
        thread_messages, transaction_counts
    ):
        assert label_names(thread_message.imapuids[0]) == {"Foo"}
        assert category_names(thread_message) == {"all", "Foo"}
        assert (
            count_message_transactions(db, thread_message)
            == transaction_count + 1
        )


def test_change_labels_task_keeps_categories_that_match_imap_rows(
    db, default_account, folder, archived_message, mock_imapclient
) -> None:
    report_after_label_change(mock_imapclient, folder, ())
    action_log_entry = pending_label_change(db, archived_message)
    transaction_count = count_message_transactions(db, archived_message)
    service = make_syncback_service()
    batch_task = service._batch_log_entries(db.session, [action_log_entry])

    batch_task.execute()

    db.session.commit()
    assert action_log_entry.status == "successful"
    assert label_names(archived_message.imapuids[0]) == set()
    assert category_names(archived_message) == {"all"}
    assert (
        count_message_transactions(db, archived_message) == transaction_count
    )


@pytest.mark.parametrize(
    ("labels", "added_category_names"),
    [
        # After the change, Gmail still reports the inbox label.
        ((b"\\Inbox",), ["inbox"]),
        # A category of a custom label goes into the log with its display
        # name.
        ((b"Foo",), ["Foo"]),
    ],
)
def test_change_labels_task_logs_categories_of_rebuild(
    db,
    default_account,
    folder,
    archived_message,
    mock_imapclient,
    monkeypatch,
    labels,
    added_category_names,
) -> None:
    report_after_label_change(mock_imapclient, folder, labels)
    mock_log = mock.Mock()
    monkeypatch.setattr("inbox.mailsync.backends.imap.common.log", mock_log)
    action_log_entry = pending_label_change(db, archived_message)
    service = make_syncback_service()
    batch_task = service._batch_log_entries(db.session, [action_log_entry])

    batch_task.execute()

    db.session.commit()
    assert category_names(archived_message) == {"all", *added_category_names}
    mock_log.warning.assert_any_call(
        "Rebuilt message categories after a change_labels action",
        account_id=default_account.id,
        message_id=archived_message.id,
        added_category_names=added_category_names,
        removed_category_names=[],
    )


def test_change_labels_task_logs_removed_categories_of_rebuild(
    db, default_account, folder, archived_message, mock_imapclient, monkeypatch
) -> None:
    # The user removed a label in Gmail while the action was pending. A poll
    # saved the IMAP rows without the label, and the pending action kept its
    # category.
    label = Label.find_or_create(db.session, default_account, "Foo")
    archived_message.categories.add(label.category)
    db.session.commit()
    assert category_names(archived_message) == {"all", "Foo"}
    report_after_label_change(mock_imapclient, folder, ())
    mock_log = mock.Mock()
    monkeypatch.setattr("inbox.mailsync.backends.imap.common.log", mock_log)
    action_log_entry = pending_label_change(db, archived_message)
    service = make_syncback_service()
    batch_task = service._batch_log_entries(db.session, [action_log_entry])

    batch_task.execute()

    db.session.commit()
    assert category_names(archived_message) == {"all"}
    mock_log.warning.assert_any_call(
        "Rebuilt message categories after a change_labels action",
        account_id=default_account.id,
        message_id=archived_message.id,
        added_category_names=[],
        removed_category_names=["Foo"],
    )


def test_change_labels_task_logs_flags_of_rebuild(
    db, default_account, folder, archived_message, mock_imapclient, monkeypatch
) -> None:
    # Gmail reports the message as read only after the label change.
    archived_message.imapuids[0].update_flags([])
    update_message_metadata(
        db.session, default_account, archived_message, False
    )
    db.session.commit()
    assert not archived_message.is_read
    report_after_label_change(mock_imapclient, folder, ())
    mock_log = mock.Mock()
    monkeypatch.setattr("inbox.mailsync.backends.imap.common.log", mock_log)
    action_log_entry = pending_label_change(db, archived_message)
    transaction_count = count_message_transactions(db, archived_message)
    service = make_syncback_service()
    batch_task = service._batch_log_entries(db.session, [action_log_entry])

    batch_task.execute()

    db.session.commit()
    assert archived_message.is_read
    assert category_names(archived_message) == {"all"}
    assert (
        count_message_transactions(db, archived_message)
        == transaction_count + 1
    )
    mock_log.info.assert_any_call(
        "Rebuilt message flags after a change_labels action",
        account_id=default_account.id,
        message_id=archived_message.id,
        is_read=True,
        is_starred=False,
    )
    logged_messages = [
        call.args[0] for call in mock_log.warning.call_args_list
    ]
    assert (
        "Rebuilt message categories after a change_labels action"
        not in logged_messages
    )


def test_change_labels_task_keeps_flags_of_pending_flag_changes(
    db, default_account, folder, archived_message, mock_imapclient
) -> None:
    # Gmail still reports the message as read and not starred after the
    # label change.
    report_after_label_change(mock_imapclient, folder, ())
    action_log_entry = pending_label_change(db, archived_message)
    # The API marks the message as unread and starred. The actions of these
    # changes run after the label change.
    update_thread(
        archived_message.thread,
        {"unread": True, "starred": True},
        db.session,
        optimistic=True,
    )
    db.session.commit()
    service = make_syncback_service()
    batch_task = service._batch_log_entries(db.session, [action_log_entry])

    batch_task.execute()

    db.session.commit()
    assert action_log_entry.status == "successful"
    assert archived_message.imapuids[0].is_seen
    assert not archived_message.is_read
    assert archived_message.is_starred


@pytest.mark.parametrize(
    ("saved_flags", "flag_update", "is_read", "is_starred"),
    [
        # The IMAP rows are unread before the label change. The API stars
        # the message.
        ([], {"starred": True}, True, True),
        # The IMAP rows are starred before the label change. The API marks
        # the message as unread.
        ([b"\\Seen", b"\\Flagged"], {"unread": True}, False, False),
    ],
)
def test_change_labels_task_keeps_only_flag_of_pending_flag_change(
    db,
    default_account,
    folder,
    archived_message,
    mock_imapclient,
    saved_flags,
    flag_update,
    is_read,
    is_starred,
) -> None:
    archived_message.imapuids[0].update_flags(saved_flags)
    update_message_metadata(
        db.session, default_account, archived_message, False
    )
    db.session.commit()
    # Gmail reports the message as read and not starred after the label
    # change.
    report_after_label_change(mock_imapclient, folder, ())
    action_log_entry = pending_label_change(db, archived_message)
    # The action of the flag change runs after the label change. Give a copy,
    # so that the parameters of the test stay as they are.
    update_thread(
        archived_message.thread,
        dict(flag_update),
        db.session,
        optimistic=True,
    )
    db.session.commit()
    service = make_syncback_service()
    batch_task = service._batch_log_entries(db.session, [action_log_entry])

    batch_task.execute()

    db.session.commit()
    assert action_log_entry.status == "successful"
    # The flag without a pending action follows the IMAP rows.
    assert (archived_message.is_read, archived_message.is_starred) == (
        is_read,
        is_starred,
    )


def completed_log_calls(syncback_logger) -> list:
    """Return the calls that logged the completion of a syncback task."""
    return [
        call
        for call in syncback_logger.new.return_value.info.call_args_list
        if call.args[0] == "syncback action completed"
    ]


def test_change_labels_task_logs_rebuild_latency(
    db, default_account, message, syncback_connection_pool, monkeypatch
) -> None:
    make_label_changes_return(monkeypatch, {})
    syncback_logger = mock.MagicMock()
    monkeypatch.setattr("inbox.transactions.actions.logger", syncback_logger)
    action_log_entry = schedule_and_fetch_action(
        db,
        default_account,
        "change_labels",
        message,
        removed_labels=["\\Inbox"],
        added_labels=[],
    )
    service = make_syncback_service()
    batch_task = service._batch_log_entries(db.session, [action_log_entry])

    batch_task.execute()

    completed_calls = completed_log_calls(syncback_logger)
    assert len(completed_calls) == 1
    assert isinstance(completed_calls[0].kwargs["rebuild_latency"], float)


def test_other_task_does_not_log_rebuild_latency(
    db, default_account, message, syncback_connection_pool, monkeypatch
) -> None:
    make_actions_run(monkeypatch, lambda *args: None)
    syncback_logger = mock.MagicMock()
    monkeypatch.setattr("inbox.transactions.actions.logger", syncback_logger)
    action_log_entry = schedule_mark_as_read(db, default_account, message)
    service = make_syncback_service()
    batch_task = service._batch_log_entries(db.session, [action_log_entry])

    batch_task.execute()

    db.session.commit()
    assert action_log_entry.status == "successful"
    completed_calls = completed_log_calls(syncback_logger)
    assert len(completed_calls) == 1
    assert "rebuild_latency" not in completed_calls[0].kwargs


def test_change_labels_task_keeps_categories_during_other_label_change(
    db, default_account, folder, archived_message, mock_imapclient
) -> None:
    report_after_label_change(mock_imapclient, folder, (b"Foo",))
    action_log_entry = pending_label_change(db, archived_message)
    service = make_syncback_service()
    batch_task = service._batch_log_entries(db.session, [action_log_entry])
    # The batch does not run this action. Use other labels, so that the
    # action is not a duplicate of the first one.
    schedule_action(
        "change_labels",
        archived_message,
        archived_message.namespace_id,
        db.session,
        removed_labels=["\\Important"],
        added_labels=[],
    )
    db.session.commit()

    batch_task.execute()

    db.session.commit()
    assert action_log_entry.status == "successful"
    assert category_names(archived_message) == {"all"}


@pytest.mark.parametrize(
    "uid_flags",
    [
        # Mail sync wrote other labels in the IMAP rows after the task saved
        # them.
        GmailFlags((b"\\Seen",), [], 123),
        # Mail sync wrote another read state in the IMAP rows after the task
        # saved them.
        GmailFlags((), ["\\Inbox"], 123),
        # Mail sync wrote another starred state in the IMAP rows after the
        # task saved them.
        GmailFlags((b"\\Seen", b"\\Flagged"), ["\\Inbox"], 123),
        # The UID is not one of the UIDs whose IMAP rows the task saved.
        None,
    ],
)
def test_change_labels_task_skips_message_rebuild_with_changed_imap_rows(
    db,
    default_account,
    folder,
    archived_message,
    syncback_connection_pool,
    monkeypatch,
    uid_flags,
) -> None:
    # The IMAP rows of the archived message are read, not starred, and still
    # have the inbox label.
    folder_uid = (folder.id, ARCHIVED_MESSAGE_UID)
    fetched_flags = {} if uid_flags is None else {folder_uid: uid_flags}
    make_label_changes_return(
        monkeypatch, {}, {archived_message.id: {folder_uid}}, fetched_flags
    )
    make_imap_row_saves_return(monkeypatch, {archived_message.id})
    mock_log = mock.Mock()
    monkeypatch.setattr("inbox.mailsync.backends.imap.common.log", mock_log)
    action_log_entry = pending_label_change(db, archived_message)
    transaction_count = count_message_transactions(db, archived_message)
    service = make_syncback_service()
    batch_task = service._batch_log_entries(db.session, [action_log_entry])

    batch_task.execute()

    db.session.commit()
    assert action_log_entry.status == "successful"
    # A rebuild from these IMAP rows brings back `inbox`.
    assert category_names(archived_message) == {"all"}
    assert (
        count_message_transactions(db, archived_message) == transaction_count
    )
    mock_log.warning.assert_called_once_with(
        "Skipped message rebuild with changed IMAP rows",
        account_id=default_account.id,
        message_id=archived_message.id,
        folder_id=folder.id,
        msg_uid=ARCHIVED_MESSAGE_UID,
        has_fetched_data=uid_flags is not None,
        imap_category_names=["all", "inbox"],
    )


def test_change_labels_task_saves_imap_rows_with_status(
    db, default_account, folder, archived_message, mock_imapclient, monkeypatch
) -> None:
    report_after_label_change(mock_imapclient, folder, ())
    action_log_entry = pending_label_change(db, archived_message)
    action_log_id = action_log_entry.id
    namespace_id = archived_message.namespace_id
    statuses_at_save = []

    def save_imap_rows(db_session, account_id, result):
        # Read the status in the session of the save, and the committed
        # status in another session.
        with session_scope(namespace_id) as other_session:
            statuses_at_save.append((
                db_session.query(ActionLog.status)
                .filter(ActionLog.id == action_log_id)
                .scalar(),
                other_session.query(ActionLog.status)
                .filter(ActionLog.id == action_log_id)
                .scalar(),
            ))
        return save_fetched_imap_rows(db_session, account_id, result)

    monkeypatch.setattr(
        "inbox.transactions.actions.save_fetched_imap_rows", save_imap_rows
    )
    service = make_syncback_service()
    batch_task = service._batch_log_entries(db.session, [action_log_entry])

    batch_task.execute()

    # The save runs in the transaction that marks the action as successful,
    # before its commit.
    assert statuses_at_save == [("successful", "pending")]
    db.session.commit()
    assert action_log_entry.status == "successful"
    assert action_log_entry.extra_args[CHANGE_LABELS_MODSEQ_KEY] == {
        str(folder.id): FolderModseq(
            uidvalidity=mock_imapclient.uidvalidity, modseq=123
        )
    }
    assert label_names(archived_message.imapuids[0]) == set()


def test_change_labels_task_keeps_imap_rows_of_poll_after_fetch(
    db, default_account, folder, archived_message, mock_imapclient, monkeypatch
) -> None:
    # Gmail reports the message as read and without labels after the label
    # change.
    report_after_label_change(mock_imapclient, folder, ())
    fetch = mock_imapclient.fetch
    account_id = default_account.id
    folder_id = folder.id

    def fetch_then_poll(*args, **kwargs):
        response = fetch(*args, **kwargs)
        # After the fetch, the message is marked as unread in Gmail. A poll
        # saves this change before the task commits.
        with session_scope(account_id) as poll_session:
            update_metadata(
                account_id,
                folder_id,
                "all",
                {ARCHIVED_MESSAGE_UID: GmailFlags((), [], 124)},
                poll_session,
            )
        return response

    mock_imapclient.fetch = fetch_then_poll
    syncback_logger = mock.MagicMock()
    monkeypatch.setattr("inbox.transactions.actions.logger", syncback_logger)
    action_log_entry = pending_label_change(db, archived_message)
    service = make_syncback_service()
    batch_task = service._batch_log_entries(db.session, [action_log_entry])

    batch_task.execute()

    db.session.commit()
    assert action_log_entry.status == "successful"
    # The IMAP rows and the message keep the data of the poll.
    imapuid = archived_message.imapuids[0]
    assert not imapuid.is_seen
    assert label_names(imapuid) == set()
    assert not archived_message.is_read
    assert category_names(archived_message) == {"all"}
    syncback_logger.new.return_value.warning.assert_any_call(
        "Skipped message rebuild without fresh IMAP rows",
        message_ids=[archived_message.id],
    )


def test_change_labels_task_succeeds_if_imap_row_save_fails(
    db, default_account, folder, archived_message, mock_imapclient, monkeypatch
) -> None:
    report_after_label_change(mock_imapclient, folder, (b"Foo",))
    monkeypatch.setattr(
        ImapUid, "update_labels", mock.Mock(side_effect=ValueError())
    )
    syncback_logger = mock.MagicMock()
    monkeypatch.setattr("inbox.transactions.actions.logger", syncback_logger)
    action_log_entry = pending_label_change(db, archived_message)
    service = make_syncback_service()
    batch_task = service._batch_log_entries(db.session, [action_log_entry])

    batch_task.execute()

    db.session.commit()
    assert action_log_entry.status == "successful"
    assert action_log_entry.retries == 0
    assert action_log_entry.extra_args[CHANGE_LABELS_MODSEQ_KEY] == {
        str(folder.id): FolderModseq(
            uidvalidity=mock_imapclient.uidvalidity, modseq=123
        )
    }
    # The IMAP rows still have the inbox label, so the message is not
    # rebuilt.
    assert label_names(archived_message.imapuids[0]) == {"inbox"}
    assert category_names(archived_message) == {"all"}
    task_log = syncback_logger.new.return_value
    task_log.warning.assert_any_call(
        "Could not save IMAP rows after label change",
        message_ids=[archived_message.id],
        exc_info=True,
    )
    task_log.warning.assert_any_call(
        "Skipped message rebuild without fresh IMAP rows",
        message_ids=[archived_message.id],
    )


def test_change_labels_task_succeeds_if_message_rebuild_fails(
    db,
    default_account,
    folder,
    archived_message,
    syncback_connection_pool,
    monkeypatch,
) -> None:
    folder_uid = (folder.id, ARCHIVED_MESSAGE_UID)
    fetched_flags = {folder_uid: GmailFlags((b"\\Seen",), ["Foo"], 123)}
    make_label_changes_return(
        monkeypatch,
        {},
        {archived_message.id: {folder_uid}},
        fetched_flags,
        saved_states(*archived_message.imapuids),
    )
    rebuild_message_metadata = mock.Mock(side_effect=ValueError())
    monkeypatch.setattr(
        "inbox.transactions.actions.rebuild_message_metadata_from_imap_rows",
        rebuild_message_metadata,
    )
    syncback_logger = mock.MagicMock()
    monkeypatch.setattr("inbox.transactions.actions.logger", syncback_logger)
    action_log_entry = pending_label_change(db, archived_message)
    service = make_syncback_service()
    batch_task = service._batch_log_entries(db.session, [action_log_entry])

    batch_task.execute()

    db.session.commit()
    rebuild_message_metadata.assert_called_once_with(
        mock.ANY,
        default_account.id,
        {archived_message.id},
        fetched_flags=fetched_flags,
    )
    assert action_log_entry.status == "successful"
    assert action_log_entry.retries == 0
    # The IMAP rows stay saved.
    assert label_names(archived_message.imapuids[0]) == {"Foo"}
    syncback_logger.new.return_value.exception.assert_called_once_with(
        "Could not rebuild message metadata"
    )


def test_change_labels_task_skips_message_rebuild_without_fetched_data(
    db, default_account, folder, archived_message, mock_imapclient, monkeypatch
) -> None:
    report_after_label_change(mock_imapclient, folder, ())
    mock_imapclient.fetch = mock.Mock(
        side_effect=imaplib.IMAP4.error("FETCH failed")
    )
    syncback_logger = mock.MagicMock()
    monkeypatch.setattr("inbox.transactions.actions.logger", syncback_logger)
    action_log_entry = pending_label_change(db, archived_message)
    transaction_count = count_message_transactions(db, archived_message)
    service = make_syncback_service()
    batch_task = service._batch_log_entries(db.session, [action_log_entry])

    batch_task.execute()

    db.session.commit()
    assert action_log_entry.status == "successful"
    assert action_log_entry.retries == 0
    # The IMAP rows still have the inbox label. A rebuild from them brings
    # back `inbox`.
    assert label_names(archived_message.imapuids[0]) == {"inbox"}
    assert category_names(archived_message) == {"all"}
    assert (
        count_message_transactions(db, archived_message) == transaction_count
    )
    syncback_logger.new.return_value.warning.assert_any_call(
        "Skipped message rebuild without fresh IMAP rows",
        message_ids=[archived_message.id],
    )


def test_change_labels_tasks_skip_message_with_uid_in_failed_fetch(
    db, default_account, message, folder, mock_imapclient, monkeypatch
) -> None:
    other_message = add_fake_message(
        db.session, default_account.namespace.id, message.thread
    )
    other_folder = add_fake_folder(
        db.session, default_account, "[Gmail]/Trash", "trash"
    )
    # `message` has UIDs in both folders, and `other_message` only in the
    # folder whose fetch works.
    add_fake_imapuid(db.session, default_account.id, message, folder, 22)
    add_fake_imapuid(db.session, default_account.id, message, other_folder, 30)
    add_fake_imapuid(
        db.session, default_account.id, other_message, other_folder, 31
    )
    thread_messages = [message, other_message]
    for thread_message in thread_messages:
        update_message_metadata(
            db.session, default_account, thread_message, False
        )
    db.session.commit()
    # After the change, Gmail also reports a label that the local state does
    # not have.
    fetched_data = {
        b"FLAGS": (),
        b"X-GM-LABELS": (b"Foo",),
        b"MODSEQ": (125,),
    }
    mock_imapclient.add_folder_data(folder.name, {22: fetched_data})
    mock_imapclient.add_folder_data(
        other_folder.name, {30: fetched_data, 31: fetched_data}
    )
    fetch = mock_imapclient.fetch

    def fail_fetch_in_folder(*args, **kwargs):
        if mock_imapclient.selected_folder == folder.name:
            raise imaplib.IMAP4.error("FETCH failed")
        return fetch(*args, **kwargs)

    mock_imapclient.fetch = fail_fetch_in_folder
    mock_imapclient.remove_gmail_labels = mock.Mock()
    syncback_logger = mock.MagicMock()
    monkeypatch.setattr("inbox.transactions.actions.logger", syncback_logger)
    action_log_entries = [
        schedule_and_fetch_action(
            db,
            default_account,
            "change_labels",
            thread_message,
            removed_labels=["\\Inbox"],
            added_labels=[],
        )
        for thread_message in thread_messages
    ]
    service = make_syncback_service()
    batch_task = service._batch_log_entries(db.session, action_log_entries)

    batch_task.execute()

    db.session.commit()
    assert [
        action_log_entry.status for action_log_entry in action_log_entries
    ] == ["successful", "successful"]
    imapuids_by_folder_uid = {
        (imapuid.folder_id, imapuid.msg_uid): imapuid
        for thread_message in thread_messages
        for imapuid in thread_message.imapuids
    }
    # Only the UIDs in the folder whose fetch works have the fetched labels.
    assert label_names(imapuids_by_folder_uid[folder.id, 22]) == set()
    assert label_names(imapuids_by_folder_uid[other_folder.id, 30]) == {"Foo"}
    assert label_names(imapuids_by_folder_uid[other_folder.id, 31]) == {"Foo"}
    # Only `other_message` has fresh IMAP rows for all its UIDs.
    assert category_names(message) == {"all", "trash"}
    assert category_names(other_message) == {"trash", "Foo"}
    skipped_calls = [
        call
        for call in syncback_logger.new.return_value.warning.call_args_list
        if call.args[0] == "Skipped message rebuild without fresh IMAP rows"
    ]
    assert skipped_calls == [
        mock.call(
            "Skipped message rebuild without fresh IMAP rows",
            message_ids=[message.id],
        )
    ]


def test_change_labels_task_does_not_log_message_without_imap_uids(
    db, default_account, message, mock_imapclient, monkeypatch
) -> None:
    syncback_logger = mock.MagicMock()
    monkeypatch.setattr("inbox.transactions.actions.logger", syncback_logger)
    action_log_entry = schedule_and_fetch_action(
        db,
        default_account,
        "change_labels",
        message,
        removed_labels=["\\Inbox"],
        added_labels=[],
    )
    service = make_syncback_service()
    batch_task = service._batch_log_entries(db.session, [action_log_entry])

    batch_task.execute()

    db.session.commit()
    assert action_log_entry.status == "successful"
    warning_messages = [
        call.args[0]
        for call in syncback_logger.new.return_value.warning.call_args_list
    ]
    assert (
        "Skipped message rebuild without fresh IMAP rows"
        not in warning_messages
    )


def test_move_uses_imap_move_when_supported(
    db, default_account, message, folder, mock_imapclient
) -> None:
    """Test that IMAP MOVE command is used when the server supports it."""
    mock_imapclient.add_folder_data(folder.name, {})
    mock_imapclient.add_folder_data("Archive", {})
    mock_imapclient.capabilities = mock.Mock(
        return_value=[b"IMAP4rev1", b"MOVE"]
    )
    mock_imapclient.move = mock.Mock()
    mock_imapclient.copy = mock.Mock()
    mock_imapclient.delete_messages = mock.Mock()
    add_fake_imapuid(db.session, default_account.id, message, folder, 42)

    with writable_connection_pool(default_account.id).get() as crispin_client:
        move(
            crispin_client,
            default_account.id,
            message.id,
            {"destination": "Archive"},
        )

    mock_imapclient.move.assert_called_once_with([42], "Archive")
    mock_imapclient.copy.assert_not_called()


def test_move_falls_back_to_copy_delete_when_move_not_supported(
    db, default_account, message, folder, mock_imapclient
) -> None:
    """Test fallback to COPY+DELETE when MOVE is not supported."""
    mock_imapclient.add_folder_data(folder.name, {})
    mock_imapclient.add_folder_data("Archive", {})
    mock_imapclient.capabilities = mock.Mock(return_value=[b"IMAP4rev1"])
    mock_imapclient.move = mock.Mock()
    mock_imapclient.copy = mock.Mock()
    mock_imapclient.delete_messages = mock.Mock()
    mock_imapclient.expunge = mock.Mock()
    add_fake_imapuid(db.session, default_account.id, message, folder, 42)

    with writable_connection_pool(default_account.id).get() as crispin_client:
        move(
            crispin_client,
            default_account.id,
            message.id,
            {"destination": "Archive"},
        )

    mock_imapclient.move.assert_not_called()
    mock_imapclient.copy.assert_called_once_with([42], "Archive")
    # delete_uids converts UIDs to strings before calling delete_messages
    mock_imapclient.delete_messages.assert_called_once_with(
        ["42"], silent=True
    )


def test_move_skips_uids_in_destination_folder(
    db, default_account, message, folder, mock_imapclient
) -> None:
    """Test that only the UIDs outside the destination folder are moved."""
    archive_folder = add_fake_folder(
        db.session, default_account, "Archive", "archive"
    )
    mock_imapclient.add_folder_data(folder.name, {})
    mock_imapclient.add_folder_data(archive_folder.name, {})
    mock_imapclient.capabilities = mock.Mock(
        return_value=[b"IMAP4rev1", b"MOVE"]
    )
    mock_imapclient.move = mock.Mock()
    add_fake_imapuid(db.session, default_account.id, message, folder, 42)
    add_fake_imapuid(
        db.session, default_account.id, message, archive_folder, 43
    )

    with writable_connection_pool(default_account.id).get() as crispin_client:
        move(
            crispin_client,
            default_account.id,
            message.id,
            {"destination": archive_folder.category.display_name},
        )

    mock_imapclient.move.assert_called_once_with([42], "Archive")


def test_move_does_nothing_when_message_is_only_in_destination_folder(
    db, default_account, message, mock_imapclient
) -> None:
    """Test that a message only in the destination folder is not changed."""
    archive_folder = add_fake_folder(
        db.session, default_account, "Archive", "archive"
    )
    mock_imapclient.add_folder_data(archive_folder.name, {})
    mock_imapclient.capabilities = mock.Mock(return_value=[b"IMAP4rev1"])
    mock_imapclient.move = mock.Mock()
    mock_imapclient.copy = mock.Mock()
    mock_imapclient.delete_messages = mock.Mock()
    add_fake_imapuid(
        db.session, default_account.id, message, archive_folder, 43
    )

    with writable_connection_pool(default_account.id).get() as crispin_client:
        move(
            crispin_client,
            default_account.id,
            message.id,
            {"destination": archive_folder.category.display_name},
        )

    mock_imapclient.move.assert_not_called()
    mock_imapclient.copy.assert_not_called()
    mock_imapclient.delete_messages.assert_not_called()
