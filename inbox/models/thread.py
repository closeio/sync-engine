import datetime
import itertools
from collections import defaultdict

from sqlalchemy import (  # type: ignore[import-untyped]
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
)
from sqlalchemy.orm import (  # type: ignore[import-untyped]
    backref,
    object_session,
    relationship,
    subqueryload,
    validates,
)

from inbox.logging import get_logger
from inbox.models.base import MailSyncBase
from inbox.models.mixins import (
    DeletedAtMixin,
    HasPublicID,
    HasRevisions,
    UpdatedAtMixin,
)
from inbox.models.namespace import Namespace
from inbox.util.misc import cleanup_subject

log = get_logger()


class Thread(
    MailSyncBase, HasPublicID, HasRevisions, UpdatedAtMixin, DeletedAtMixin
):
    """
    Threads are a first-class object in Nylas. This thread aggregates
    the relevant thread metadata from elsewhere so that clients can only
    query on threads.

    A thread can be a member of an arbitrary number of folders.

    If you're attempting to display _all_ messages a la Gmail's All Mail,
    don't query based on folder!

    """

    API_OBJECT_NAME = "thread"  # type: ignore[assignment]

    namespace_id = Column(
        ForeignKey(Namespace.id, ondelete="CASCADE"), nullable=False
    )
    namespace = relationship(
        "Namespace",
        backref=backref("threads", passive_deletes=True),
        load_on_pending=True,
    )

    subject = Column(String(255), nullable=True)
    # a column with the cleaned up version of a subject string, to speed up
    # threading queries.
    _cleaned_subject = Column(String(255), nullable=True)
    subjectdate = Column(DateTime, nullable=False, index=True)
    recentdate = Column(DateTime, nullable=False, index=True)
    snippet = Column(String(191), nullable=True, default="")
    version = Column(Integer, nullable=True, server_default="0")

    @validates("subject")
    def compute_cleaned_up_subject(  # type: ignore[no-untyped-def]  # noqa: ANN201
        self, key, value
    ):
        self._cleaned_subject = cleanup_subject(value)
        return value

    @validates("messages")
    def update_from_message(self, k, message):  # type: ignore[no-untyped-def]  # noqa: ANN201
        with object_session(self).no_autoflush:
            if message.is_draft:
                # Don't change subjectdate, recentdate, or unread/unseen based
                # on drafts
                return message

            if message.received_date > self.recentdate:
                self.recentdate = message.received_date
                self.snippet = message.snippet

            # Subject is subject of original message in the thread
            if message.received_date < self.subjectdate:
                self.subject = message.subject
                self.subjectdate = message.received_date
            return message

    @property
    def most_recent_received_date(self):  # type: ignore[no-untyped-def]  # noqa: ANN201
        received_recent_date: datetime.datetime | None = None
        for m in self.messages:  # type: ignore[attr-defined]
            if (
                all(
                    category.name != "sent"
                    for category in m.categories
                    if category is not None
                )
                and not m.is_draft
                and not m.is_sent
                and (
                    not received_recent_date
                    or m.received_date > received_recent_date
                )
            ):
                received_recent_date = m.received_date

        if not received_recent_date:
            sorted_messages = sorted(
                self.messages,  # type: ignore[attr-defined]
                key=lambda m: m.received_date,
            )
            if not sorted_messages:
                log.warning(
                    "Thread does not have associated messages",
                    thread_id=self.id,
                )
                return None
            received_recent_date = sorted_messages[-1].received_date

        return received_recent_date

    @property
    def most_recent_sent_date(self):  # type: ignore[no-untyped-def]  # noqa: ANN201
        """
        This is the timestamp of the most recently *sent* message on this
        thread, as decided by whether the message is in the sent folder or
        not. Clients can use this to properly sort the Sent view.
        """  # noqa: D404
        sent_recent_date = None
        sorted_messages = sorted(
            self.messages,  # type: ignore[attr-defined]
            key=lambda m: m.received_date,
            reverse=True,
        )
        for message in sorted_messages:
            if "sent" in [
                category.name for category in message.categories if category
            ] or (message.is_draft and message.is_sent):
                sent_recent_date = message.received_date
                return sent_recent_date
        return None

    @property
    def unread(self) -> bool:
        return not all(
            m.is_read
            for m in self.messages  # type: ignore[attr-defined]
            if not m.is_draft
        )

    @property
    def starred(self):  # type: ignore[no-untyped-def]  # noqa: ANN201
        return any(
            m.is_starred
            for m in self.messages  # type: ignore[attr-defined]
            if not m.is_draft
        )

    @property
    def has_attachments(self):  # type: ignore[no-untyped-def]  # noqa: ANN201
        return any(
            m.attachments
            for m in self.messages  # type: ignore[attr-defined]
            if not m.is_draft
        )

    @property
    def versioned_relationships(self):  # type: ignore[no-untyped-def]  # noqa: ANN201
        return ["messages"]

    @property
    def participants(self):  # type: ignore[no-untyped-def]  # noqa: ANN201
        """
        Different messages in the thread may reference the same email
        address with different phrases. We partially deduplicate: if the same
        email address occurs with both empty and nonempty phrase, we don't
        separately return the (empty phrase, address) pair.

        """
        deduped_participants = defaultdict(set)
        for m in self.messages:  # type: ignore[attr-defined]
            if m.is_draft:
                # Don't use drafts to compute participants.
                continue
            for phrase, address in itertools.chain(
                m.from_addr, m.to_addr, m.cc_addr, m.bcc_addr
            ):
                deduped_participants[address].add(phrase.strip())
        p = []
        for address, phrases in deduped_participants.items():
            for phrase in phrases:
                if phrase != "" or len(phrases) == 1:
                    p.append((phrase, address))
        return p

    @property
    def drafts(self):  # type: ignore[no-untyped-def]  # noqa: ANN201
        """
        Return all drafts on this thread that don't have later revisions.

        """
        return [
            m
            for m in self.messages  # type: ignore[attr-defined]
            if m.is_draft
        ]

    @property
    def attachments(self):  # type: ignore[no-untyped-def]  # noqa: ANN201
        return any(
            m.attachments for m in self.messages  # type: ignore[attr-defined]
        )

    @property
    def account(self):  # type: ignore[no-untyped-def]  # noqa: ANN201
        return self.namespace.account

    @property
    def categories(self):  # type: ignore[no-untyped-def]  # noqa: ANN201
        categories = set()
        for m in self.messages:  # type: ignore[attr-defined]
            categories.update(m.categories)
        return categories

    @classmethod
    def api_loading_options(  # type: ignore[no-untyped-def]  # noqa: ANN206
        cls, expand: bool = False
    ):
        message_columns = [
            "public_id",
            "is_draft",
            "from_addr",
            "to_addr",
            "cc_addr",
            "bcc_addr",
            "is_read",
            "is_starred",
            "received_date",
            "is_sent",
        ]
        if expand:
            message_columns += [
                "subject",
                "snippet",
                "version",
                "from_addr",
                "to_addr",
                "cc_addr",
                "bcc_addr",
                "reply_to",
            ]
        return (
            subqueryload(Thread.messages)  # type: ignore[attr-defined]
            .load_only(*message_columns)
            .joinedload("messagecategories")
            .joinedload("category"),
            subqueryload(Thread.messages)  # type: ignore[attr-defined]
            .joinedload("parts")
            .joinedload("block"),
        )

    def mark_for_deletion(self) -> None:
        """
        Mark this message to be deleted by an asynchronous delete
        handler.

        """
        self.deleted_at = datetime.datetime.utcnow()

    discriminator = Column("type", String(16))
    __mapper_args__ = {
        "polymorphic_on": discriminator,
        "polymorphic_identity": "thread",
    }


# Need to explicitly specify the index length for MySQL 5.6, because the
# subject column is too long to be fully indexed with utf8mb4 collation.
Index("ix_thread_subject", Thread.subject, mysql_length=80)

# For async deletion.
Index(
    "ix_thread_namespace_id_deleted_at", Thread.namespace_id, Thread.deleted_at
)

# For fetch_corresponding_thread.
Index(
    "ix_namespace_id__cleaned_subject",
    Thread.namespace_id,
    Thread._cleaned_subject,
    mysql_length={"_cleaned_subject": 80},
)
