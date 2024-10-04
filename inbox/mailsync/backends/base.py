import concurrent.futures
import threading

from inbox.config import config
from inbox.greenlet_like import GreenletLikeThread, GreenletLikeThreadExit
from inbox.logging import get_logger
from inbox.models.session import session_scope
from inbox.util.concurrency import retry_with_logging
from inbox.util.debug import bind_context

log = get_logger()

THROTTLE_COUNT = config.get("THROTTLE_COUNT", 200)
THROTTLE_WAIT = config.get("THROTTLE_WAIT", 60)


class MailsyncError(Exception):
    pass


class MailsyncDone(GreenletLikeThreadExit):
    pass


class BaseMailSyncMonitor(GreenletLikeThread):
    """
    The SYNC_MONITOR_CLS for all mail sync providers should subclass this.

    Parameters
    ----------
    account_id : int
        Which account to sync.
    email_address : str
        Email address for `account_id`.
    provider : str
        Provider for `account_id`.
    heartbeat : int
        How often to check for commands.
    """

    def __init__(self, account, heartbeat=1):
        bind_context(self, "mailsyncmonitor", account.id)
        self.shutdown = threading.Event()
        # how often to check inbox, in seconds
        self.heartbeat = heartbeat
        self.log = log.new(component="mail sync", account_id=account.id)
        self.account_id = account.id
        self.namespace_id = account.namespace.id
        self.email_address = account.email_address
        self.provider_name = account.verbose_provider

        super().__init__()

    def _run(self):
        try:
            return retry_with_logging(
                self._run_impl,
                account_id=self.account_id,
                provider=self.provider_name,
                logger=self.log,
            )
        except GreenletLikeThreadExit:
            self._cleanup()
            raise

    def _run_impl(self):
        self.sync_greenlet = GreenletLikeThread(
            retry_with_logging,
            self.sync,
            account_id=self.account_id,
            provider=self.provider_name,
            logger=self.log,
        )
        self.sync_greenlet.start()
        # MARK: child spawn
        self.sync_greenlet.join()

        if self.sync_greenlet.successful():
            self._cleanup()
            self.log.info(
                "mail sync finished successfully", provider=self.provider_name
            )
            return

        self.log.error(
            "mail sync raised an exception",
            provider=self.provider_name,
            exc=self.sync_greenlet.exception,
        )
        raise self.sync_greenlet.exception

    def sync(self):
        raise NotImplementedError

    def _cleanup(self):
        self.sync_greenlet.kill()
        with session_scope(self.namespace_id) as mailsync_db_session:
            for x in self.folder_monitors:
                x.set_stopped(mailsync_db_session)
        with concurrent.futures.ThreadPoolExecutor(
            len(self.folder_monitors) or 1
        ) as executor:
            executor.map(
                lambda folder_monitor: folder_monitor.kill(), self.folder_monitors
            )

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__}(account_id={self.account_id!r})>"
