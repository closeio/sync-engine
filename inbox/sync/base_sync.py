import threading
import time

from gevent import Greenlet

from inbox.exceptions import ConnectionError, ValidationError
from inbox.heartbeat.store import HeartbeatStatusProxy
from inbox.logging import get_logger
from inbox.models import Account
from inbox.models.session import session_scope
from inbox.util.concurrency import retry_with_logging

logger = get_logger()


class BaseSyncMonitor(Greenlet):
    """
    Abstracted sync monitor, based on BaseMailSyncMonitor but not mail-specific

    Subclasses should run
    bind_context(self, 'mailsyncmonitor', account.id)

    poll_frequency : int
        How often to check for commands.
    retry_fail_classes : list
        Exceptions to *not* retry on.

    """

    def __init__(
        self,
        account_id,
        namespace_id,
        email_address,
        folder_id,
        folder_name,
        provider_name,
        poll_frequency=1,
        scope=None,
    ):
        self.account_id = account_id
        self.namespace_id = namespace_id
        self.provider_name = provider_name
        self.poll_frequency = poll_frequency
        self.scope = scope

        self.log = logger.new(account_id=account_id)

        self.shutdown = threading.Event()
        self.heartbeat_status = HeartbeatStatusProxy(
            self.account_id, folder_id, folder_name, email_address, provider_name
        )
        super().__init__()

    def _run(self):
        # Bind greenlet-local logging context.
        self.log = self.log.new(account_id=self.account_id)
        try:
            while True:
                retry_with_logging(
                    self._run_impl,
                    account_id=self.account_id,
                    fail_classes=[ValidationError],
                    provider=self.provider_name,
                    logger=self.log,
                )
        except ValidationError:
            # Bad account credentials; exit.
            self.log.error(
                "Credential validation error; exiting",
                exc_info=True,
                logstash_tag="mark_invalid",
            )
            with session_scope(self.namespace_id) as db_session:
                account = db_session.query(Account).get(self.account_id)
                account.mark_invalid(scope=self.scope)

    def _run_impl(self):
        try:
            self.sync()
            self.heartbeat_status.publish(state="poll")

        # If we get a connection or API permissions error, then sleep
        # 2x poll frequency.
        except ConnectionError:
            self.log.error("Error while polling", exc_info=True)
            time.sleep(self.poll_frequency)
        time.sleep(self.poll_frequency)

    def sync(self):
        """Subclasses should override this to do work"""
        raise NotImplementedError
