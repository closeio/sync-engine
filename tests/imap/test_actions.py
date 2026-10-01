import time
from datetime import datetime, timedelta
from unittest import mock

import pytest
from flanker import mime
from imapclient.exceptions import LoginError

from inbox import interruptible_threading
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
from inbox.crispin import writable_connection_pool
from inbox.interruptible_threading import InterruptibleThreadTimeout
from inbox.models import ActionLog, Category
from inbox.models.action_log import schedule_action
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


def test_change_labels(
    db, default_account, message, folder, mock_imapclient
) -> None:
    mock_imapclient.add_folder_data(folder.name, {})
    mock_imapclient.add_gmail_labels = mock.Mock()
    mock_imapclient.remove_gmail_labels = mock.Mock()
    add_fake_imapuid(db.session, default_account.id, message, folder, 22)

    with writable_connection_pool(default_account.id).get() as crispin_client:
        change_labels(
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
    syncback_logger.new.return_value.exception.assert_called_once_with(
        "Syncback connection timed out", account_id=default_account.id
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
    def function_for_action(name):
        def func(*args):
            raise InterruptibleThreadTimeout()

        return func

    monkeypatch.setattr(
        "inbox.transactions.actions.function_for_action", function_for_action
    )
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
    syncback_logger.new.return_value.exception.assert_called_once_with(
        "Syncback action timed out",
        account_id=default_account.id,
        provider=default_account.verbose_provider,
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
