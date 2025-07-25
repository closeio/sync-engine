import os
import traceback
from datetime import datetime
from typing import Literal, Never

from sqlalchemy import (  # type: ignore[import-untyped]
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    String,
    bindparam,
    event,
    inspect,
)
from sqlalchemy.orm import relationship  # type: ignore[import-untyped]
from sqlalchemy.orm.session import Session  # type: ignore[import-untyped]
from sqlalchemy.sql.expression import false  # type: ignore[import-untyped]

from inbox.config import config
from inbox.logging import get_logger
from inbox.models.base import MailSyncBase
from inbox.models.calendar import Calendar
from inbox.models.mixins import (
    DeletedAtMixin,
    HasEmailAddress,
    HasPublicID,
    HasRevisions,
    HasRunState,
    UpdatedAtMixin,
)
from inbox.providers import provider_info
from inbox.scheduling.event_queue import EventQueue
from inbox.sqlalchemy_ext.util import JSON, MutableDict

log = get_logger()


# Note, you should never directly create Account objects. Instead you
# should use objects that inherit from this, such as GenericAccount or
# GmailAccount


CategoryType = Literal["folder", "label"]


class Account(
    MailSyncBase,
    HasPublicID,
    HasEmailAddress,
    HasRunState,
    HasRevisions,
    UpdatedAtMixin,
    DeletedAtMixin,
):
    API_OBJECT_NAME = "account"  # type: ignore[assignment]

    @property
    def provider(self) -> Never:
        """
        A constant, unique lowercase identifier for the account provider
        (e.g., 'gmail', 'eas'). Subclasses should override this.

        """
        raise NotImplementedError

    @property
    def verbose_provider(self):  # type: ignore[no-untyped-def]  # noqa: ANN201
        """
        A detailed identifier for the account provider
        (e.g., 'gmail', 'office365', 'outlook').
        Subclasses may override this.

        """
        return self.provider

    @property
    def category_type(self) -> CategoryType:
        """
        Whether the account is organized by folders or labels
        ('folder'/ 'label'), depending on the provider.
        Subclasses should override this.

        """
        raise NotImplementedError

    @property
    def auth_handler(self):  # type: ignore[no-untyped-def]  # noqa: ANN201
        from inbox.auth.base import handler_from_provider

        return handler_from_provider(self.provider)

    @property
    def provider_info(self):  # type: ignore[no-untyped-def]  # noqa: ANN201
        return provider_info(self.provider)

    @property
    def thread_cls(self):  # type: ignore[no-untyped-def]  # noqa: ANN201
        from inbox.models.thread import Thread

        return Thread

    # The default phrase used when sending mail from this account.
    name = Column(String(256), nullable=False, server_default="")

    # If True, throttle initial sync to reduce resource load
    throttled = Column(Boolean, server_default=false())

    # if True we sync contacts/events/email
    # NOTE: these columns are meaningless for EAS accounts
    sync_email = Column(Boolean, nullable=False, default=True)
    sync_contacts = Column(Boolean, nullable=False, default=False)
    sync_events = Column(Boolean, nullable=False, default=False)

    last_synced_contacts = Column(DateTime, nullable=True)

    # DEPRECATED
    last_synced_events = Column(DateTime, nullable=True)

    emailed_events_calendar_id = Column(
        BigInteger,
        ForeignKey(
            "calendar.id",
            ondelete="SET NULL",
            use_alter=True,
            name="emailed_events_cal",
        ),
        nullable=True,
    )

    _emailed_events_calendar = relationship(
        "Calendar", post_update=True, foreign_keys=[emailed_events_calendar_id]
    )

    def create_emailed_events_calendar(self) -> None:
        if not self._emailed_events_calendar:
            calname = "Emailed events"
            cal = Calendar(  # type: ignore[call-arg]
                namespace=self.namespace,  # type: ignore[attr-defined]
                description=calname,
                uid="inbox",
                name=calname,
                read_only=True,
            )
            self._emailed_events_calendar = cal

    @property
    def emailed_events_calendar(self):  # type: ignore[no-untyped-def]  # noqa: ANN201
        self.create_emailed_events_calendar()
        return self._emailed_events_calendar

    @emailed_events_calendar.setter
    def emailed_events_calendar(  # type: ignore[no-untyped-def]
        self, cal
    ) -> None:
        self._emailed_events_calendar = cal

    sync_host = Column(String(255), nullable=True)
    desired_sync_host = Column(String(255), nullable=True)

    # current state of this account
    state = Column(Enum("live", "down", "invalid"), nullable=True)

    # Based on account status, should the sync be running?
    # (Note, this is stored via a mixin.)
    # This is set to false if:
    #  - Account credentials are invalid (see mark_invalid())
    #  - External factors no longer require this account to sync
    # The value of this bit should always equal the AND value of all its
    # folders and heartbeats.

    @property
    def sync_enabled(self):  # type: ignore[no-untyped-def]  # noqa: ANN201
        return self.sync_should_run

    sync_state = Column(
        Enum("running", "stopped", "killed", "invalid", "connerror"),
        nullable=True,
    )

    _sync_status = Column(
        MutableDict.as_mutable(JSON), default={}, nullable=True
    )

    @property
    def sync_status(self):  # type: ignore[no-untyped-def]  # noqa: ANN201
        d = dict(
            id=self.id,
            email=self.email_address,
            provider=self.provider,
            is_enabled=self.sync_enabled,
            state=self.sync_state,
            sync_host=self.sync_host,
            desired_sync_host=self.desired_sync_host,
        )
        d.update(self._sync_status or {})

        return d

    @property
    def sync_error(self):  # type: ignore[no-untyped-def]  # noqa: ANN201
        return self._sync_status.get("sync_error")

    @property
    def initial_sync_start(self):  # type: ignore[no-untyped-def]  # noqa: ANN201
        if len(self.folders) == 0 or any(  # type: ignore[attr-defined]
            [
                f.initial_sync_start is None
                for f in self.folders  # type: ignore[attr-defined]
            ]
        ):
            return None
        return min(
            f.initial_sync_start
            for f in self.folders  # type: ignore[attr-defined]
        )

    @property
    def initial_sync_end(self):  # type: ignore[no-untyped-def]  # noqa: ANN201
        if len(self.folders) == 0 or any(  # type: ignore[attr-defined]
            [
                f.initial_sync_end is None
                for f in self.folders  # type: ignore[attr-defined]
            ]
        ):
            return None
        return max(
            f.initial_sync_end
            for f in self.folders  # type: ignore[attr-defined]
        )

    @property
    def initial_sync_duration(self):  # type: ignore[no-untyped-def]  # noqa: ANN201
        if not self.initial_sync_start or not self.initial_sync_end:
            return None
        return (self.initial_sync_end - self.initial_sync_end).total_seconds()

    def update_sync_error(  # type: ignore[no-untyped-def]
        self, error=None
    ) -> None:
        if error is None:
            self._sync_status["sync_error"] = None
        else:
            message = error.args[0] if error.args else ""
            error_obj = {
                "message": str(message)[:3000],
                "exception": "".join(
                    traceback.format_exception_only(type(error), error)
                )[:500],
                "traceback": traceback.format_exc(20)[:3000],
            }

            self._sync_status["sync_error"] = error_obj

    def sync_started(self) -> None:
        """
        Record transition to started state. Should be called after the
        sync is actually started, not when the request to start it is made.

        """
        current_time = datetime.utcnow()

        # Never run before (vs restarting stopped/killed)
        if self.sync_state is None and (
            not self._sync_status
            or self._sync_status.get("sync_end_time") is None
        ):
            self._sync_status["original_start_time"] = current_time

        self._sync_status["sync_start_time"] = current_time
        self._sync_status["sync_end_time"] = None
        self._sync_status["sync_error"] = None
        self._sync_status["sync_disabled_reason"] = None
        self._sync_status["sync_disabled_on"] = None
        self._sync_status["sync_disabled_by"] = None

        self.sync_state = "running"

    def enable_sync(  # type: ignore[no-untyped-def]
        self, sync_host=None
    ) -> None:
        """Tell the monitor that this account should be syncing."""
        self.sync_should_run = True
        if sync_host is not None:
            self.desired_sync_host = sync_host

    def disable_sync(self, reason) -> None:  # type: ignore[no-untyped-def]
        """Tell the monitor that this account should stop syncing."""
        self.sync_should_run = False
        self._sync_status["sync_disabled_reason"] = reason
        self._sync_status["sync_disabled_on"] = datetime.utcnow()
        self._sync_status["sync_disabled_by"] = os.environ.get(
            "USER", "unknown"
        )

    def mark_invalid(
        self, reason: str = "invalid credentials", scope: str = "mail"
    ) -> None:
        """
        In the event that the credentials for this account are invalid,
        update the status and sync flag accordingly. Should only be called
        after trying to re-authorize / get new token.

        """
        self.disable_sync(reason)
        self.sync_state = "invalid"

    def mark_for_deletion(self) -> None:
        """
        Mark account for deletion
        """
        self.disable_sync("account deleted")
        self.sync_state = "stopped"
        # Commit this to prevent race conditions
        inspect(self).session.commit()

    def unmark_for_deletion(self) -> None:
        self.enable_sync()
        self._sync_status = {}
        self.sync_state = "running"
        inspect(self).session.commit()

    def sync_stopped(  # type: ignore[no-untyped-def]
        self, requesting_host
    ) -> bool:
        """
        Record transition to stopped state. Should be called after the
        sync is actually stopped, not when the request to stop it is made.

        """
        if requesting_host == self.sync_host:
            # Perform a compare-and-swap before updating these values.
            # Only if the host requesting to update the account.sync_* attributes
            # here still owns the account sync (i.e is account.sync_host),
            # the request can proceed.
            self.sync_host = None
            if self.sync_state == "running":
                self.sync_state = "stopped"
            self._sync_status["sync_end_time"] = datetime.utcnow()
            return True
        return False

    @classmethod
    def get(cls, id_, session):  # type: ignore[no-untyped-def]  # noqa: ANN206
        q = session.query(cls)
        q = q.filter(cls.id == bindparam("id_"))
        return q.params(id_=id_).first()

    @property
    def is_killed(self):  # type: ignore[no-untyped-def]  # noqa: ANN201
        return self.sync_state == "killed"

    @property
    def is_running(self):  # type: ignore[no-untyped-def]  # noqa: ANN201
        return self.sync_state == "running"

    @property
    def is_marked_for_deletion(self):  # type: ignore[no-untyped-def]  # noqa: ANN201
        return (
            self.sync_state in ("stopped", "killed", "invalid")
            and self.sync_should_run is False
            and self._sync_status.get("sync_disabled_reason")
            == "account deleted"
        )

    @property
    def should_suppress_transaction_creation(self) -> bool:
        # Only version if new or the `sync_state` has changed.
        obj_state = inspect(self)
        return not (
            obj_state.pending
            or inspect(self).attrs.sync_state.history.has_changes()
        )

    @property
    def server_settings(self) -> None:
        return None

    def get_raw_message_contents(  # type: ignore[no-untyped-def]
        self, message
    ) -> Never:
        # Get the raw contents of a message. We do this differently
        # for every backend (Gmail, IMAP, EAS), and the best way
        # to do this across repos is to make it a method of the
        # account class.
        raise NotImplementedError

    discriminator = Column("type", String(16))
    __mapper_args__ = {
        "polymorphic_identity": "account",
        "polymorphic_on": discriminator,
    }


def should_send_event(obj):  # type: ignore[no-untyped-def]  # noqa: ANN201
    if not isinstance(obj, Account):
        return False
    inspected_obj = inspect(obj)
    hist = inspected_obj.attrs.sync_host.history
    if hist.has_changes():
        return True
    hist = inspected_obj.attrs.desired_sync_host.history
    if hist.has_changes():
        return True
    hist = inspected_obj.attrs.sync_should_run.history
    return hist.has_changes()


def already_registered_listener(obj) -> bool:  # type: ignore[no-untyped-def]
    return getattr(obj, "_listener_state", None) is not None


def update_listener_state(obj) -> None:  # type: ignore[no-untyped-def]
    obj._listener_state["sync_should_run"] = obj.sync_should_run
    obj._listener_state["sync_host"] = obj.sync_host
    obj._listener_state["desired_sync_host"] = obj.desired_sync_host
    obj._listener_state["sent_event"] = False


@event.listens_for(Session, "after_flush")
def after_flush(  # type: ignore[no-untyped-def]
    session, flush_context
) -> None:
    from inbox.mailsync.service import (
        SYNC_EVENT_QUEUE_NAME,
        shared_sync_event_queue_for_zone,
    )

    def send_migration_events(obj_state):  # type: ignore[no-untyped-def]
        def f(session) -> None:  # type: ignore[no-untyped-def]
            if obj_state["sent_event"]:
                return

            id = obj_state["id"]  # noqa: A001
            sync_should_run = obj_state["sync_should_run"]
            sync_host = obj_state["sync_host"]
            desired_sync_host = obj_state["desired_sync_host"]

            try:
                if sync_host is not None:
                    # Somebody is actively syncing this Account, so notify them if
                    # they should give up the Account.
                    if not sync_should_run or (
                        sync_host != desired_sync_host
                        and desired_sync_host is not None
                    ):
                        queue_name = SYNC_EVENT_QUEUE_NAME.format(sync_host)
                        log.info(
                            "Sending 'migrate_from' event for Account",
                            account_id=id,
                            queue_name=queue_name,
                        )
                        EventQueue(queue_name).send_event(
                            {"event": "migrate_from", "id": id}
                        )
                    return

                if not sync_should_run:
                    # We don't need to notify anybody because the Account is not
                    # actively being synced (sync_host is None) and sync_should_run is False,
                    # so just return early.
                    return

                if desired_sync_host is not None:
                    # Nobody is actively syncing the Account, and we have somebody
                    # who wants to sync this Account, so notify them.
                    queue_name = SYNC_EVENT_QUEUE_NAME.format(
                        desired_sync_host
                    )
                    log.info(
                        "Sending 'migrate_to' event for Account",
                        account_id=id,
                        queue_name=queue_name,
                    )
                    EventQueue(queue_name).send_event(
                        {"event": "migrate_to", "id": id}
                    )
                    return

                # Nobody is actively syncing the Account, and nobody in particular
                # wants to sync the Account so notify the shared queue.
                shared_queue = shared_sync_event_queue_for_zone(
                    config.get("ZONE")
                )
                log.info(
                    "Sending 'migrate' event for Account",
                    account_id=id,
                    queue_name=shared_queue.queue_name,
                )
                shared_queue.send_event({"event": "migrate", "id": id})
                obj_state["sent_event"] = True
            except Exception:
                log.exception(
                    "Uncaught error",
                    account_id=id,
                    sync_host=sync_host,
                    desired_sync_host=desired_sync_host,
                )

        return f

    for obj in session.new:
        if isinstance(obj, Account):
            if already_registered_listener(obj):
                update_listener_state(obj)
            else:
                obj._listener_state = {  # type: ignore[attr-defined]
                    "id": obj.id
                }
                update_listener_state(obj)
                event.listen(
                    session,
                    "after_commit",
                    send_migration_events(
                        obj._listener_state  # type: ignore[attr-defined]
                    ),
                )

    for obj in session.dirty:
        if not session.is_modified(obj):
            continue
        if should_send_event(obj):
            if already_registered_listener(obj):
                update_listener_state(obj)
            else:
                obj._listener_state = {"id": obj.id}
                update_listener_state(obj)
                event.listen(
                    session,
                    "after_commit",
                    send_migration_events(obj._listener_state),
                )


Index(
    "ix_account_sync_should_run_sync_host",
    Account.sync_should_run,
    Account.sync_host,
    mysql_length={"sync_host": 191},
)
