import datetime

from sqlalchemy import func
from sqlalchemy.orm import load_only
from sqlalchemy.orm.exc import ObjectDeletedError

from inbox import interruptible_threading
from inbox.crispin import connection_pool
from inbox.interruptible_threading import InterruptibleThread
from inbox.logging import get_logger
from inbox.mailsync.backends.imap import common
from inbox.mailsync.backends.imap.generic import uidvalidity_cb
from inbox.models import Message, Thread
from inbox.models.category import EPOCH, Category
from inbox.models.folder import Folder
from inbox.models.message import MessageCategory
from inbox.models.session import session_scope
from inbox.models.util import delete_message_hashes
from inbox.util.concurrency import retry_with_logging
from inbox.util.debug import bind_context
from inbox.util.itert import chunk

log = get_logger()

DEFAULT_MESSAGE_TTL = 2 * 60  # 2 minutes
DEFAULT_THREAD_TTL = 60 * 60 * 24 * 7  # 7 days
MAX_FETCH = 1000


class DeleteHandler(InterruptibleThread):
    """
    We don't outright delete message objects when all their associated
    uids are deleted. Instead, we mark them by setting a deleted_at
    timestamp. This is so that we can identify when a message is moved between
    folders, or when a draft is updated.

    This class is responsible for periodically checking for marked messages,
    and deleting them for good if they've been marked as deleted for longer
    than message_ttl seconds.

    It also periodically deletes categories which have no associated messages.

    Parameters
    ----------
    account_id, namespace_id: int
        IDs for the namespace to check.
    uid_accessor: function
        Function that takes a message and returns a list of associated uid
        objects. For IMAP sync, this would just be
        `uid_accessor=lambda m: m.imapuids`
    message_ttl: int
        Number of seconds to wait after a message is marked for deletion before
        deleting it for good.

    """

    def __init__(
        self,
        account_id,
        namespace_id,
        provider_name,
        uid_accessor,
        message_ttl=DEFAULT_MESSAGE_TTL,
        thread_ttl=DEFAULT_THREAD_TTL,
    ) -> None:
        bind_context(self, "deletehandler", account_id)
        self.account_id = account_id
        self.namespace_id = namespace_id
        self.provider_name = provider_name
        self.uids_for_message = uid_accessor
        self.log = log.new(account_id=account_id)
        self.message_ttl = datetime.timedelta(seconds=message_ttl)
        self.thread_ttl = datetime.timedelta(seconds=thread_ttl)

        super().__init__()

        self.name = f"{self.__class__.__name__}(account_id={account_id!r})"

    def _run(self) -> None:
        while True:
            interruptible_threading.check_interrupted()
            retry_with_logging(
                self._run_impl,
                account_id=self.account_id,
                provider=self.provider_name,
            )

    def _run_impl(self) -> None:
        current_time = datetime.datetime.utcnow()
        self.check(current_time)
        self.gc_deleted_categories()
        self.gc_deleted_threads(current_time)
        interruptible_threading.sleep(self.message_ttl.total_seconds())

    def check(self, current_time) -> None:
        dangling_sha256s = set()

        with session_scope(self.namespace_id) as db_session:
            dangling_messages = (
                db_session.query(Message)
                .filter(
                    Message.namespace_id == self.namespace_id,
                    Message.deleted_at <= current_time - self.message_ttl,
                )
                .limit(MAX_FETCH)
            )
            for message in dangling_messages:
                # If the message isn't *actually* dangling (i.e., it has
                # imapuids associated with it), undelete it.
                try:
                    uids_for_message = self.uids_for_message(message)
                except ObjectDeletedError:
                    # It looks like we are expiring the session potentially when one message is deleted,
                    # and then when accessing the IMAP uids, there is a lazy load trying to get the data.
                    # If that object has also been deleted (how?) it raises this exception.
                    continue

                if uids_for_message:
                    message.deleted_at = None
                    continue

                thread = message.thread

                if not thread or message not in thread.messages:
                    self.log.warning(
                        "Running delete handler check but message"
                        " is not part of referenced thread: {}",
                        thread_id=thread.id,
                    )
                    # Nothing to check
                    continue

                # Remove message from thread, so that the change to the thread
                # gets properly versioned.
                thread.messages.remove(message)
                # Thread.messages relationship is versioned i.e. extra
                # logic gets executed on remove call.
                # This early flush is needed so the configure_versioning logic
                # in inbox.model.sessions can work reliably on newer versions of
                # SQLAlchemy.
                db_session.flush()

                # Also need to explicitly delete, so that message shows up in
                # db_session.deleted.
                db_session.delete(message)

                dangling_sha256s.add(message.data_sha256)

                if not thread.messages:
                    # We don't eagerly delete empty Threads because there's a
                    # race condition between deleting a Thread and creating a
                    # new Message that refers to the old deleted Thread.
                    thread.mark_for_deletion()
                else:
                    # TODO(emfree): This is messy. We need better
                    # abstractions for recomputing a thread's attributes
                    # from messages, here and in mail sync.
                    non_draft_messages = [
                        m for m in thread.messages if not m.is_draft
                    ]
                    if not non_draft_messages:
                        continue
                    # The value of thread.messages is ordered oldest-to-newest.
                    first_message = non_draft_messages[0]
                    last_message = non_draft_messages[-1]
                    thread.subject = first_message.subject
                    thread.subjectdate = first_message.received_date
                    thread.recentdate = last_message.received_date
                    thread.snippet = last_message.snippet
                # YES this is at the right indentation level. Delete statements
                # may cause InnoDB index locks to be acquired, so we opt to
                # simply commit after each delete in order to prevent bulk
                # delete scenarios from creating a long-running, blocking
                # transaction.
                db_session.commit()

        delete_message_hashes(
            self.namespace_id, self.account_id, dangling_sha256s
        )

    def gc_deleted_categories(self) -> None:
        # Delete categories which have been deleted on the backend.
        # Go through all the categories and check if there are messages
        # associated with it. If not, delete it.
        with session_scope(self.namespace_id) as db_session:
            categories = db_session.query(Category).filter(
                Category.namespace_id == self.namespace_id,
                Category.deleted_at > EPOCH,
            )

            for category in categories:
                # Check if no message is associated with the category. If yes,
                # delete it.
                count = (
                    db_session.query(func.count(MessageCategory.id))
                    .filter(MessageCategory.category_id == category.id)
                    .scalar()
                )

                if count == 0:
                    db_session.delete(category)
                    db_session.commit()

    def gc_deleted_threads(self, current_time) -> None:
        with session_scope(self.namespace_id) as db_session:
            deleted_threads = (
                db_session.query(Thread)
                .filter(
                    Thread.namespace_id == self.namespace_id,
                    Thread.deleted_at <= current_time - self.thread_ttl,
                )
                .limit(MAX_FETCH)
            )
            for thread in deleted_threads:
                if thread.messages:
                    thread.deleted_at = None
                    db_session.commit()
                    continue
                db_session.delete(thread)
                db_session.commit()

    def __repr__(self) -> str:
        return f"<{self.name}>"


class LabelRenameHandler(InterruptibleThread):
    """
    Gmail has a long-standing bug where it won't notify us
    of a label rename (https://stackoverflow.com/questions/19571456/how-imap-client-can-detact-gmail-label-rename-programmatically).

    Because of this, we manually refresh the labels for all the UIDs in
    this label. To do this, we select all the folders we sync and run a search
    for the uids holding the new label.

    This isn't elegant but it beats having to issue a complex query to the db.

    """

    def __init__(
        self, account_id, namespace_id, label_name, semaphore
    ) -> None:
        bind_context(self, "renamehandler", account_id)
        self.account_id = account_id
        self.namespace_id = namespace_id
        self.label_name = label_name
        self.log = log.new(account_id=account_id)
        self.semaphore = semaphore

        super().__init__()

        self.name = f"{self.__class__.__name__}(account_id={account_id!r}, label_name={label_name!r})"

    def _run(self):
        interruptible_threading.check_interrupted()
        return retry_with_logging(self._run_impl, account_id=self.account_id)

    def _run_impl(self) -> None:
        self.log.info(
            "Starting LabelRenameHandler", label_name=self.label_name
        )

        with (
            self.semaphore,
            connection_pool(self.account_id).get() as crispin_client,
        ):
            folder_names = []
            with session_scope(self.account_id) as db_session:
                folders = db_session.query(Folder).filter(
                    Folder.account_id == self.account_id
                )

                folder_names = [folder.name for folder in folders]
                db_session.expunge_all()

            for folder_name in folder_names:
                crispin_client.select_folder(folder_name, uidvalidity_cb)

                found_uids = crispin_client.search_uids(
                    ["X-GM-LABELS", self.label_name]
                )

                for chnk in chunk(found_uids, 200):
                    flags = crispin_client.flags(chnk)

                    self.log.info(
                        "Running metadata update for folder",
                        folder_name=folder_name,
                    )
                    with session_scope(self.account_id) as db_session:
                        fld = (
                            db_session.query(Folder)
                            .options(load_only("id"))
                            .filter(
                                Folder.account_id == self.account_id,
                                Folder.name == folder_name,
                            )
                            .one()
                        )

                        common.update_metadata(
                            self.account_id,
                            fld.id,
                            fld.canonical_name,
                            flags,
                            db_session,
                        )
                        db_session.commit()

    def __repr__(self) -> str:
        return f"<{self.name}>"
