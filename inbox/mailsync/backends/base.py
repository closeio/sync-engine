import threading
from typing import TYPE_CHECKING, ClassVar

from inbox.config import config
from inbox.interruptible_threading import InterruptibleThread, InterruptibleThreadExit
from inbox.logging import get_logger
from inbox.models.session import session_scope
from inbox.util.concurrency import kill_all, retry_with_logging
from inbox.util.debug import bind_context

if TYPE_CHECKING:
    from inbox.mailsync.backends.imap.generic import FolderSyncEngine

log = get_logger()

THROTTLE_COUNT = config.get("THROTTLE_COUNT", 200)
THROTTLE_WAIT = config.get("THROTTLE_WAIT", 60)


class MailsyncError(Exception):
    pass


class MailsyncDone(InterruptibleThreadExit):
    pass


class BaseMailSyncMonitor(InterruptibleThread):
    sync_engine_class: ClassVar[type["FolderSyncEngine"]]

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

        self.name = f"{self.__class__.__name__}(account_id={account.id!r})"

    def _run(self):
        try:
            return retry_with_logging(
                self._run_impl,
                account_id=self.account_id,
                provider=self.provider_name,
                logger=self.log,
            )
        except InterruptibleThreadExit:
            self._cleanup()
            raise

    def _run_impl(self):
        self.sync_thread = InterruptibleThread(
            retry_with_logging,
            self.sync,
            account_id=self.account_id,
            provider=self.provider_name,
            logger=self.log,
        )
        self.sync_thread.start()
        self.sync_thread.join()

        if self.sync_thread.successful():
            self._cleanup()
            self.log.info(
                "mail sync finished successfully", provider=self.provider_name
            )
            return

        self.log.error(
            "mail sync raised an exception",
            provider=self.provider_name,
            exc=self.sync_thread.exception,
        )
        raise self.sync_thread.exception

    def sync(self):
        raise NotImplementedError

    def _cleanup(self):
        self.sync_thread.kill()
        with session_scope(self.namespace_id) as mailsync_db_session:
            for x in self.folder_monitors:
                x.set_stopped(mailsync_db_session)
        kill_all(self.folder_monitors)

    def __repr__(self) -> str:
        return f"<{self.name}>"
