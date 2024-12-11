from hashlib import sha256

from flanker import mime
from sqlalchemy import (
    Boolean,
    Column,
    Enum,
    ForeignKey,
    Integer,
    String,
    event,
)
from sqlalchemy.orm import backref, reconstructor, relationship
from sqlalchemy.schema import UniqueConstraint
from sqlalchemy.sql.expression import false

from inbox.config import config
from inbox.logging import get_logger
from inbox.models.base import MailSyncBase
from inbox.models.message import Message
from inbox.models.mixins import (
    DeletedAtMixin,
    HasPublicID,
    HasRevisions,
    UpdatedAtMixin,
)
from inbox.s3.base import get_raw_from_provider
from inbox.util import blockstore
from inbox.util.stats import statsd_client

log = get_logger()

# TODO: store AWS credentials in a better way.
STORE_MSG_ON_S3 = config.get("STORE_MESSAGES_ON_S3", None)
STORE_MESSAGE_ATTACHMENTS = config.get("STORE_MESSAGE_ATTACHMENTS", True)

# These are the top 15 most common Content-Type headers
# in my personal mail archive. --mg
COMMON_CONTENT_TYPES = [
    "text/plain",
    "text/html",
    "multipart/alternative",
    "multipart/mixed",
    "image/jpeg",
    "multipart/related",
    "application/pdf",
    "image/png",
    "image/gif",
    "application/octet-stream",
    "multipart/signed",
    "application/msword",
    "application/pkcs7-signature",
    "message/rfc822",
    "image/jpg",
]


class Block(
    MailSyncBase, HasRevisions, HasPublicID, UpdatedAtMixin, DeletedAtMixin
):
    """Metadata for any file that we store"""

    API_OBJECT_NAME = "file"

    @property
    def should_suppress_transaction_creation(self):
        # Only version attachments
        return not any(part.is_attachment for part in self.parts)

    from inbox.models.namespace import Namespace

    data_sha256 = Column(String(64))
    size = Column(Integer, default=0)

    # Save some space with common content types
    _content_type_common = Column(Enum(*COMMON_CONTENT_TYPES))
    _content_type_other = Column(String(255))
    filename = Column(String(255))

    # TODO: create a constructor that allows the 'content_type' keyword
    def __init__(self, *args, **kwargs):
        self.content_type = None
        self.size = 0
        MailSyncBase.__init__(self, *args, **kwargs)

    namespace_id = Column(
        ForeignKey(Namespace.id, ondelete="CASCADE"), nullable=False
    )
    namespace = relationship(
        "Namespace",
        backref=backref(
            "blocks", passive_deletes=True, cascade="all,delete-orphan"
        ),
        load_on_pending=True,
    )

    @reconstructor
    def init_on_load(self):
        if self._content_type_common:
            self.content_type = self._content_type_common
        else:
            self.content_type = self._content_type_other

    @property
    def data(self):
        value: bytes | None
        if self.size == 0:
            log.warning("Block size is 0")
            return ""
        elif hasattr(self, "_data"):
            # On initial download we temporarily store data in memory
            value = self._data
        else:
            value = blockstore.get_from_blockstore(self.data_sha256)

        if value is None:
            log.warning(
                "Couldn't find data on S3 for block", sha_hash=self.data_sha256
            )

            from inbox.models.block import Block

            if isinstance(self, Block) and self.parts:
                # This block is an attachment of a message that was
                # deleted. We will attempt to fetch the raw
                # message and parse out the needed attachment.

                message = self.parts[0].message  # only grab one
                account = message.namespace.account

                statsd_string = (
                    f"api.direct_fetching.{account.provider}.{account.id}"
                )

                # Try to fetch the message from S3 first.
                with statsd_client.timer(
                    f"{statsd_string}.blockstore_latency"
                ):
                    raw_mime = blockstore.get_raw_mime(message.data_sha256)

                # If it's not there, get it from the provider.
                if raw_mime is None:
                    statsd_client.incr(f"{statsd_string}.cache_misses")

                    with statsd_client.timer(
                        f"{statsd_string}.provider_latency"
                    ):
                        raw_mime = get_raw_from_provider(message)

                    msg_sha256 = sha256(raw_mime).hexdigest()

                    # Cache the raw message in the blockstore so that
                    # we don't have to fetch it over and over.

                    with statsd_client.timer(
                        f"{statsd_string}.blockstore_save_latency"
                    ):
                        blockstore.save_to_blockstore(msg_sha256, raw_mime)
                else:
                    # We found it in the blockstore --- report this.
                    statsd_client.incr(f"{statsd_string}.cache_hits")

                # If we couldn't find it there, give up.
                if raw_mime is None:
                    log.error(
                        f"Don't have raw message for hash {message.data_sha256}"
                    )
                    return None

                parsed = mime.from_string(raw_mime)
                if parsed is not None:
                    for mimepart in parsed.walk(
                        with_self=parsed.content_type.is_singlepart()
                    ):
                        if mimepart.content_type.is_multipart():
                            continue  # TODO should we store relations?

                        data = mimepart.body

                        if data is None:
                            continue

                        if not isinstance(data, bytes):
                            data = data.encode("utf-8", "strict")

                        # Found it!
                        if sha256(data).hexdigest() == self.data_sha256:
                            log.info(
                                f"Found subpart with hash {self.data_sha256}"
                            )

                            with statsd_client.timer(
                                f"{statsd_string}.blockstore_save_latency"
                            ):
                                blockstore.save_to_blockstore(
                                    self.data_sha256, data
                                )
                                return data
                log.error(
                    "Couldn't find the attachment in the raw message",
                    message_id=message.id,
                )

            log.error("No data returned!")
            return value

        assert (
            self.data_sha256 == sha256(value).hexdigest()
        ), "Returned data doesn't match stored hash!"
        return value

    @data.setter
    def data(self, value):
        assert value is not None
        assert isinstance(value, bytes)

        # Cache value in memory. Otherwise message-parsing incurs a disk or S3
        # roundtrip.
        self._data = value
        self.size = len(value)
        self.data_sha256 = sha256(value).hexdigest()
        assert self.data_sha256

        if len(value) == 0:
            log.warning("Not saving 0-length data blob")
            return

        if STORE_MESSAGE_ATTACHMENTS:
            blockstore.save_to_blockstore(self.data_sha256, value)


@event.listens_for(Block, "before_insert", propagate=True)
def serialize_before_insert(mapper, connection, target):
    if target.content_type in COMMON_CONTENT_TYPES:
        target._content_type_common = target.content_type
        target._content_type_other = None
    else:
        target._content_type_common = None
        target._content_type_other = target.content_type


class Part(MailSyncBase, UpdatedAtMixin, DeletedAtMixin):
    """
    Part is a section of a specific message. This includes message bodies
    as well as attachments.
    """

    block_id = Column(ForeignKey(Block.id, ondelete="CASCADE"))
    block = relationship(
        Block,
        backref=backref(
            "parts", passive_deletes=True, cascade="all,delete,delete-orphan"
        ),
        load_on_pending=True,
    )

    message_id = Column(ForeignKey(Message.id, ondelete="CASCADE"))
    message = relationship(
        "Message",
        backref=backref(
            "parts", passive_deletes=True, cascade="all,delete,delete-orphan"
        ),
        load_on_pending=True,
    )

    walk_index = Column(Integer)
    # https://www.ietf.org/rfc/rfc2183.txt
    content_disposition = Column(Enum("inline", "attachment"), nullable=True)
    content_id = Column(String(255))  # For attachments

    is_inboxapp_attachment = Column(Boolean, server_default=false())

    __table_args__ = (UniqueConstraint("message_id", "walk_index"),)

    @property
    def thread_id(self):
        if not self.message:
            return None
        return self.message.thread_id

    @property
    def is_attachment(self):
        return self.content_disposition is not None

    @property
    def is_embedded(self):
        return (
            self.content_disposition is not None
            and self.content_disposition.lower() == "inline"
        )
