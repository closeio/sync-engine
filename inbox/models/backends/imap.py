import json
from datetime import datetime
from typing import List, Set

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    desc,
)
from sqlalchemy.ext.associationproxy import association_proxy
from sqlalchemy.orm import backref, object_session, relationship
from sqlalchemy.schema import UniqueConstraint
from sqlalchemy.sql.expression import false

from inbox.logging import get_logger
from inbox.models.account import Account
from inbox.models.base import MailSyncBase
from inbox.models.category import Category
from inbox.models.folder import Folder
from inbox.models.label import Label
from inbox.models.message import Message
from inbox.models.mixins import DeletedAtMixin, HasRunState, UpdatedAtMixin
from inbox.models.thread import Thread
from inbox.sqlalchemy_ext.util import JSON, LittleJSON, MutableDict
from inbox.util.misc import cleanup_subject

log = get_logger()

PROVIDER = "imap"


# Note, you should never directly create ImapAccount objects. Instead you
# should use objects that inherit from this, such as GenericAccount or
# GmailAccount
class ImapAccount(Account):
    id = Column(ForeignKey(Account.id, ondelete="CASCADE"), primary_key=True)

    _imap_server_host = Column(String(255), nullable=True)
    _imap_server_port = Column(Integer, nullable=False, server_default="993")

    _smtp_server_host = Column(String(255), nullable=True)
    _smtp_server_port = Column(Integer, nullable=False, server_default="587")

    @property
    def imap_endpoint(self):
        if self._imap_server_host is not None:
            # We have to take care to coerce to int here and below, because
            # mysqlclient returns Integer columns as type long, and
            # socket.getaddrinfo in older versions of Python 2.7 fails to
            # handle ports of type long. Yay. http://bugs.python.org/issue8853.
            return (self._imap_server_host, int(self._imap_server_port))
        else:
            return self.provider_info["imap"]

    @imap_endpoint.setter
    def imap_endpoint(self, endpoint):
        host, port = endpoint
        self._imap_server_host = host
        self._imap_server_port = int(port)

    @property
    def smtp_endpoint(self):
        if self._smtp_server_host is not None:
            return (self._smtp_server_host, int(self._smtp_server_port))
        else:
            return self.provider_info["smtp"]

    @smtp_endpoint.setter
    def smtp_endpoint(self, endpoint):
        host, port = endpoint
        self._smtp_server_host = host
        self._smtp_server_port = int(port)

    def get_raw_message_contents(self, message):
        from inbox.s3.backends.imap import get_imap_raw_contents

        return get_imap_raw_contents(message)

    __mapper_args__ = {"polymorphic_identity": "imapaccount"}


class ImapUid(MailSyncBase, UpdatedAtMixin, DeletedAtMixin):
    """
    Maps UIDs to their IMAP folders and per-UID flag metadata.
    This table is used solely for bookkeeping by the IMAP mail sync backends.

    """

    account_id = Column(
        ForeignKey(ImapAccount.id, ondelete="CASCADE"), nullable=False
    )
    account = relationship(ImapAccount)

    message_id = Column(
        ForeignKey(Message.id, ondelete="CASCADE"), nullable=False
    )
    message = relationship(
        Message, backref=backref("imapuids", passive_deletes=True)
    )
    msg_uid = Column(BigInteger, nullable=False, index=True)

    folder_id = Column(
        ForeignKey(Folder.id, ondelete="CASCADE"), nullable=False
    )
    # We almost always need the folder name too, so eager load by default.
    folder = relationship(
        Folder,
        lazy="joined",
        backref=backref("imapuids", passive_deletes=True),
    )

    labels = association_proxy(
        "labelitems", "label", creator=lambda label: LabelItem(label=label)
    )

    # Flags #
    # Message has not completed composition (marked as a draft).
    is_draft = Column(Boolean, server_default=false(), nullable=False)
    # Message has been read
    is_seen = Column(Boolean, server_default=false(), nullable=False)
    # Message is "flagged" for urgent/special attention
    is_flagged = Column(Boolean, server_default=false(), nullable=False)
    # session is the first session to have been notified about this message
    is_recent = Column(Boolean, server_default=false(), nullable=False)
    # Message has been answered
    is_answered = Column(Boolean, server_default=false(), nullable=False)
    # things like: ['$Forwarded', 'nonjunk', 'Junk']
    extra_flags = Column(LittleJSON, default=[], nullable=False)
    # labels (Gmail-specific)
    # TO BE DEPRECATED
    g_labels = Column(JSON, default=list, nullable=True)

    def update_flags(self, new_flags: List[bytes]) -> None:
        """
        Sets flag and g_labels values based on the new_flags and x_gm_labels
        parameters. Returns True if any values have changed compared to what we
        previously stored.

        """
        changed = False
        new_flags = {flag.decode() for flag in new_flags}
        columns_for_flag = {
            "\\Draft": "is_draft",
            "\\Seen": "is_seen",
            "\\Recent": "is_recent",
            "\\Answered": "is_answered",
            "\\Flagged": "is_flagged",
        }
        for flag, column in columns_for_flag.items():
            prior_column_value = getattr(self, column)
            new_column_value = flag in new_flags
            if prior_column_value != new_column_value:
                changed = True
                setattr(self, column, new_column_value)
            new_flags.discard(flag)

        extra_flags = sorted(new_flags)

        if extra_flags != self.extra_flags:
            changed = True

        # Sadly, there's a limit of 255 chars for this
        # column.
        while len(json.dumps(extra_flags)) > 255:
            extra_flags.pop()

        self.extra_flags = extra_flags
        return changed

    def update_labels(self, new_labels: List[str]) -> None:
        # TODO(emfree): This is all mad complicated. Simplify if possible?

        # Gmail IMAP doesn't use the normal IMAP \\Draft flag. Silly Gmail
        # IMAP.
        self.is_draft = "\\Draft" in new_labels
        self.is_starred = "\\Starred" in new_labels

        category_map = {
            "\\Inbox": "inbox",
            "\\Important": "important",
            "\\Sent": "sent",
            "\\Trash": "trash",
            "\\Spam": "spam",
            "\\All": "all",
        }

        remote_labels = set()
        for label in new_labels:
            if label in ("\\Draft", "\\Starred"):
                continue
            elif label in category_map:
                remote_labels.add((category_map[label], category_map[label]))
            else:
                remote_labels.add((label, None))

        local_labels = {
            (lbl.name, lbl.canonical_name): lbl for lbl in self.labels
        }

        remove = set(local_labels) - remote_labels
        add = remote_labels - set(local_labels)

        with object_session(self).no_autoflush:
            for key in remove:
                self.labels.remove(local_labels[key])

            for name, canonical_name in add:
                label = Label.find_or_create(
                    object_session(self), self.account, name, canonical_name
                )
                self.labels.add(label)

    @property
    def namespace(self):
        return self.imapaccount.namespace

    @property
    def categories(self) -> Set[Category]:
        categories = {label.category for label in self.labels}
        categories.add(self.folder.category)
        return categories

    __table_args__ = (
        UniqueConstraint("folder_id", "msg_uid", "account_id"),
        # This index is used to quickly retrieve IMAP uids
        # in local_uids and lastseenuid functions.
        # Those queries consistently stay in top 5 most busy SELECTs
        # and having dedicated index helps to reduce the load on the database
        # by 15% - 20%.
        Index(
            "ix_imapuid_account_id_folder_id_msg_uid_desc",
            account_id,
            folder_id,
            msg_uid.desc(),
            unique=True,
        ),
    )


# make pulling up all messages in a given folder fast
Index("account_id_folder_id", ImapUid.account_id, ImapUid.folder_id)


class ImapFolderInfo(MailSyncBase, UpdatedAtMixin, DeletedAtMixin):
    """
    Per-folder UIDVALIDITY and (if applicable) HIGHESTMODSEQ.

    If the UIDVALIDITY value changes, it indicates that all UIDs for messages
    in the folder need to be thrown away and resynced.

    These values come from the IMAP STATUS or SELECT commands.

    See http://tools.ietf.org/html/rfc3501#section-2.3.1.1 for more info
    on UIDVALIDITY, and http://tools.ietf.org/html/rfc4551 for more info on
    HIGHESTMODSEQ.

    """

    account_id = Column(
        ForeignKey(ImapAccount.id, ondelete="CASCADE"), nullable=False
    )
    account = relationship(ImapAccount)
    folder_id = Column(
        ForeignKey("folder.id", ondelete="CASCADE"), nullable=False
    )
    folder = relationship(
        "Folder",
        backref=backref("imapfolderinfo", uselist=False, passive_deletes=True),
    )
    uidvalidity = Column(BigInteger, nullable=False)
    # Invariant: the local datastore for this folder has always incorporated
    # remote changes up to _at least_ this modseq (we can't guarantee that we
    # haven't incorporated later changes too, since IMAP doesn't provide a true
    # transactional interface).
    #
    # Note that some IMAP providers do not support the CONDSTORE extension, and
    # therefore will not use this field.
    highestmodseq = Column(BigInteger, nullable=True)
    uidnext = Column(BigInteger, nullable=True)
    last_slow_refresh = Column(DateTime)

    __table_args__ = (UniqueConstraint("account_id", "folder_id"),)


def _choose_existing_thread_for_gmail(message, db_session):
    """
    For Gmail, determine if `message` should be added to an existing thread
    based on the value of `g_thrid`. If so, return the existing ImapThread
    object; otherwise return None.

    If a thread in Gmail (as identified by g_thrid) is split among multiple
    Nylas threads, try to choose which thread to put the new message in based
    on the In-Reply-To header. If that doesn't succeed because the In-Reply-To
    header is missing or doesn't match existing synced messages, return the
    most recent thread.

    """
    # TODO(emfree): also use the References header, or better yet, change API
    # semantics so that we don't have to do this at all.
    prior_threads = (
        db_session.query(ImapThread)
        .filter_by(g_thrid=message.g_thrid, namespace_id=message.namespace_id)
        .order_by(desc(ImapThread.recentdate))
        .all()
    )
    if not prior_threads:
        return None
    if len(prior_threads) == 1:
        return prior_threads[0]
    if not message.in_reply_to:
        # If no header, add the new message to the most recent thread.
        return prior_threads[0]
    for prior_thread in prior_threads:
        prior_message_ids = [
            m.message_id_header for m in prior_thread.messages
        ]
        if message.in_reply_to in prior_message_ids:
            return prior_thread

    return prior_threads[0]


class ImapThread(Thread):
    """TODO: split into provider-specific classes."""

    id = Column(ForeignKey(Thread.id, ondelete="CASCADE"), primary_key=True)

    # Only on messages from Gmail
    #
    # Gmail documents X-GM-THRID as 64-bit unsigned integer. Unique across
    # an account but not necessarily globally unique. The same message sent
    # to multiple users *may* have the same X-GM-THRID, but usually won't.
    g_thrid = Column(BigInteger, nullable=True, index=True, unique=False)

    @classmethod
    def from_gmail_message(cls, session, namespace_id, message):
        """
        Threads are broken solely on Gmail's X-GM-THRID for now. (Subjects
        are not taken into account, even if they change.)

        Returns the updated or new thread, and adds the message to the thread.
        Doesn't commit.

        """
        if message.thread is not None:
            # If this message *already* has a thread associated with it, just
            # update its g_thrid value.
            message.thread.g_thrid = message.g_thrid
            return message.thread
        if message.g_thrid is not None:
            thread = _choose_existing_thread_for_gmail(message, session)
            if thread is None:
                thread = cls(
                    subject=message.subject,
                    g_thrid=message.g_thrid,
                    recentdate=message.received_date,
                    namespace_id=namespace_id,
                    subjectdate=message.received_date,
                    snippet=message.snippet,
                )
        return thread

    @classmethod
    def from_imap_message(cls, session, namespace_id, message):
        if message.thread is not None:
            # If this message *already* has a thread associated with it, don't
            # create a new one.
            return message.thread
        clean_subject = cleanup_subject(message.subject)
        thread = cls(
            subject=clean_subject,
            recentdate=message.received_date,
            namespace_id=namespace_id,
            subjectdate=message.received_date,
            snippet=message.snippet,
        )
        return thread

    __mapper_args__ = {"polymorphic_identity": "imapthread"}


class ImapFolderSyncStatus(
    MailSyncBase, HasRunState, UpdatedAtMixin, DeletedAtMixin
):
    """Per-folder status state saving for IMAP folders."""

    account_id = Column(
        ForeignKey(ImapAccount.id, ondelete="CASCADE"), nullable=False
    )
    account = relationship(
        ImapAccount,
        backref=backref("foldersyncstatuses", passive_deletes=True),
    )

    folder_id = Column(
        ForeignKey("folder.id", ondelete="CASCADE"), nullable=False
    )
    # We almost always need the folder name too, so eager load by default.
    folder = relationship(
        "Folder",
        lazy="joined",
        backref=backref("imapsyncstatus", uselist=False, passive_deletes=True),
    )

    # see state machine in mailsync/backends/imap/imap.py
    state = Column(
        Enum(
            "initial",
            "initial uidinvalid",
            "poll",
            "poll uidinvalid",
            "finish",
        ),
        server_default="initial",
        nullable=False,
    )

    # stats on messages downloaded etc.
    _metrics = Column(MutableDict.as_mutable(JSON), default={}, nullable=True)

    @property
    def metrics(self):
        status = dict(name=self.folder.name, state=self.state)
        status.update(self._metrics or {})

        return status

    def start_sync(self):
        self._metrics = dict(
            run_state="running", sync_start_time=datetime.utcnow()
        )

    def stop_sync(self):
        self._metrics["run_state"] = "stopped"
        self._metrics["sync_end_time"] = datetime.utcnow()

    @property
    def is_killed(self):
        return self._metrics.get("run_state") == "killed"

    def update_metrics(self, metrics):
        sync_status_metrics = [
            "remote_uid_count",
            "delete_uid_count",
            "update_uid_count",
            "download_uid_count",
            "uid_checked_timestamp",
            "num_downloaded_since_timestamp",
            "queue_checked_at",
            "percent",
        ]

        assert isinstance(metrics, dict)
        for k in metrics:
            assert k in sync_status_metrics, k

        if self._metrics is not None:
            self._metrics.update(metrics)
        else:
            self._metrics = metrics

    @property
    def sync_enabled(self):
        # sync is enabled if the folder's run bit is set, and the account's
        # run bit is set. (this saves us needing to reproduce account-state
        # transition logic on the folder level, and gives us a comparison bit
        # against folder heartbeats.)
        return self.sync_should_run and self.account.sync_should_run

    __table_args__ = (UniqueConstraint("account_id", "folder_id"),)


class LabelItem(MailSyncBase, UpdatedAtMixin, DeletedAtMixin):
    """Mapping between imapuids and labels."""

    imapuid_id = Column(
        ForeignKey(ImapUid.id, ondelete="CASCADE"), nullable=False
    )
    imapuid = relationship(
        "ImapUid",
        backref=backref(
            "labelitems", collection_class=set, cascade="all, delete-orphan"
        ),
    )

    label_id = Column(ForeignKey(Label.id, ondelete="CASCADE"), nullable=False)
    label = relationship(
        Label,
        backref=backref(
            "labelitems", cascade="all, delete-orphan", lazy="dynamic"
        ),
    )

    @property
    def namespace(self):
        return self.label.namespace


Index("imapuid_label_ids", LabelItem.imapuid_id, LabelItem.label_id)
