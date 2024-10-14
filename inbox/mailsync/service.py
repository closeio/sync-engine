import platform
import random
import time
from threading import BoundedSemaphore
from typing import Type

from sqlalchemy import and_, or_
from sqlalchemy.exc import OperationalError

from inbox.config import config
from inbox.contacts.remote_sync import ContactSync
from inbox.error_handling import log_uncaught_errors
from inbox.events.abstract import AbstractEventsProvider
from inbox.events.google import GoogleEventsProvider
from inbox.events.microsoft.events_provider import MicrosoftEventsProvider
from inbox.events.remote_sync import EventSync, WebhookEventSync
from inbox.heartbeat.status import clear_heartbeat_status
from inbox.logging import get_logger
from inbox.mailsync.backends import module_registry
from inbox.models import Account
from inbox.models.session import global_session_scope, session_scope
from inbox.providers import providers
from inbox.scheduling.event_queue import EventQueue, EventQueueGroup
from inbox.util.concurrency import kill_all, retry_with_logging
from inbox.util.stats import statsd_client

USE_WEBHOOKS = "GOOGLE_PUSH_NOTIFICATIONS" in config.get(
    "FEATURE_FLAGS", []
) or "WEBHOOKS" in config.get("FEATURE_FLAGS", [])

# How much time (in minutes) should all CPUs be over 90% to consider them
# overloaded.
SYNC_POLL_INTERVAL = 20
PENDING_AVGS_THRESHOLD = 10

MAX_ACCOUNTS_PER_PROCESS = config.get("MAX_ACCOUNTS_PER_PROCESS", 150)

SYNC_EVENT_QUEUE_NAME = "sync:event_queue:{}"
SHARED_SYNC_EVENT_QUEUE_NAME = "sync:shared_event_queue:{}"

SHARED_SYNC_EVENT_QUEUE_ZONE_MAP = {}


def shared_sync_event_queue_for_zone(zone):
    queue_name = SHARED_SYNC_EVENT_QUEUE_NAME.format(zone)
    if queue_name not in SHARED_SYNC_EVENT_QUEUE_ZONE_MAP:
        SHARED_SYNC_EVENT_QUEUE_ZONE_MAP[queue_name] = EventQueue(queue_name)
    return SHARED_SYNC_EVENT_QUEUE_ZONE_MAP[queue_name]


class SyncService:
    """
    Parameters
    ----------
    process_identifier: string
        Unique identifying string for this process (currently
        <hostname>:<process_number>)
    process_number: int
        If a system is launching 16 sync processes, value from 0-15. (Each
        sync service on the system should get a different value.)
    poll_interval : int
        Serves as the max timeout for the redis blocking pop.
    """

    def __init__(
        self, process_identifier, process_number, poll_interval=SYNC_POLL_INTERVAL
    ):
        self.keep_running = True
        self.host = platform.node()
        self.process_number = process_number
        self.process_identifier = process_identifier
        self.monitor_cls_for = {
            mod.PROVIDER: getattr(mod, mod.SYNC_MONITOR_CLS)
            for mod in module_registry.values()
            if hasattr(mod, "SYNC_MONITOR_CLS")
        }

        for p_name, _ in providers.items():
            if p_name not in self.monitor_cls_for:
                self.monitor_cls_for[p_name] = self.monitor_cls_for["generic"]

        self.log = get_logger()
        self.log.bind(process_number=process_number)
        self.log.info(
            "starting mail sync process", supported_providers=list(module_registry)
        )

        self.syncing_accounts = set()
        self.email_sync_monitors = {}
        self.contact_sync_monitors = {}
        self.event_sync_monitors = {}
        # Randomize the poll_interval so we maintain at least a little fairness
        # when using a timeout while blocking on the redis queues.
        min_poll_interval = 5
        self.poll_interval = int(
            (random.random() * (poll_interval - min_poll_interval)) + min_poll_interval
        )
        self.semaphore = BoundedSemaphore(1)
        self.zone = config.get("ZONE")

        # Note that we don't partition by zone for the private queues.
        # There's not really a reason to since there's one queue per machine
        # anyways. Also, if you really want to send an Account to a mailsync
        # machine in another zone you can do so.
        self.private_queue = EventQueue(
            SYNC_EVENT_QUEUE_NAME.format(self.process_identifier)
        )
        self.queue_group = EventQueueGroup(
            [shared_sync_event_queue_for_zone(self.zone), self.private_queue]
        )

        self.stealing_enabled = config.get("SYNC_STEAL_ACCOUNTS", True)
        self._pending_avgs_provider = None
        self.last_unloaded_account = time.time()

    def run(self):
        while self.keep_running:
            retry_with_logging(self._run_impl, self.log)

        kill_all(self.contact_sync_monitors.values())
        self.log.info(
            "stopped contact sync monitors", count=len(self.contact_sync_monitors)
        )
        kill_all(self.event_sync_monitors.values())
        self.log.info(
            "stopped event sync monitors", count=len(self.event_sync_monitors)
        )

        for email_sync_monitor in self.email_sync_monitors.values():
            if email_sync_monitor.delete_handler:
                email_sync_monitor.delete_handler.kill()
            kill_all(email_sync_monitor.folder_monitors, block=False)
            email_sync_monitor.sync_greenlet.kill(block=False)
            email_sync_monitor.join()
        self.log.info(
            "stopped email sync monitors", count=len(self.email_sync_monitors)
        )

    def _run_impl(self):
        """
        Waits for notifications about Account migrations and checks for start/stop commands.

        """
        # When the service first starts we should check the state of the world.
        self.poll()
        event = None
        while self.keep_running and event is None:
            event = self.queue_group.receive_event(timeout=self.poll_interval)

        if not event:
            return

        if (
            shared_sync_event_queue_for_zone(self.zone).queue_name
            == event["queue_name"]
        ):
            self.handle_shared_queue_event(event)
            return

        # We're going to re-evaluate the world so we don't need any of the
        # other pending events in our private queue.
        self._flush_private_queue()
        self.poll()

    def _flush_private_queue(self):
        while True:
            event = self.private_queue.receive_event(timeout=None)
            if event is None:
                break

    def handle_shared_queue_event(self, event):
        # Conservatively, stop accepting accounts if the process pending averages
        # is over PENDING_AVGS_THRESHOLD or if the total of accounts being
        # synced by a single process exceeds the threshold. Excessive
        # concurrency per process can result in lowered database throughput
        # or availability problems, since many transactions may be held open
        # at the same time.
        pending_avgs_over_threshold = False
        if self._pending_avgs_provider is not None:
            pending_avgs = self._pending_avgs_provider.get_pending_avgs()
            pending_avgs_over_threshold = pending_avgs[15] >= PENDING_AVGS_THRESHOLD

        if (
            self.stealing_enabled
            and not pending_avgs_over_threshold
            and len(self.syncing_accounts) < MAX_ACCOUNTS_PER_PROCESS
        ):
            account_id = event["id"]
            if self.start_sync(account_id):
                self.log.info(
                    "Claimed new unassigned account sync", account_id=account_id
                )
            return

        if not self.stealing_enabled:
            reason = "stealing disabled"
        elif pending_avgs_over_threshold:
            reason = "process pending avgs too high"
        else:
            reason = "reached max accounts for process"
        self.log.info(
            "Not claiming new account sync, sending event back to shared queue",
            reason=reason,
        )
        shared_sync_event_queue_for_zone(self.zone).send_event(event)

    def poll(self):
        # Determine which accounts to sync
        start_accounts = self.account_ids_to_sync()
        statsd_client.gauge(
            f"mailsync.account_counts.{self.host}.mailsync-{self.process_number}.count",
            len(start_accounts),
        )

        # Perform the appropriate action on each account
        for account_id in start_accounts:
            if account_id not in self.syncing_accounts:
                try:
                    self.start_sync(account_id)
                except OperationalError:
                    self.log.error(
                        "Database error starting account sync", exc_info=True
                    )
                    log_uncaught_errors()

        stop_accounts = self.account_ids_owned() - set(start_accounts)
        for account_id in stop_accounts:
            self.log.info("sync service stopping sync", account_id=account_id)
            try:
                self.stop_sync(account_id)
            except OperationalError:
                self.log.error("Database error stopping account sync", exc_info=True)
                log_uncaught_errors()

    def account_ids_to_sync(self):
        with global_session_scope() as db_session:
            return {
                r[0]
                for r in db_session.query(Account.id)
                .filter(
                    Account.sync_should_run,
                    or_(
                        and_(
                            Account.desired_sync_host == self.process_identifier,
                            Account.sync_host.is_(None),
                        ),
                        and_(
                            Account.desired_sync_host.is_(None),
                            Account.sync_host == self.process_identifier,
                        ),
                        and_(
                            Account.desired_sync_host == self.process_identifier,
                            Account.sync_host == self.process_identifier,
                        ),
                    ),
                )
                .all()
            }

    def account_ids_owned(self):
        with global_session_scope() as db_session:
            return {
                r[0]
                for r in db_session.query(Account.id)
                .filter(Account.sync_host == self.process_identifier)
                .all()
            }

    def register_pending_avgs_provider(self, pending_avgs_provider):
        self._pending_avgs_provider = pending_avgs_provider

    def start_event_sync(self, account: Account) -> None:
        provider_class: Type[AbstractEventsProvider]
        if account.provider == "gmail":
            provider_class = GoogleEventsProvider
        elif account.provider == "microsoft":
            provider_class = MicrosoftEventsProvider
        else:
            raise AssertionError(
                "Events can be only synced for gmail and microsoft accounts"
            )

        sync_class = WebhookEventSync if USE_WEBHOOKS else EventSync
        event_sync = sync_class(
            account.email_address,
            account.verbose_provider,
            account.id,
            account.namespace.id,
            provider_class=provider_class,
        )
        self.log.info(
            "starting event sync",
            account_id=account.id,
            provider_class=provider_class.__name__,
            sync_class=sync_class.__name__,
        )

        self.event_sync_monitors[account.id] = event_sync
        event_sync.start()

    def start_sync(self, account_id: int) -> bool:
        """
        Starts a sync for the account with the given account_id.
        If that account doesn't exist, does nothing.

        """
        with self.semaphore, session_scope(account_id) as db_session:
            account = db_session.query(Account).with_for_update().get(account_id)
            if account is None:
                self.log.error("no such account", account_id=account_id)
                return False
            if not account.sync_should_run:
                return False
            if (
                account.desired_sync_host is not None
                and account.desired_sync_host != self.process_identifier
            ):
                return False
            if (
                account.sync_host is not None
                and account.sync_host != self.process_identifier
            ):
                return False
            self.log.info(
                "starting sync",
                account_id=account.id,
                email_address=account.email_address,
            )

            if account.id in self.syncing_accounts:
                self.log.info("sync already started", account_id=account_id)
                return False

            try:
                account.sync_host = self.process_identifier
                if account.sync_email:
                    monitor = self.monitor_cls_for[account.provider](account)
                    self.email_sync_monitors[account.id] = monitor
                    monitor.start()

                info = account.provider_info
                if info.get("contacts", None) and account.sync_contacts:
                    contact_sync = ContactSync(
                        account.email_address,
                        account.verbose_provider,
                        account.id,
                        account.namespace.id,
                    )
                    self.contact_sync_monitors[account.id] = contact_sync
                    contact_sync.start()

                if info.get("events", None) and account.sync_events:
                    self.start_event_sync(account)

                account.sync_started()
                self.syncing_accounts.add(account.id)
                # TODO (mark): Uncomment this after we've transitioned to from statsd to brubeck
                # statsd_client.gauge('mailsync.sync_hosts_counts.{}'.format(acc.id), 1, delta=True)
                db_session.commit()
                self.log.info(
                    "Sync started", account_id=account_id, sync_host=account.sync_host
                )
            except Exception:
                self.log.error(
                    "Error starting sync", exc_info=True, account_id=account_id
                )
                return False
        return True

    def stop(self) -> None:
        self.log.info("stopping sync process")
        self.keep_running = False

    def stop_sync(self, account_id):
        """
        Stops the sync for the account with given account_id.
        If that account doesn't exist, does nothing.

        """
        with self.semaphore:
            self.log.info("Stopping monitors", account_id=account_id)
            if account_id in self.email_sync_monitors:
                email_sync_monitor = self.email_sync_monitors[account_id]
                if email_sync_monitor.delete_handler:
                    email_sync_monitor.delete_handler.kill()
                email_sync_monitor.sync_greenlet.kill(block=False)
                email_sync_monitor.join()
                del self.email_sync_monitors[account_id]

            # Stop contacts sync if necessary
            if account_id in self.contact_sync_monitors:
                self.contact_sync_monitors[account_id].kill()
                del self.contact_sync_monitors[account_id]

            # Stop events sync if necessary
            if account_id in self.event_sync_monitors:
                self.event_sync_monitors[account_id].kill()
                del self.event_sync_monitors[account_id]

            # Update database/heartbeat state
            with session_scope(account_id) as db_session:
                acc = db_session.query(Account).get(account_id)
                if not acc.sync_should_run:
                    clear_heartbeat_status(acc.id)
                if not acc.sync_stopped(self.process_identifier):
                    self.syncing_accounts.discard(account_id)
                    return False
                self.log.info("sync stopped", account_id=account_id)
                # TODO (mark): Uncomment this after we've transitioned to from statsd to brubeck
                # statsd_client.gauge('mailsync.sync_hosts_counts.{}'.format(acc.id), -1, delta=True)
                db_session.commit()
                self.syncing_accounts.discard(account_id)
            return True
