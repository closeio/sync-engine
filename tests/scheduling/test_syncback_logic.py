import random
import time

import pytest

from inbox.ignition import engine_manager
from inbox.models.action_log import ActionLog, schedule_action
from inbox.models.session import session_scope, session_scope_by_shard_id
from inbox.transactions.actions import SyncbackService

from tests.util.base import add_generic_imap_account


@pytest.fixture
def purge_accounts_and_actions():
    for key in engine_manager.engines:
        with session_scope_by_shard_id(key) as db_session:
            db_session.query(ActionLog).delete(synchronize_session=False)
            db_session.commit()


@pytest.fixture
def patched_enginemanager(monkeypatch):
    engines = {k: None for k in range(6)}
    monkeypatch.setattr("inbox.ignition.engine_manager.engines", engines)
    yield
    monkeypatch.undo()


@pytest.fixture
def patched_task(monkeypatch):
    def uses_crispin_client(self):
        return False

    def execute_with_lock(self):
        with session_scope(self.account_id) as db_session:
            action_log_entries = db_session.query(ActionLog).filter(
                ActionLog.id.in_(self.action_log_ids)
            )
            for action_log_entry in action_log_entries:
                action_log_entry.status = "successful"
                db_session.commit()

    monkeypatch.setattr(
        "inbox.transactions.actions.SyncbackTask.uses_crispin_client",
        uses_crispin_client,
    )
    monkeypatch.setattr(
        "inbox.transactions.actions.SyncbackTask.execute_with_lock",
        execute_with_lock,
    )
    yield
    monkeypatch.undo()


def schedule_test_action(db_session, account):
    from inbox.models.category import Category

    category_type = "label" if account.provider == "gmail" else "folder"
    category = Category.find_or_create(
        db_session,
        account.namespace.id,
        name=None,
        display_name=f"{account.id}-{random.randint(1, 356)}",
        type_=category_type,
    )
    db_session.flush()

    if category_type == "folder":
        schedule_action(
            "create_folder", category, account.namespace.id, db_session
        )
    else:
        schedule_action(
            "create_label", category, account.namespace.id, db_session
        )
    db_session.commit()


def test_all_keys_are_assigned_exactly_once(patched_enginemanager):
    assigned_keys = []

    service = SyncbackService(
        syncback_id=0, process_number=0, total_processes=2, num_workers=2
    )
    assert service.keys == [0, 2, 4]
    assigned_keys.extend(service.keys)

    service = SyncbackService(
        syncback_id=0, process_number=1, total_processes=2, num_workers=2
    )
    assert service.keys == [1, 3, 5]
    assigned_keys.extend(service.keys)

    # All keys are assigned (therefore all accounts are assigned)
    assert set(engine_manager.engines.keys()) == set(assigned_keys)
    # No key is assigned more than once (and therefore, no account)
    assert len(assigned_keys) == len(set(assigned_keys))


@pytest.mark.skipif(True, reason="Need to investigate")
def test_actions_are_claimed(purge_accounts_and_actions, patched_task):
    with session_scope_by_shard_id(0) as db_session:
        account = add_generic_imap_account(
            db_session, email_address="0@test.com"
        )
        schedule_test_action(db_session, account)

    with session_scope_by_shard_id(1) as db_session:
        account = add_generic_imap_account(
            db_session, email_address="1@test.com"
        )
        schedule_test_action(db_session, account)

    service = SyncbackService(
        syncback_id=0, process_number=1, total_processes=2, num_workers=2
    )
    service._restart_workers()
    service._process_log()

    while not service.task_queue.empty():
        time.sleep(0)

    with session_scope_by_shard_id(0) as db_session:
        q = db_session.query(ActionLog)
        assert q.count() == 1
        assert all(a.status == "pending" for a in q)

    with session_scope_by_shard_id(1) as db_session:
        q = db_session.query(ActionLog)
        assert q.count() == 1
        assert all(a.status != "pending" for a in q)

    service.stop()


@pytest.mark.skipif(True, reason="Need to investigate")
def test_actions_claimed_by_a_single_service(
    purge_accounts_and_actions, patched_task
):
    actionlogs = []
    for key in (0, 1):
        with session_scope_by_shard_id(key) as db_session:
            account = add_generic_imap_account(
                db_session, email_address=f"{key}@test.com"
            )
            schedule_test_action(db_session, account)
            actionlogs += [db_session.query(ActionLog).one().id]

    services = []
    for process_number in (0, 1):
        service = SyncbackService(
            syncback_id=0,
            process_number=process_number,
            total_processes=2,
            num_workers=2,
        )
        service._process_log()
        services.append(service)

    for i, service in enumerate(services):
        assert service.task_queue.qsize() == 1
        assert service.task_queue.peek().action_log_ids == [actionlogs[i]]


@pytest.mark.skipif(True, reason="Test if causing Jenkins build to fail")
def test_actions_for_invalid_accounts_are_skipped(
    purge_accounts_and_actions, patched_task
):
    with session_scope_by_shard_id(0) as db_session:
        account = add_generic_imap_account(
            db_session, email_address="person@test.com"
        )
        schedule_test_action(db_session, account)
        namespace_id = account.namespace.id
        count = (
            db_session.query(ActionLog)
            .filter(ActionLog.namespace_id == namespace_id)
            .count()
        )
        assert account.sync_state != "invalid"

        another_account = add_generic_imap_account(
            db_session, email_address="another@test.com"
        )
        schedule_test_action(db_session, another_account)
        another_namespace_id = another_account.namespace.id
        another_count = (
            db_session.query(ActionLog)
            .filter(ActionLog.namespace_id == another_namespace_id)
            .count()
        )
        assert another_account.sync_state != "invalid"

        account.mark_invalid()
        db_session.commit()

    service = SyncbackService(
        syncback_id=0, process_number=0, total_processes=2, num_workers=2
    )
    service._process_log()

    while not service.task_queue.empty():
        time.sleep(0)

    with session_scope_by_shard_id(0) as db_session:
        q = db_session.query(ActionLog).filter(
            ActionLog.namespace_id == namespace_id,
            ActionLog.status == "pending",
        )
        assert q.count() == count

        q = db_session.query(ActionLog).filter(
            ActionLog.namespace_id == another_namespace_id
        )
        assert q.filter(ActionLog.status == "pending").count() == 0
        assert (
            q.filter(ActionLog.status == "successful").count() == another_count
        )
