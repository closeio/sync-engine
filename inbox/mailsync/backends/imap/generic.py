# deal with unicode literals: http://www.python.org/dev/peps/pep-0263/
"""
----------------
IMAP SYNC ENGINE
----------------

Okay, here's the deal.

The IMAP sync engine runs per-folder on each account.

Only one initial sync can be running per-account at a time, to avoid
hammering the IMAP backend too hard (Gmail shards per-user, so parallelizing
folder download won't actually increase our throughput anyway).

Any time we reconnect, we have to make sure the folder's uidvalidity hasn't
changed, and if it has, we need to update the UIDs for any messages we've
already downloaded. A folder's uidvalidity cannot change during a session
(SELECT during an IMAP session starts a session on a folder) (see
http://tools.ietf.org/html/rfc3501#section-2.3.1.1).

Note that despite a session giving you a HIGHESTMODSEQ at the start of a
SELECT, that session will still always give you the latest message list
including adds, deletes, and flag changes that have happened since that
highestmodseq. (In Gmail, there is a small delay between changes happening on
the web client and those changes registering on a connected IMAP session,
though bizarrely the HIGHESTMODSEQ is updated immediately.) So we have to keep
in mind that the data may be changing behind our backs as we're syncing.
Fetching info about UIDs that no longer exist is not an error but gives us
empty data.

Folder sync state is stored in the ImapFolderSyncStatus table to allow for
restarts.

Here's the state machine:


        -----
        |   ----------------         ----------------------
        ∨   | initial sync | <-----> | initial uidinvalid |
----------  ----------------         ----------------------
| finish |      |    ^
----------      |    |_________________________
        ^       ∨                              |
        |   ----------------         ----------------------
        |---|      poll    | <-----> |   poll uidinvalid  |
            ----------------         ----------------------
            |  ∧
            ----

We encapsulate sync engine instances in greenlets for cooperative coroutine
scheduling around network I/O.

--------------
SESSION SCOPES
--------------

Database sessions are held for as short a duration as possible---just to
query for needed information or update the local state. Long-held database
sessions reduce scalability.

"""


import contextlib
import imaplib
import time
from datetime import datetime, timedelta
from typing import Any, Dict

import gevent
from gevent import Greenlet
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import NoResultFound

from inbox.basicauth import ValidationError
from inbox.logging import get_logger
from inbox.util.concurrency import retry_with_logging
from inbox.util.debug import bind_context
from inbox.util.itert import chunk
from inbox.util.misc import or_none
from inbox.util.stats import statsd_client
from inbox.util.threading import MAX_THREAD_LENGTH, fetch_corresponding_thread

log = get_logger()
from inbox.crispin import FolderMissingError, connection_pool, retry_crispin
from inbox.events.ical import import_attached_events
from inbox.heartbeat.store import HeartbeatStatusProxy
from inbox.mailsync.backends.base import (
    THROTTLE_COUNT,
    THROTTLE_WAIT,
    MailsyncDone,
    MailsyncError,
)
from inbox.mailsync.backends.imap import common
from inbox.models import Account, Folder, Message
from inbox.models.backends.imap import (
    ImapFolderInfo,
    ImapFolderSyncStatus,
    ImapThread,
    ImapUid,
)
from inbox.models.session import session_scope

# Idle doesn't necessarily pick up flag changes, so we don't want to
# idle for very long, or we won't detect things like messages being
# marked as read.
IDLE_WAIT = 30
DEFAULT_POLL_FREQUENCY = 30
# Poll on the Inbox folder more often.
INBOX_POLL_FREQUENCY = 10
FAST_FLAGS_REFRESH_LIMIT = 100
SLOW_FLAGS_REFRESH_LIMIT = 2000
SLOW_REFRESH_INTERVAL = timedelta(seconds=3600)
FAST_REFRESH_INTERVAL = timedelta(seconds=30)

# Maximum number of uidinvalidity errors in a row.
MAX_UIDINVALID_RESYNCS = 5

CONDSTORE_FLAGS_REFRESH_BATCH_SIZE = 200


class FolderSyncEngine(Greenlet):
    """Base class for a per-folder IMAP sync engine."""

    def __init__(
        self,
        account_id,
        namespace_id,
        folder_name,
        email_address,
        provider_name,
        syncmanager_lock,
    ):

        with session_scope(namespace_id) as db_session:
            try:
                folder = (
                    db_session.query(Folder)
                    .filter(Folder.name == folder_name, Folder.account_id == account_id)
                    .one()
                )
            except NoResultFound:
                raise MailsyncError(
                    "Missing Folder '{}' on account {}".format(folder_name, account_id)
                )

            self.folder_id = folder.id
            self.folder_role = folder.canonical_name
            # Metric flags for sync performance
            self.is_initial_sync = folder.initial_sync_end is None
            self.is_first_sync = folder.initial_sync_start is None
            self.is_first_message = self.is_first_sync

        bind_context(self, "foldersyncengine", account_id, self.folder_id)
        self.account_id = account_id
        self.namespace_id = namespace_id
        self.folder_name = folder_name
        self.email_address = email_address

        if self.folder_name.lower() == "inbox":
            self.poll_frequency = INBOX_POLL_FREQUENCY
        else:
            self.poll_frequency = DEFAULT_POLL_FREQUENCY
        self.syncmanager_lock = syncmanager_lock
        self.state = None
        self.provider_name = provider_name
        self.last_fast_refresh = None
        self.flags_fetch_results = {}
        self.conn_pool = connection_pool(self.account_id)
        self.polling_logged_at = 0

        self.state_handlers = {
            "initial": self.initial_sync,
            "initial uidinvalid": self.resync_uids,
            "poll": self.poll,
            "poll uidinvalid": self.resync_uids,
            "finish": lambda: "finish",
        }

        self.setup_heartbeats()
        Greenlet.__init__(self)

        # Some generic IMAP servers are throwing UIDVALIDITY
        # errors forever. Instead of resyncing those servers
        # ad vitam, we keep track of the number of consecutive
        # times we got such an error and bail out if it's higher than
        # MAX_UIDINVALID_RESYNCS.
        self.uidinvalid_count = 0

    def setup_heartbeats(self):
        self.heartbeat_status = HeartbeatStatusProxy(
            self.account_id,
            self.folder_id,
            self.folder_name,
            self.email_address,
            self.provider_name,
        )

    def _run(self):
        # Bind greenlet-local logging context.
        self.log = log.new(
            account_id=self.account_id,
            folder=self.folder_name,
            provider=self.provider_name,
        )
        # eagerly signal the sync status
        self.heartbeat_status.publish()

        def start_sync(saved_folder_status):
            # Ensure we don't cause an error if the folder was deleted.
            sync_end_time = (
                saved_folder_status.folder
                and saved_folder_status.metrics.get("sync_end_time")
            )
            if sync_end_time:
                sync_delay = datetime.utcnow() - sync_end_time
                if sync_delay > timedelta(days=1):
                    saved_folder_status.state = "initial"
                    log.info(
                        "switching to initial sync due to delay",
                        folder_id=self.folder_id,
                        account_id=self.account_id,
                        sync_delay=sync_delay.total_seconds(),
                    )

            saved_folder_status.start_sync()

        try:
            self.update_folder_sync_status(start_sync)
        except IntegrityError:
            # The state insert failed because the folder ID ForeignKey
            # was no longer valid, ie. the folder for this engine was deleted
            # while we were starting up.
            # Exit the sync and let the monitor sort things out.
            log.info(
                "Folder state loading failed due to IntegrityError",
                folder_id=self.folder_id,
                account_id=self.account_id,
            )
            raise MailsyncDone()

        # NOTE: The parent ImapSyncMonitor handler could kill us at any
        # time if it receives a shutdown command. The shutdown command is
        # equivalent to ctrl-c.
        while self.state != "finish":
            retry_with_logging(
                self._run_impl,
                account_id=self.account_id,
                provider=self.provider_name,
                logger=log,
            )

    def _run_impl(self):
        old_state = self.state
        try:
            self.state = self.state_handlers[old_state]()
            self.heartbeat_status.publish(state=self.state)
        except UidInvalid:
            self.state = self.state + " uidinvalid"
            self.uidinvalid_count += 1
            self.heartbeat_status.publish(state=self.state)

            # Check that we're not stuck in an endless uidinvalidity resync loop.
            if self.uidinvalid_count > MAX_UIDINVALID_RESYNCS:
                log.error(
                    "Resynced more than MAX_UIDINVALID_RESYNCS in a"
                    " row. Stopping sync.",
                    folder_name=self.folder_name,
                )

                # Only stop syncing the entire account if the INBOX folder is
                # failing. Otherwise simply stop syncing the folder.
                if self.folder_name.lower() == "inbox":
                    with session_scope(self.namespace_id) as db_session:
                        account = db_session.query(Account).get(self.account_id)
                        account.disable_sync(
                            "Detected endless uidvalidity " "resync loop"
                        )
                        account.sync_state = "stopped"
                        db_session.commit()
                    raise MailsyncDone()
                else:
                    self.state = "finish"
                    self.heartbeat_status.publish(state=self.state)

        except FolderMissingError:
            # Folder was deleted by monitor while its sync was running.
            # TODO: Monitor should handle shutting down the folder engine.
            log.info(
                "Folder disappeared. Stopping sync.",
                account_id=self.account_id,
                folder_id=self.folder_id,
            )
            raise MailsyncDone()
        except ValidationError as exc:
            log.error(
                "Error authenticating; stopping sync",
                exc_info=True,
                account_id=self.account_id,
                folder_id=self.folder_id,
                logstash_tag="mark_invalid",
            )
            with session_scope(self.namespace_id) as db_session:
                account = db_session.query(Account).get(self.account_id)
                account.mark_invalid()
                account.update_sync_error(exc)
            raise MailsyncDone()

        # State handlers are idempotent, so it's okay if we're
        # killed between the end of the handler and the commit.
        if self.state != old_state:

            def update(status):
                status.state = self.state

            self.update_folder_sync_status(update)

        if self.state == old_state and self.state in ["initial", "poll"]:
            # We've been through a normal state transition without raising any
            # error. It's safe to reset the uidvalidity counter.
            self.uidinvalid_count = 0

    def update_folder_sync_status(self, cb):
        # Loads the folder sync status and invokes the provided callback to
        # modify it. Commits any changes and updates `self.state` to ensure
        # they are never out of sync.
        with session_scope(self.namespace_id) as db_session:
            try:
                saved_folder_status = (
                    db_session.query(ImapFolderSyncStatus)
                    .filter_by(account_id=self.account_id, folder_id=self.folder_id)
                    .one()
                )

            except NoResultFound:
                saved_folder_status = ImapFolderSyncStatus(
                    account_id=self.account_id, folder_id=self.folder_id
                )
                db_session.add(saved_folder_status)

            cb(saved_folder_status)
            db_session.commit()

            self.state = saved_folder_status.state

    def set_stopped(self, db_session):
        self.update_folder_sync_status(lambda s: s.stop_sync())

    def _report_initial_sync_start(self):
        with session_scope(self.namespace_id) as db_session:
            q = db_session.query(Folder).get(self.folder_id)
            q.initial_sync_start = datetime.utcnow()

    def _report_initial_sync_end(self):
        with session_scope(self.namespace_id) as db_session:
            q = db_session.query(Folder).get(self.folder_id)
            q.initial_sync_end = datetime.utcnow()

    @retry_crispin
    def initial_sync(self):
        log.bind(state="initial")
        log.info("starting initial sync")

        if self.is_first_sync:
            self._report_initial_sync_start()
            self.is_first_sync = False

        with self.conn_pool.get() as crispin_client:
            crispin_client.select_folder(self.folder_name, uidvalidity_cb)
            # Ensure we have an ImapFolderInfo row created prior to sync start.
            with session_scope(self.namespace_id) as db_session:
                try:
                    db_session.query(ImapFolderInfo).filter(
                        ImapFolderInfo.account_id == self.account_id,
                        ImapFolderInfo.folder_id == self.folder_id,
                    ).one()
                except NoResultFound:
                    imapfolderinfo = ImapFolderInfo(
                        account_id=self.account_id,
                        folder_id=self.folder_id,
                        uidvalidity=crispin_client.selected_uidvalidity,
                        uidnext=crispin_client.selected_uidnext,
                    )
                    db_session.add(imapfolderinfo)
                db_session.commit()

            self.initial_sync_impl(crispin_client)

        if self.is_initial_sync:
            self._report_initial_sync_end()
            self.is_initial_sync = False

        return "poll"

    @retry_crispin
    def poll(self):
        log.bind(state="poll")
        # Only log every 5 minutes to cut down on the volume of
        # this log statement
        timestamp = time.time()
        if timestamp - self.polling_logged_at > 60 * 5:
            self.polling_logged_at = timestamp
            log.debug("polling")
        self.poll_impl()
        return "poll"

    @retry_crispin
    def resync_uids(self):
        log.bind(state=self.state)
        log.warning("UIDVALIDITY changed; initiating resync")
        self.resync_uids_impl()
        return "initial"

    def initial_sync_impl(self, crispin_client):
        # We wrap the block in a try/finally because the change_poller greenlet
        # needs to be killed when this greenlet is interrupted
        change_poller = None
        try:
            assert crispin_client.selected_folder_name == self.folder_name
            remote_uids = crispin_client.all_uids()
            with self.syncmanager_lock:
                with session_scope(self.namespace_id) as db_session:
                    local_uids = common.local_uids(
                        self.account_id, db_session, self.folder_id
                    )
                common.remove_deleted_uids(
                    self.account_id,
                    self.folder_id,
                    set(local_uids).difference(remote_uids),
                )

            new_uids = set(remote_uids).difference(local_uids)
            with session_scope(self.namespace_id) as db_session:
                account = db_session.query(Account).get(self.account_id)
                throttled = account.throttled
                self.update_uid_counts(
                    db_session,
                    remote_uid_count=len(remote_uids),
                    # This is the initial size of our download_queue
                    download_uid_count=len(new_uids),
                )

            change_poller = gevent.spawn(self.poll_for_changes)
            bind_context(change_poller, "changepoller", self.account_id, self.folder_id)
            uids = sorted(new_uids, reverse=True)
            for count, uid in enumerate(uids, start=1):
                # The speedup from batching appears to be less clear for
                # non-Gmail accounts, so for now just download one-at-a-time.
                self.download_and_commit_uids(crispin_client, [uid])
                self.heartbeat_status.publish()
                if throttled and count >= THROTTLE_COUNT:
                    # Throttled accounts' folders sync at a rate of
                    # 1 message/ minute, after the first approx. THROTTLE_COUNT
                    # messages per folder are synced.
                    # Note this is an approx. limit since we use the #(uids),
                    # not the #(messages).
                    gevent.sleep(THROTTLE_WAIT)
        finally:
            if change_poller is not None:
                # schedule change_poller to die
                gevent.kill(change_poller)

    def should_idle(self, crispin_client):
        if not hasattr(self, "_should_idle"):
            self._should_idle = (
                crispin_client.idle_supported()
                and self.folder_name in crispin_client.folder_names()["inbox"]
            )
        return self._should_idle

    def poll_impl(self):
        with self.conn_pool.get() as crispin_client:
            self.check_uid_changes(crispin_client)
            if self.should_idle(crispin_client):
                crispin_client.select_folder(self.folder_name, self.uidvalidity_cb)
                idling = True
                try:
                    crispin_client.idle(IDLE_WAIT)
                except Exception as exc:
                    # With some servers we get e.g.
                    # 'Unexpected IDLE response: * FLAGS  (...)'
                    if isinstance(exc, imaplib.IMAP4.error):
                        message = exc.args[0] if exc.args else ""
                        if not message.startswith("Unexpected IDLE response"):
                            raise

                        log.info("Error initiating IDLE, not idling", error=exc)
                        with contextlib.suppress(AttributeError):
                            # Still have to take the connection out of IDLE
                            # mode to reuse it though.
                            crispin_client.conn.idle_done()
                        idling = False
                    else:
                        raise
            else:
                idling = False
        # Close IMAP connection before sleeping
        if not idling:
            gevent.sleep(self.poll_frequency)

    def resync_uids_impl(self):
        # First, let's check if the UIVDALIDITY change was spurious, if
        # it is, just discard it and go on.
        with self.conn_pool.get() as crispin_client:
            crispin_client.select_folder(self.folder_name, lambda *args: True)
            remote_uidvalidity = crispin_client.selected_uidvalidity
            remote_uidnext = crispin_client.selected_uidnext
            if remote_uidvalidity <= self.uidvalidity:
                log.debug("UIDVALIDITY unchanged")
                return
        # Otherwise, if the UIDVALIDITY really has changed, discard all saved
        # UIDs for the folder, mark associated messages for garbage-collection,
        # and return to the 'initial' state to resync.
        # This will cause message and threads to be deleted and recreated, but
        # uidinvalidity is sufficiently rare that this tradeoff is acceptable.
        with session_scope(self.namespace_id) as db_session:
            invalid_uids = {
                uid
                for uid, in db_session.query(ImapUid.msg_uid).filter_by(
                    account_id=self.account_id, folder_id=self.folder_id
                )
            }
        with self.syncmanager_lock:
            common.remove_deleted_uids(self.account_id, self.folder_id, invalid_uids)
        self.uidvalidity = remote_uidvalidity
        self.highestmodseq = None
        self.uidnext = remote_uidnext

    @retry_crispin
    def poll_for_changes(self):
        log.new(account_id=self.account_id, folder=self.folder_name)
        while True:
            log.debug("polling for changes")
            self.poll_impl()

    def create_message(self, db_session, acct, folder, msg):
        assert acct is not None and acct.namespace is not None

        # Check if we somehow already saved the imapuid (shouldn't happen, but
        # possible due to race condition). If so, don't commit changes.
        existing_imapuid = (
            db_session.query(ImapUid)
            .filter(
                ImapUid.account_id == acct.id,
                ImapUid.folder_id == folder.id,
                ImapUid.msg_uid == msg.uid,
            )
            .first()
        )
        if existing_imapuid is not None:
            log.error(
                "Expected to create imapuid, but existing row found",
                remote_msg_uid=msg.uid,
                existing_imapuid=existing_imapuid.id,
            )
            return None

        # Check if the message is valid.
        if msg.body is None:
            log.warning("Server returned a message with an empty body.")
            return None

        new_uid = common.create_imap_message(db_session, acct, folder, msg)
        self.add_message_to_thread(db_session, new_uid.message, msg)

        db_session.flush()

        # We're calling import_attached_events here instead of some more
        # obvious place (like Message.create_from_synced) because the function
        # requires new_uid.message to have been flushed.
        # This is necessary because the import_attached_events does db lookups.
        if new_uid.message.has_attached_events:
            with db_session.no_autoflush:
                import_attached_events(db_session, acct, new_uid.message)

        # If we're in the polling state, then we want to report the metric
        # for latency when the message was received vs created
        if self.state == "poll":
            latency_millis = (
                datetime.utcnow() - new_uid.message.received_date
            ).total_seconds() * 1000
            metrics = [
                ".".join(["mailsync", "providers", "overall", "message_latency"]),
                ".".join(
                    ["mailsync", "providers", self.provider_name, "message_latency"]
                ),
            ]
            for metric in metrics:
                statsd_client.timing(metric, latency_millis)

        return new_uid

    def _count_thread_messages(self, thread_id, db_session):
        (count,) = (
            db_session.query(func.count(Message.id))
            .filter(Message.thread_id == thread_id)
            .one()
        )
        return count

    def add_message_to_thread(self, db_session, message_obj, raw_message):
        """Associate message_obj to the right Thread object, creating a new
        thread if necessary."""
        with db_session.no_autoflush:
            # Disable autoflush so we don't try to flush a message with null
            # thread_id.
            parent_thread = fetch_corresponding_thread(
                db_session, self.namespace_id, message_obj
            )
            construct_new_thread = True

            if parent_thread:
                # If there's a parent thread that isn't too long already,
                # add to it. Otherwise create a new thread.
                parent_message_count = self._count_thread_messages(
                    parent_thread.id, db_session
                )
                if parent_message_count < MAX_THREAD_LENGTH:
                    construct_new_thread = False

            if construct_new_thread:
                message_obj.thread = ImapThread.from_imap_message(
                    db_session, self.namespace_id, message_obj
                )
            else:
                parent_thread.messages.append(message_obj)

    def download_and_commit_uids(self, crispin_client, uids):
        start = datetime.utcnow()
        raw_messages = crispin_client.uids(uids)
        if not raw_messages:
            return 0

        new_uids = set()
        with self.syncmanager_lock, session_scope(self.namespace_id) as db_session:
            account = Account.get(self.account_id, db_session)
            folder = Folder.get(self.folder_id, db_session)
            for msg in raw_messages:
                uid = self.create_message(db_session, account, folder, msg)
                if uid is not None:
                    db_session.add(uid)
                    db_session.flush()
                    new_uids.add(uid)
            db_session.commit()

        log.debug("Committed new UIDs", new_committed_message_count=len(new_uids))
        # If we downloaded uids, record message velocity (#uid / latency)
        if self.state == "initial" and len(new_uids):
            self._report_message_velocity(datetime.utcnow() - start, len(new_uids))
        if self.is_first_message:
            self._report_first_message()
            self.is_first_message = False

        return len(new_uids)

    def _report_first_message(self):
        # Only record the "time to first message" in the inbox. Because users
        # can add more folders at any time, "initial sync"-style metrics for
        # other folders don't mean much.
        if self.folder_role not in ["inbox", "all"]:
            return

        now = datetime.utcnow()
        with session_scope(self.namespace_id) as db_session:
            account = db_session.query(Account).get(self.account_id)
            account_created = account.created_at

        latency = (now - account_created).total_seconds() * 1000

        metrics = [
            ".".join(["mailsync", "providers", self.provider_name, "first_message"]),
            ".".join(["mailsync", "providers", "overall", "first_message"]),
        ]

        for metric in metrics:
            statsd_client.timing(metric, latency)

    def _report_message_velocity(self, timedelta, num_uids):
        latency = (timedelta).total_seconds() * 1000
        latency_per_uid = float(latency) / num_uids
        metrics = [
            ".".join(["mailsync", "providers", self.provider_name, "message_velocity"]),
            ".".join(["mailsync", "providers", "overall", "message_velocity"]),
        ]
        for metric in metrics:
            statsd_client.timing(metric, latency_per_uid)

    def update_uid_counts(self, db_session, **kwargs):
        saved_status = (
            db_session.query(ImapFolderSyncStatus)
            .join(Folder)
            .filter(
                ImapFolderSyncStatus.account_id == self.account_id,
                Folder.name == self.folder_name,
            )
            .one()
        )
        # We're not updating the current_remote_count metric
        # so don't update uid_checked_timestamp.
        if kwargs.get("remote_uid_count") is None:
            saved_status.update_metrics(kwargs)
        else:
            metrics = dict(uid_checked_timestamp=datetime.utcnow())
            metrics.update(kwargs)
            saved_status.update_metrics(metrics)

    def get_new_uids(self, crispin_client):
        try:
            remote_uidnext = crispin_client.conn.folder_status(
                self.folder_name, ["UIDNEXT"]
            ).get(b"UIDNEXT")
        except ValueError:
            # Work around issue where ValueError is raised on parsing STATUS
            # response.
            log.warning("Error getting UIDNEXT", exc_info=True)
            remote_uidnext = None
        except imaplib.IMAP4.error as e:
            # TODO: match with CrispinClient.select_folder
            message = e.args[0] if e.args else ""
            if (
                "[NONEXISTENT]" in message
                or "does not exist" in message
                or "doesn't exist" in message
            ):
                raise FolderMissingError()
            else:
                raise e
        if remote_uidnext is not None and remote_uidnext == self.uidnext:
            return
        log.debug(
            "UIDNEXT changed, checking for new UIDs",
            remote_uidnext=remote_uidnext,
            saved_uidnext=self.uidnext,
        )

        crispin_client.select_folder(self.folder_name, self.uidvalidity_cb)
        with session_scope(self.namespace_id) as db_session:
            lastseenuid = common.lastseenuid(
                self.account_id, db_session, self.folder_id
            )
        latest_uids = crispin_client.conn.fetch(
            "{}:*".format(lastseenuid + 1), ["UID"]
        ).keys()
        new_uids = set(latest_uids) - {lastseenuid}
        if new_uids:
            for uid in sorted(new_uids):
                self.download_and_commit_uids(crispin_client, [uid])
        self.uidnext = remote_uidnext

    def condstore_refresh_flags(self, crispin_client):
        new_highestmodseq = crispin_client.conn.folder_status(
            self.folder_name, ["HIGHESTMODSEQ"]
        )[
            b"HIGHESTMODSEQ"
        ]  # type: int
        # Ensure that we have an initial highestmodseq value stored before we
        # begin polling for changes.
        if self.highestmodseq is None:
            self.highestmodseq = new_highestmodseq

        if new_highestmodseq == self.highestmodseq:
            # Don't need to do anything if the highestmodseq hasn't
            # changed.
            return
        elif new_highestmodseq < self.highestmodseq:
            # This should really never happen, but if it does, handle it.
            log.warning(
                "got server highestmodseq less than saved " "highestmodseq",
                new_highestmodseq=new_highestmodseq,
                saved_highestmodseq=self.highestmodseq,
            )
            return

        log.debug(
            "HIGHESTMODSEQ has changed, getting changed UIDs",
            new_highestmodseq=new_highestmodseq,
            saved_highestmodseq=self.highestmodseq,
        )
        crispin_client.select_folder(self.folder_name, self.uidvalidity_cb)
        changed_flags = crispin_client.condstore_changed_flags(self.highestmodseq)
        remote_uids = crispin_client.all_uids()

        # In order to be able to sync changes to tens of thousands of flags at
        # once, we commit updates in batches. We do this in ascending order by
        # modseq and periodically "checkpoint" our saved highestmodseq. (It's
        # safe to checkpoint *because* we go in ascending order by modseq.)
        # That way if the process gets restarted halfway through this refresh,
        # we don't have to completely start over. It's also slow to load many
        # objects into the SQLAlchemy session and then issue lots of commits;
        # we avoid that by batching.
        flag_batches = chunk(
            sorted(
                changed_flags.items(), key=lambda key_and_value: key_and_value[1].modseq
            ),
            CONDSTORE_FLAGS_REFRESH_BATCH_SIZE,
        )
        for flag_batch in flag_batches:
            with session_scope(self.namespace_id) as db_session:
                common.update_metadata(
                    self.account_id,
                    self.folder_id,
                    self.folder_role,
                    dict(flag_batch),
                    db_session,
                )
            if len(flag_batch) == CONDSTORE_FLAGS_REFRESH_BATCH_SIZE:
                interim_highestmodseq = max(v.modseq for k, v in flag_batch)
                self.highestmodseq = interim_highestmodseq

        with session_scope(self.namespace_id) as db_session:
            local_uids = common.local_uids(self.account_id, db_session, self.folder_id)
            expunged_uids = set(local_uids).difference(remote_uids)

        if expunged_uids:
            # If new UIDs have appeared since we last checked in
            # get_new_uids, save them first. We want to always have the
            # latest UIDs before expunging anything, in order to properly
            # capture draft revisions.
            with session_scope(self.namespace_id) as db_session:
                lastseenuid = common.lastseenuid(
                    self.account_id, db_session, self.folder_id
                )
            if remote_uids and lastseenuid < max(remote_uids):
                log.info("Downloading new UIDs before expunging")
                self.get_new_uids(crispin_client)
            with self.syncmanager_lock:
                common.remove_deleted_uids(
                    self.account_id, self.folder_id, expunged_uids
                )
        self.highestmodseq = new_highestmodseq

    def generic_refresh_flags(self, crispin_client):
        now = datetime.utcnow()
        slow_refresh_due = (
            self.last_slow_refresh is None
            or now > self.last_slow_refresh + SLOW_REFRESH_INTERVAL
        )
        fast_refresh_due = (
            self.last_fast_refresh is None
            or now > self.last_fast_refresh + FAST_REFRESH_INTERVAL
        )
        if slow_refresh_due:
            self.refresh_flags_impl(crispin_client, SLOW_FLAGS_REFRESH_LIMIT)
            self.last_slow_refresh = datetime.utcnow()
        elif fast_refresh_due:
            self.refresh_flags_impl(crispin_client, FAST_FLAGS_REFRESH_LIMIT)
            self.last_fast_refresh = datetime.utcnow()

    def refresh_flags_impl(self, crispin_client, max_uids):
        crispin_client.select_folder(self.folder_name, self.uidvalidity_cb)

        # Check for any deleted messages.
        remote_uids = crispin_client.all_uids()
        with session_scope(self.namespace_id) as db_session:
            local_uids = common.local_uids(self.account_id, db_session, self.folder_id)
            expunged_uids = set(local_uids).difference(remote_uids)
        if expunged_uids:
            with self.syncmanager_lock:
                common.remove_deleted_uids(
                    self.account_id, self.folder_id, expunged_uids
                )

        # Get recent UIDs to monitor for flag changes.
        with session_scope(self.namespace_id) as db_session:
            local_uids = common.local_uids(
                account_id=self.account_id,
                session=db_session,
                folder_id=self.folder_id,
                limit=max_uids,
            )

        flags = crispin_client.flags(local_uids)
        if max_uids in self.flags_fetch_results and self.flags_fetch_results[
            max_uids
        ] == (local_uids, flags):
            # If the flags fetch response is exactly the same as the last one
            # we got, then we don't need to persist any changes.

            # Stopped logging this to reduce overall logging volume
            # log.debug('Unchanged flags refresh response, '
            #          'not persisting changes', max_uids=max_uids)
            return
        log.debug(
            "Changed flags refresh response, persisting changes", max_uids=max_uids
        )
        expunged_uids = set(local_uids).difference(flags.keys())
        with self.syncmanager_lock:
            common.remove_deleted_uids(self.account_id, self.folder_id, expunged_uids)
        with self.syncmanager_lock, session_scope(self.namespace_id) as db_session:
            common.update_metadata(
                self.account_id, self.folder_id, self.folder_role, flags, db_session
            )
        self.flags_fetch_results[max_uids] = (local_uids, flags)

    def check_uid_changes(self, crispin_client):
        self.get_new_uids(crispin_client)
        if crispin_client.condstore_supported():
            self.condstore_refresh_flags(crispin_client)
        else:
            self.generic_refresh_flags(crispin_client)

    @property
    def uidvalidity(self):
        if not hasattr(self, "_uidvalidity"):
            self._uidvalidity = self._load_imap_folder_info().uidvalidity
        return self._uidvalidity

    @uidvalidity.setter
    def uidvalidity(self, value):
        self._update_imap_folder_info("uidvalidity", value)
        self._uidvalidity = value

    @property
    def uidnext(self):
        if not hasattr(self, "_uidnext"):
            self._uidnext = self._load_imap_folder_info().uidnext
        return self._uidnext

    @uidnext.setter
    def uidnext(self, value):
        self._update_imap_folder_info("uidnext", value)
        self._uidnext = value

    @property
    def last_slow_refresh(self):
        # We persist the last_slow_refresh timestamp so that we don't end up
        # doing a (potentially expensive) full flags refresh for every account
        # on every process restart.
        if not hasattr(self, "_last_slow_refresh"):
            self._last_slow_refresh = self._load_imap_folder_info().last_slow_refresh
        return self._last_slow_refresh

    @last_slow_refresh.setter
    def last_slow_refresh(self, value):
        self._update_imap_folder_info("last_slow_refresh", value)
        self._last_slow_refresh = value

    @property
    def highestmodseq(self):
        if not hasattr(self, "_highestmodseq"):
            self._highestmodseq = self._load_imap_folder_info().highestmodseq
        return self._highestmodseq

    @highestmodseq.setter
    def highestmodseq(self, value):
        self._highestmodseq = value
        self._update_imap_folder_info("highestmodseq", value)

    def _load_imap_folder_info(self):
        with session_scope(self.namespace_id) as db_session:
            imapfolderinfo = (
                db_session.query(ImapFolderInfo)
                .filter(
                    ImapFolderInfo.account_id == self.account_id,
                    ImapFolderInfo.folder_id == self.folder_id,
                )
                .one()
            )
            db_session.expunge(imapfolderinfo)
            return imapfolderinfo

    def _update_imap_folder_info(self, attrname, value):
        with session_scope(self.namespace_id) as db_session:
            imapfolderinfo = (
                db_session.query(ImapFolderInfo)
                .filter(
                    ImapFolderInfo.account_id == self.account_id,
                    ImapFolderInfo.folder_id == self.folder_id,
                )
                .one()
            )
            setattr(imapfolderinfo, attrname, value)
            db_session.commit()

    def uidvalidity_cb(self, account_id, folder_name, select_info):
        assert folder_name == self.folder_name
        assert account_id == self.account_id
        selected_uidvalidity = select_info[b"UIDVALIDITY"]
        is_valid = self.uidvalidity is None or selected_uidvalidity <= self.uidvalidity
        if not is_valid:
            raise UidInvalid(
                "folder: {}, remote uidvalidity: {}, "
                "cached uidvalidity: {}".format(
                    folder_name.encode("utf-8"), selected_uidvalidity, self.uidvalidity
                )
            )
        return select_info


class UidInvalid(Exception):
    """Raised when a folder's UIDVALIDITY changes, requiring a resync."""

    pass


# This version is elsewhere in the codebase, so keep it for now
# TODO(emfree): clean this up.
def uidvalidity_cb(account_id, folder_name, select_info):
    # type: (int, str, Dict[bytes, Any]) -> Dict[bytes, Any]
    assert (
        folder_name is not None and select_info is not None
    ), "must start IMAP session before verifying UIDVALIDITY"
    with session_scope(account_id) as db_session:
        saved_folder_info = common.get_folder_info(account_id, db_session, folder_name)
        saved_uidvalidity = or_none(saved_folder_info, lambda i: i.uidvalidity)
    selected_uidvalidity = select_info[b"UIDVALIDITY"]
    if saved_folder_info:
        is_valid = (
            saved_uidvalidity is None or selected_uidvalidity <= saved_uidvalidity
        )
        if not is_valid:
            raise UidInvalid(
                "folder: {}, remote uidvalidity: {}, "
                "cached uidvalidity: {}".format(
                    folder_name.encode("utf-8"), selected_uidvalidity, saved_uidvalidity
                )
            )
    return select_info
