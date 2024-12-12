"""
Monitor the action log for changes that should be synced back to the remote
backend.

TODO(emfree):
* Make this more robust across multiple machines. If you started two instances
talking to the same database backend things could go really badly.

"""

import queue
import random
import threading
import weakref
from collections import defaultdict
from datetime import datetime, timedelta

from sqlalchemy import desc

from inbox import interruptible_threading
from inbox.actions.base import (
    can_handle_multiple_records,
    change_labels,
    create_folder,
    create_label,
    delete_draft,
    delete_folder,
    delete_label,
    delete_sent_email,
    mark_starred,
    mark_unread,
    move,
    save_draft,
    save_sent_email,
    update_draft,
    update_folder,
    update_label,
)
from inbox.config import config
from inbox.crispin import writable_connection_pool
from inbox.error_handling import log_uncaught_errors
from inbox.events.actions.base import create_event, delete_event, update_event
from inbox.ignition import engine_manager
from inbox.interruptible_threading import InterruptibleThread
from inbox.logging import get_logger
from inbox.models import ActionLog, Event
from inbox.models.session import session_scope, session_scope_by_shard_id
from inbox.util.concurrency import kill_all, retry_with_logging
from inbox.util.misc import DummyContextManager
from inbox.util.stats import statsd_client

logger = get_logger()

MAIL_ACTION_FUNCTION_MAP = {
    "mark_unread": mark_unread,
    "mark_starred": mark_starred,
    "move": move,
    "change_labels": change_labels,
    "save_draft": save_draft,
    "update_draft": update_draft,
    "delete_draft": delete_draft,
    "save_sent_email": save_sent_email,
    "delete_sent_email": delete_sent_email,
    "create_folder": create_folder,
    "create_label": create_label,
    "update_folder": update_folder,
    "delete_folder": delete_folder,
    "update_label": update_label,
    "delete_label": delete_label,
}

EVENT_ACTION_FUNCTION_MAP = {
    "create_event": create_event,
    "delete_event": delete_event,
    "update_event": update_event,
}


def action_uses_crispin_client(action) -> bool:
    return action in MAIL_ACTION_FUNCTION_MAP


def function_for_action(action):  # noqa: ANN201
    if action in MAIL_ACTION_FUNCTION_MAP:
        return MAIL_ACTION_FUNCTION_MAP[action]
    return EVENT_ACTION_FUNCTION_MAP[action]


ACTION_MAX_NR_OF_RETRIES = 5
NUM_PARALLEL_ACCOUNTS = 500
INVALID_ACCOUNT_GRACE_PERIOD = 60 * 60 * 2  # 2 hours

# Max amount of actionlog entries to fetch for specific records to
# deduplicate.
MAX_DEDUPLICATION_BATCH_SIZE = 5000


class SyncbackService(InterruptibleThread):
    """Asynchronously consumes the action log and executes syncback actions."""

    def __init__(
        self,
        syncback_id,
        process_number,
        total_processes,
        poll_interval: int = 1,
        retry_interval: int = 120,
        num_workers=NUM_PARALLEL_ACCOUNTS,
        batch_size: int = 20,
        fetch_batch_size: int = 100,
    ) -> None:
        self.process_number = process_number
        self.total_processes = total_processes
        self.poll_interval = poll_interval
        self.retry_interval = retry_interval

        # Amount of log entries to fetch before merging/de-duplication to
        # determine which records need to be processed.
        self.fetch_batch_size = fetch_batch_size

        # Amount of log entries to process in a batch.
        self.batch_size = batch_size

        self.keep_running = True
        self.workers = []
        # Dictionary account_id -> semaphore to serialize action syncback for
        # any particular account.
        # TODO(emfree): We really only need to serialize actions that operate
        # on any given object. But IMAP actions are already effectively
        # serialized by using an IMAP connection pool of size 1, so it doesn't
        # matter too much.
        self.account_semaphores: defaultdict[
            int, threading.BoundedSemaphore
        ] = defaultdict(lambda: threading.BoundedSemaphore(1))
        # This SyncbackService performs syncback for only and all the accounts
        # on shards it is reponsible for; shards are divided up between
        # running SyncbackServices.
        self.log = logger.new(component="syncback")
        syncback_assignments = {
            int(k): v
            for k, v in config.get("SYNCBACK_ASSIGNMENTS", {}).items()
        }
        if syncback_id in syncback_assignments:
            self.keys = [
                key
                for key in engine_manager.engines
                if key in syncback_assignments[syncback_id]
                and key % total_processes == process_number
            ]
        else:
            self.log.warning(
                "No shards assigned to syncback server",
                syncback_id=syncback_id,
            )
            self.keys = []

        self.log = logger.new(component="syncback")
        self.num_workers = num_workers
        self.num_idle_workers = 0
        self.worker_did_finish = threading.Event()
        self.worker_did_finish.clear()
        self.task_queue = queue.Queue()
        self.running_action_ids = set()
        super().__init__()

    def _has_recent_move_action(self, db_session, log_entries) -> bool:
        """
        Determines if we recently completed a move action. Since Nylas doesn't
        update local UID state after completing an action, we space
        non-optimistic actions apart so the sync process can catch up.
        """  # noqa: D401
        if not log_entries:
            return False

        log_entry = log_entries[0]
        action_log_ids = [entry.id for entry in log_entries]
        # Check if there was a pending move action that recently completed.
        threshold = datetime.utcnow() - timedelta(seconds=90)
        actionlog = (
            db_session.query(ActionLog)
            .filter(
                ActionLog.namespace_id == log_entry.namespace.id,
                ActionLog.table_name == log_entry.table_name,
                ActionLog.record_id == log_entry.record_id,
                ActionLog.action.in_(["change_labels", "move"]),
                ActionLog.status == "successful",
                ActionLog.updated_at >= threshold,
            )
            .order_by(desc(ActionLog.id))
            .first()
        )

        if actionlog:
            account_id = log_entries[0].namespace.account.id
            self.log.debug(
                "Temporarily skipping actions",
                account_id=account_id,
                table_name=log_entry.table_name,
                record_id=log_entry.record_id,
                action_log_ids=action_log_ids,
                action=log_entry.action,
                other_action_id=actionlog.id,
                other_action_updated_at=actionlog.updated_at.isoformat(),
            )
            return True
        else:
            return False

    def _tasks_for_log_entries(self, db_session, log_entries, has_more):
        """
        Return SyncbackTask for similar actions (same action & record).
        """
        if not log_entries:
            return []

        namespace = log_entries[0].namespace
        account_id = namespace.account.id
        semaphore = self.account_semaphores[account_id]

        actions = {entry.action for entry in log_entries}
        assert len(actions) == 1
        action = actions.pop()

        # XXX: Don't do this for change_labels because we use optimistic
        # updates.
        if action == "move" and self._has_recent_move_action(
            db_session, log_entries
        ):
            return []

        if has_more and action in ("move", "mark_unread", "change_labels"):
            # There may be more records to deduplicate.
            self.log.debug(
                "fetching more entries",
                account_id=account_id,
                action=action,
                record_id=log_entries[0].record_id,
            )
            log_entries = (
                db_session.query(ActionLog)
                .filter(
                    ActionLog.discriminator == "actionlog",
                    ActionLog.status == "pending",
                    ActionLog.namespace_id == namespace.id,
                    ActionLog.action == action,
                    ActionLog.record_id == log_entries[0].record_id,
                )
                .order_by(ActionLog.id)
                .limit(MAX_DEDUPLICATION_BATCH_SIZE)
                .all()
            )

        record_ids = [entry.record_id for entry in log_entries]

        log_entry_ids = [entry.id for entry in log_entries]

        if action in ("move", "mark_unread"):
            extra_args = log_entries[-1].extra_args
        elif action == "change_labels":
            added_labels: set[str] = set()
            removed_labels: set[str] = set()
            for log_entry in log_entries:
                for label in log_entry.extra_args["added_labels"]:
                    if label in removed_labels:
                        removed_labels.remove(label)
                    else:
                        added_labels.add(label)
                for label in log_entry.extra_args["removed_labels"]:
                    if label in added_labels:
                        added_labels.remove(label)
                    else:
                        removed_labels.add(label)
            extra_args = {
                "added_labels": list(added_labels),
                "removed_labels": list(removed_labels),
            }
        else:
            # Can't merge
            tasks = [
                SyncbackTask(
                    action_name=log_entry.action,
                    semaphore=semaphore,
                    action_log_ids=[log_entry.id],
                    record_ids=[log_entry.record_id],
                    account_id=account_id,
                    provider=namespace.account.verbose_provider,
                    service=self,
                    retry_interval=self.retry_interval,
                    extra_args=log_entry.extra_args,
                )
                for log_entry in log_entries
            ]
            return tasks

        task = SyncbackTask(
            action_name=action,
            semaphore=semaphore,
            action_log_ids=log_entry_ids,
            record_ids=record_ids,
            account_id=account_id,
            provider=namespace.account.verbose_provider,
            service=self,
            retry_interval=self.retry_interval,
            extra_args=extra_args,
        )
        return [task]

    def _get_batch_task(self, db_session, log_entries, has_more):
        """
        Helper for _batch_log_entries that returns the batch task for the given
        valid log entries.
        """  # noqa: D401
        if not log_entries:
            return None
        namespace = log_entries[0].namespace
        account_id = namespace.account.id
        semaphore = self.account_semaphores[account_id]
        grouper = defaultdict(list)  # Similar actions
        group_keys = []  # Used for ordering

        for log_entry in log_entries:
            group_key = (
                log_entry.namespace.id,
                log_entry.table_name,
                log_entry.record_id,
                log_entry.action,
            )
            if group_key not in grouper:
                group_keys.append(group_key)
            grouper[group_key].append(log_entry)

        tasks = []
        for group_key in group_keys:
            group_log_entries = grouper[group_key]
            group_tasks = self._tasks_for_log_entries(
                db_session, group_log_entries, has_more
            )
            tasks += group_tasks
            if len(tasks) > self.batch_size:
                break
        if tasks:
            return SyncbackBatchTask(
                semaphore, tasks[: self.batch_size], account_id
            )
        return None

    def _batch_log_entries(self, db_session, log_entries):
        """
        Batch action log entries together and return a batch task after
        verifying we can process them. All actions must belong to the same
        account.
        """
        valid_log_entries = []
        account_id: int | None = None

        has_more = len(log_entries) == self.fetch_batch_size

        for log_entry in log_entries:
            if log_entry is None:
                self.log.error("Got no action, skipping")
                continue

            if log_entry.id in self.running_action_ids:
                self.log.debug(
                    "Skipping already running action",
                    action_log_id=log_entry.id,
                )
                # We're already running an action for this account, so don't
                # queue up any additional actions for this account until the
                # previous batch has finished.
                return None

            namespace = log_entry.namespace
            if account_id is None:
                account_id = namespace.account.id
            else:
                assert (
                    account_id == namespace.account.id
                ), "account_id and namespace.account.id do not match"

            if namespace.account.sync_state in ("invalid", "stopped"):
                sync_state = namespace.account.sync_state
                self.log.warning(
                    f"Skipping action for {sync_state} account",
                    account_id=account_id,
                    action_log_id=log_entry.id,
                    action=log_entry.action,
                )

                action_age = (
                    datetime.utcnow() - log_entry.created_at
                ).total_seconds()

                if action_age > INVALID_ACCOUNT_GRACE_PERIOD:
                    log_entry.status = "failed"
                    db_session.commit()
                    self.log.warning(
                        "Marking action as failed for {} account, older than grace period".format(  # noqa: G001
                            sync_state
                        ),
                        account_id=account_id,
                        action_log_id=log_entry.id,
                        action=log_entry.action,
                    )
                    statsd_client.incr(f"syncback.{sync_state}_failed.total")
                    statsd_client.incr(
                        f"syncback.{sync_state}_failed.{account_id}"
                    )
                continue

            # If there is a recently failed action, don't execute any actions
            # for this account.
            if log_entry.retries > 0:
                action_updated_age = (
                    datetime.utcnow() - log_entry.updated_at
                ).total_seconds()

                # TODO(T6974): We might want to do some kind of exponential
                # backoff with jitter to avoid the thundering herd problem if a
                # provider suddenly starts having issues for a short period of
                # time.
                if action_updated_age < self.retry_interval:
                    self.log.info(
                        "Skipping tasks due to recently failed action",
                        account_id=account_id,
                        action_log_id=log_entry.id,
                        retries=log_entry.retries,
                    )
                    return None

            valid_log_entries.append(log_entry)

        batch_task = self._get_batch_task(
            db_session, valid_log_entries, has_more
        )
        if not batch_task:
            return None
        for task in batch_task.tasks:
            self.running_action_ids.update(task.action_log_ids)
            self.log.debug(
                "Syncback added task",
                process=self.process_number,
                account_id=account_id,
                action_log_ids=task.action_log_ids,
                num_actions=len(task.action_log_ids),
                msg=task.action_name,
                task_count=self.task_queue.qsize(),
                extra_args=task.extra_args,
            )
        return batch_task

    def _process_log(self) -> None:
        for key in self.keys:
            with session_scope_by_shard_id(key) as db_session:
                # Get the list of namespace ids with pending actions
                namespace_ids = [
                    ns_id[0]
                    for ns_id in db_session.query(ActionLog.namespace_id)
                    .filter(
                        ActionLog.discriminator == "actionlog",
                        ActionLog.status == "pending",
                    )
                    .distinct()
                ]

                # Pick NUM_PARALLEL_ACCOUNTS randomly to make sure we're
                # executing actions equally for each namespace_id --- we
                # don't want a single account with 100k actions hogging
                # the action log.
                namespaces_to_process = []
                if len(namespace_ids) <= NUM_PARALLEL_ACCOUNTS:
                    namespaces_to_process = namespace_ids
                else:
                    namespaces_to_process = random.sample(
                        namespace_ids, NUM_PARALLEL_ACCOUNTS
                    )
                for ns_id in namespaces_to_process:
                    # The discriminator filter restricts actions to IMAP. EAS
                    # uses a different system.
                    query = (
                        db_session.query(ActionLog)
                        .filter(
                            ActionLog.discriminator == "actionlog",
                            ActionLog.status == "pending",
                            ActionLog.namespace_id == ns_id,
                        )
                        .order_by(ActionLog.id)
                        .limit(self.fetch_batch_size)
                    )
                    task = self._batch_log_entries(db_session, query.all())
                    if task is not None:
                        self.task_queue.put(task)

    def _restart_workers(self) -> None:
        while len(self.workers) < self.num_workers:
            worker = SyncbackWorker(self)
            self.workers.append(worker)
            self.num_idle_workers += 1
            worker.start()

    def _run_impl(self) -> None:
        self._restart_workers()
        self._process_log()
        # Wait for a worker to finish or for the fixed poll_interval,
        # whichever happens first.
        timeout = self.poll_interval
        if self.num_idle_workers == 0:
            timeout = None
        self.worker_did_finish.clear()
        self.worker_did_finish.wait(timeout=timeout)

    def stop(self) -> None:
        self.keep_running = False
        kill_all(self.workers)

    def _run(self) -> None:
        self.log.info(
            "Starting syncback service",
            process_num=self.process_number,
            total_processes=self.total_processes,
            keys=self.keys,
        )
        while self.keep_running:
            interruptible_threading.check_interrupted()
            retry_with_logging(self._run_impl, self.log)

    def notify_worker_active(self) -> None:
        self.num_idle_workers -= 1

    def notify_worker_finished(self, action_ids) -> None:
        self.num_idle_workers += 1
        self.worker_did_finish.set()
        for action_id in action_ids:
            self.running_action_ids.remove(action_id)

    def __del__(self) -> None:
        if self.keep_running:
            self.stop()


class SyncbackBatchTask:
    def __init__(self, semaphore, tasks, account_id) -> None:
        self.semaphore = semaphore
        self.tasks = tasks
        self.account_id = account_id

    def _crispin_client_or_none(self):
        if self.uses_crispin_client():
            return writable_connection_pool(self.account_id).get()
        else:
            return DummyContextManager()

    def execute(self) -> None:
        log = logger.new()
        with self.semaphore, self._crispin_client_or_none() as crispin_client:
            log.debug(
                "Syncback running batch of actions",
                num_actions=len(self.tasks),
                account_id=self.account_id,
            )
            for task in self.tasks:
                interruptible_threading.check_interrupted()
                task.crispin_client = crispin_client
                if not task.execute_with_lock():
                    log.info(
                        "Pausing syncback tasks due to error",
                        account_id=self.account_id,
                    )
                    # Stop executing further actions for an account if any
                    # failed.
                    break

    def uses_crispin_client(self):  # noqa: ANN201
        return any([task.uses_crispin_client() for task in self.tasks])

    def timeout(self, per_task_timeout):  # noqa: ANN201
        return len(self.tasks) * per_task_timeout

    @property
    def action_log_ids(self):  # noqa: ANN201
        return [entry for task in self.tasks for entry in task.action_log_ids]


class SyncbackTask:
    """
    Task responsible for executing a single syncback action. We can retry the
    action up to ACTION_MAX_NR_OF_RETRIES times before we mark it as failed.
    Note: Each task holds an account-level lock, in order to ensure that
    actions are executed in the order they were first scheduled. This means
    that in the worst case, a misbehaving action can block other actions for
    the account from executing, for up to about
    retry_interval * ACTION_MAX_NR_OF_RETRIES = 600 seconds

    TODO(emfree): Fix this with more granular locking (or a better strategy
    altogether). We only really need ordering guarantees for actions on any
    given object, not on the whole account.

    """

    def __init__(
        self,
        action_name,
        semaphore,
        action_log_ids,
        record_ids,
        account_id,
        provider,
        service,
        retry_interval: int = 30,
        extra_args=None,
    ) -> None:
        self.parent_service = weakref.ref(service)
        self.action_name = action_name
        self.semaphore = semaphore
        self.func = function_for_action(action_name)
        self.action_log_ids = list(action_log_ids)
        self.record_ids = record_ids
        self.account_id = account_id
        self.provider = provider
        self.extra_args = extra_args
        self.retry_interval = retry_interval
        self.crispin_client = None

    def try_merge_with(self, other):  # noqa: ANN201
        if self.func != other.func:
            return None

        if self.action_name == "change_labels":
            my_removed_labels = set(self.extra_args["removed_labels"])
            other_removed_labels = set(other.extra_args["removed_labels"])
            if my_removed_labels != other_removed_labels:
                return None

            my_added_labels = set(self.extra_args["added_labels"])
            other_added_labels = set(other.extra_args["added_labels"])
            if my_added_labels != other_added_labels:
                return None

            # If anything seems fishy, conservatively return None.
            if (
                self.provider != other.provider
                or self.action_log_ids == other.action_log_ids
                or self.record_ids == other.record_ids
                or self.account_id != other.account_id
                or self.action_name != other.action_name
            ):
                return None
            return SyncbackTask(
                self.action_name,
                self.semaphore,
                self.action_log_ids + other.action_log_ids,
                self.record_ids + other.record_ids,
                self.account_id,
                self.provider,
                self.parent_service(),
                self.retry_interval,
                self.extra_args,
            )
        return None

    def _log_to_statsd(self, action_log_status, latency=None) -> None:
        metric_names = [
            f"syncback.overall.{action_log_status}",
            f"syncback.providers.{self.provider}.{action_log_status}",
        ]

        for metric in metric_names:
            statsd_client.incr(metric)
            if latency:
                statsd_client.timing(metric, latency * 1000)

    def execute_with_lock(self) -> bool | None:
        """
        Process a task and return whether it executed successfully.
        """
        interruptible_threading.check_interrupted()

        self.log = logger.new(
            record_ids=list(set(self.record_ids)),
            action_log_ids=self.action_log_ids[:100],
            n_action_log_ids=len(self.action_log_ids),
            action=self.action_name,
            account_id=self.account_id,
            extra_args=self.extra_args,
        )

        # Double-check that the action is still pending.
        # Although the task queue is populated based on pending actions, it's
        # possible that the processing of one action involved marking other
        # actions as failed.
        (records_to_process, action_ids_to_process) = (
            self._get_records_and_actions_to_process()
        )
        if len(action_ids_to_process) == 0:
            return True

        try:
            before, after = self._execute_timed_action(records_to_process)
            self.log.debug(
                "executing action", action_log_ids=action_ids_to_process
            )

            with session_scope(self.account_id) as db_session:
                action_log_entries = db_session.query(ActionLog).filter(
                    ActionLog.id.in_(action_ids_to_process)
                )

                max_latency = max_func_latency = 0
                for action_log_entry in action_log_entries:
                    latency, func_latency = self._mark_action_as_successful(
                        action_log_entry, before, after, db_session
                    )
                    max_latency = max(latency, max_latency)
                    max_func_latency = max(func_latency, max_func_latency)
                parent_service = self.parent_service()
                assert parent_service
                self.log.info(
                    "syncback action completed",
                    latency=max_latency,
                    process=parent_service.process_number,
                    func_latency=max_func_latency,
                )
                return True
        except Exception:
            log_uncaught_errors(
                self.log, account_id=self.account_id, provider=self.provider
            )
            with session_scope(self.account_id) as db_session:
                action_log_entries = db_session.query(ActionLog).filter(
                    ActionLog.id.in_(action_ids_to_process)
                )

                marked_as_failed = False
                for action_log_entry in action_log_entries:
                    action_log_entry.retries += 1
                    if action_log_entry.retries == ACTION_MAX_NR_OF_RETRIES:
                        marked_as_failed = True

                if marked_as_failed:
                    self.log.debug(
                        "marking actions as failed",
                        action_log_ids=action_ids_to_process,
                    )
                    # If we merged actions, fail them all at the same time.
                    for action_log_entry in action_log_entries:
                        self._mark_action_as_failed(
                            action_log_entry, db_session
                        )
                db_session.commit()

                return False

    def _get_records_and_actions_to_process(self):
        records_to_process = []
        action_ids_to_process = []
        action_log_record_map = dict(zip(self.action_log_ids, self.record_ids))
        with session_scope(self.account_id) as db_session:
            action_log_entries = db_session.query(ActionLog).filter(
                ActionLog.id.in_(self.action_log_ids)
            )
            for action_log_entry in action_log_entries:
                if action_log_entry.status != "pending":
                    self.log.info(
                        "Skipping SyncbackTask, action is no longer pending"
                    )
                    continue
                action_ids_to_process.append(action_log_entry.id)
                records_to_process.append(
                    action_log_record_map[action_log_entry.id]
                )
        return records_to_process, action_ids_to_process

    def _execute_timed_action(self, records_to_process):
        before_func = datetime.utcnow()
        func_args = [self.account_id]
        if can_handle_multiple_records(self.action_name):
            func_args.append(records_to_process)
        else:
            assert len(set(records_to_process)) == 1
            func_args.append(records_to_process[0])

        if self.extra_args:
            func_args.append(self.extra_args)
        if self.uses_crispin_client():
            assert self.crispin_client is not None
            func_args.insert(0, self.crispin_client)
        self.func(*func_args)
        after_func = datetime.utcnow()
        return before_func, after_func

    def _mark_action_as_successful(
        self, action_log_entry, before, after, db_session
    ):
        action_log_entry.status = "successful"
        db_session.commit()
        latency = round(
            (datetime.utcnow() - action_log_entry.created_at).total_seconds(),
            2,
        )
        func_latency = round((after - before).total_seconds(), 2)
        self._log_to_statsd(action_log_entry.status, latency)
        return (latency, func_latency)

    def _mark_action_as_failed(self, action_log_entry, db_session) -> None:
        self.log.critical("Max retries reached, giving up.", exc_info=True)
        action_log_entry.status = "failed"
        self._log_to_statsd(action_log_entry.status)

        if action_log_entry.action == "create_event":
            # Creating a remote copy of the event failed.
            # Without it, none of the other pending actions
            # for this event will succeed. To prevent their
            # execution, preemptively mark them as failed.
            actions = (
                db_session.query(ActionLog)
                .filter_by(
                    record_id=action_log_entry.record_id,
                    namespace_id=action_log_entry.namespace_id,
                    status="pending",
                )
                .all()
            )
            for pending_action in actions:
                pending_action.status = "failed"

            # Mark the local copy as deleted so future actions can't be made.
            event = db_session.query(Event).get(action_log_entry.record_id)
            event.deleted_at = datetime.now()
        db_session.commit()

    def uses_crispin_client(self):  # noqa: ANN201
        return action_uses_crispin_client(self.action_name)

    def timeout(self, per_task_timeout):  # noqa: ANN201
        return per_task_timeout

    def execute(self) -> None:
        with self.semaphore:
            self.execute_with_lock()


class SyncbackWorker(InterruptibleThread):
    def __init__(self, parent_service, task_timeout: int = 60) -> None:
        self.parent_service = weakref.ref(parent_service)
        self.task_timeout = task_timeout
        self.log = logger.new(component="syncback-worker")
        super().__init__()

    def _run(self) -> None:
        while self.parent_service().keep_running:
            task = interruptible_threading.queue_get(
                self.parent_service().task_queue
            )

            try:
                self.parent_service().notify_worker_active()
                with interruptible_threading.timeout(
                    task.timeout(self.task_timeout)
                ):
                    task.execute()
            except Exception:
                self.log.error(  # noqa: G201
                    "SyncbackWorker caught exception",
                    exc_info=True,
                    account_id=task.account_id,
                )
            finally:
                self.parent_service().notify_worker_finished(
                    task.action_log_ids
                )
