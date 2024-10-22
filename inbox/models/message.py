import datetime
import itertools
import os
import threading
import typing
from collections import defaultdict
from hashlib import sha256
from typing import Any, DefaultDict, Dict, List, Optional, Set, Tuple, Union

from flanker import mime
from flanker.mime.message.part import MimePart
from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Enum,
    Index,
    Integer,
    String,
    bindparam,
)
from sqlalchemy.dialects.mysql import LONGBLOB, VARCHAR
from sqlalchemy.ext.associationproxy import association_proxy
from sqlalchemy.orm import (
    backref,
    joinedload,
    load_only,
    relationship,
    subqueryload,
    synonym,
    validates,
    with_polymorphic,
)
from sqlalchemy.sql.expression import false

from inbox.config import config
from inbox.constants import MAX_MESSAGE_BODY_LENGTH
from inbox.logging import get_logger
from inbox.models.account import Account
from inbox.models.base import MailSyncBase
from inbox.models.category import Category
from inbox.models.mixins import (
    DeletedAtMixin,
    HasPublicID,
    HasRevisions,
    UpdatedAtMixin,
)
from inbox.security.blobstorage import decode_blob, encode_blob
from inbox.sqlalchemy_ext.util import JSON, MAX_MYSQL_INTEGER, json_field_too_long
from inbox.util.addr import HeaderTooBigException, parse_mimepart_address_header
from inbox.util.blockstore import save_raw_mime
from inbox.util.encoding import unicode_safe_truncate
from inbox.util.html import HTMLParseError, plaintext2html, strip_tags
from inbox.util.misc import get_internaldate, parse_references

log = get_logger()

SNIPPET_LENGTH = 191

if typing.TYPE_CHECKING:
    from inbox.models.block import Part


email_parsing_lock = threading.BoundedSemaphore(value=1)


def _trim_filename(
    s: Optional[Union[str, bytes]], namespace_id: int, max_len: int = 255
) -> Optional[str]:
    if s is None:
        return s

    # The filename may be up to 255 4-byte unicode characters. If the
    # filename is longer than that, truncate it appropriately.

    # If `s` is not stored as a unicode string, but contains unicode
    # characters, len will return the wrong value (bytes not chars).
    # Convert it to unicode first.
    if isinstance(s, bytes):
        s = s.decode("utf-8", "ignore")

    if len(s) > max_len:
        # If we need to truncate the string, keep the extension
        filename, fileext = os.path.splitext(s)
        if len(fileext) < max_len - 1:
            return filename[: (max_len - len(fileext))] + fileext
        else:
            return filename[0] + fileext[: (max_len - 1)]

    return s


def normalize_data(data: str) -> str:
    return data.replace("\r\n", "\n").replace("\r", "\n")


class MessageTooBigException(Exception):
    def __init__(self, body_length):
        super().__init__(f"message length ({body_length}) is over the parsing limit")


class Message(MailSyncBase, HasRevisions, HasPublicID, UpdatedAtMixin, DeletedAtMixin):
    @property
    def API_OBJECT_NAME(self):
        return "message" if not self.is_draft else "draft"

    namespace_id = Column(BigInteger, index=True, nullable=False)
    namespace = relationship(
        "Namespace",
        primaryjoin="foreign(Message.namespace_id) == remote(Namespace.id)",
        load_on_pending=True,
    )

    # Do delete messages if their associated thread is deleted.
    thread_id = Column(BigInteger, index=True, nullable=False)
    _thread = relationship(
        "Thread",
        primaryjoin="foreign(Message.thread_id) == remote(Thread.id)",
        backref=backref(
            "messages", order_by="Message.received_date", cascade="all, delete-orphan"
        ),
    )

    @property
    def thread(self):
        return self._thread

    @thread.setter
    def thread(self, value):
        if value is not None and self._thread is not None:
            self._thread.deleted_at = None
        self._thread = value

    thread = synonym("_thread", descriptor=thread)

    from_addr = Column(JSON, nullable=False, default=list)
    sender_addr = Column(JSON, nullable=True)
    reply_to = Column(JSON, nullable=True, default=list)
    to_addr = Column(JSON, nullable=False, default=list)
    cc_addr = Column(JSON, nullable=False, default=list)
    bcc_addr = Column(JSON, nullable=False, default=list)
    in_reply_to = Column(JSON, nullable=True)
    # From: http://tools.ietf.org/html/rfc4130, section 5.3.3,
    # max message_id_header is 998 characters
    message_id_header = Column(String(998), nullable=True)
    # There is no hard limit on subject limit in the spec, but 255 is common.
    subject = Column(String(255), nullable=True, default="")
    received_date = Column(DateTime, nullable=False, index=True)
    size = Column(Integer, nullable=False)
    data_sha256 = Column(VARCHAR(64, charset="ascii"), nullable=True, index=True)

    is_read = Column(Boolean, server_default=false(), nullable=False)
    is_starred = Column(Boolean, server_default=false(), nullable=False)

    # For drafts (both Nylas-created and otherwise)
    is_draft = Column(Boolean, server_default=false(), nullable=False)
    is_sent = Column(Boolean, server_default=false(), nullable=False)

    # REPURPOSED
    state = Column(
        Enum(
            "draft",
            "sending",
            "sending failed",
            "sent",
            "actions_pending",
            "actions_committed",
        )
    )

    @property
    def is_sending(self):
        return self.version == MAX_MYSQL_INTEGER and not self.is_draft

    def mark_as_sending(self):
        if self.is_sent:
            raise ValueError("Cannot mark a sent message as sending")
        self.version = MAX_MYSQL_INTEGER
        self.is_draft = False
        self.regenerate_nylas_uid()

    @property
    def categories_changes(self):
        return self.state == "actions_pending"

    @categories_changes.setter
    def categories_changes(self, has_changes):
        if has_changes is True:
            self.state = "actions_pending"
        else:
            self.state = "actions_committed"

    _compacted_body = Column(LONGBLOB, nullable=True)
    snippet = Column(String(191), nullable=False)

    # this might be a mail-parsing bug, or just a message from a bad client
    decode_error = Column(Boolean, server_default=false(), nullable=False, index=True)

    # In accordance with JWZ (http://www.jwz.org/doc/threading.html)
    references = Column(JSON, nullable=True)

    # Only used for drafts.
    version = Column(Integer, nullable=False, server_default="0")

    # only on messages from Gmail (TODO: use different table)
    #
    # X-GM-MSGID is guaranteed unique across an account but not globally
    # across all Gmail.
    #
    # Messages between different accounts *may* have the same X-GM-MSGID,
    # but it's unlikely.
    #
    # (Gmail info from
    # http://mailman13.u.washington.edu/pipermail/imap-protocol/
    # 2014-July/002290.html.)
    g_msgid = Column(BigInteger, nullable=True, index=True, unique=False)
    g_thrid = Column(BigInteger, nullable=True, index=True, unique=False)

    # The uid as set in the X-INBOX-ID header of a sent message we create
    nylas_uid = Column(String(64), nullable=True, index=True, name="inbox_uid")

    def regenerate_nylas_uid(self) -> None:
        """
        The value of nylas_uid is simply the draft public_id and version,
        concatenated. Because the nylas_uid identifies the draft on the remote
        provider, we regenerate it on each draft revision so that we can delete
        the old draft and add the new one on the remote.
        """
        from inbox.sendmail.message import generate_message_id_header

        self.nylas_uid = f"{self.public_id}-{self.version}"
        self.message_id_header = generate_message_id_header(self.nylas_uid)

    categories = association_proxy(
        "messagecategories",
        "category",
        creator=lambda category: MessageCategory(category=category),
    )

    # FOR INBOX-CREATED MESSAGES:

    is_created = Column(Boolean, server_default=false(), nullable=False)

    # Whether this draft is a reply to an existing thread.
    is_reply = Column(Boolean)

    reply_to_message_id = Column(BigInteger, nullable=True, index=True)
    # The backref here is unused, but must be configured so that the child's
    # foreign key gets updated when the parent is deleted.
    reply_to_message = relationship(
        "Message",
        primaryjoin="foreign(Message.reply_to_message_id) == remote(Message.id)",
        backref="replies",
    )

    def mark_for_deletion(self) -> None:
        """
        Mark this message to be deleted by an asynchronous delete
        handler.

        """
        self.deleted_at = datetime.datetime.utcnow()

    @validates("subject")
    def sanitize_subject(self, key: Any, value: Optional[str]) -> Optional[str]:
        # Trim overlong subjects, and remove null bytes. The latter can result
        # when, for example, UTF-8 text decoded from an RFC2047-encoded header
        # contains null bytes.
        if value is None:
            return None
        value = unicode_safe_truncate(value, 255)
        value = value.replace("\0", "")
        return value

    @classmethod
    def create_from_synced(
        cls,
        account: Account,
        imap_uid: int,
        folder_name: str,
        received_date: Optional[datetime.datetime],
        body: bytes,
    ) -> "Message":
        """
        Parses message data and writes out db metadata and MIME blocks.

        Returns the new Message, which links to the new Part and Block objects
        through relationships. All new objects are uncommitted.

        Threads are not computed here; you gotta do that separately.

        Parameters
        ----------
        imap_uid : int
            The account backend-specific message identifier; it's only used for
            logging errors.

        body : bytes
            The full message including headers (encoded).

        """
        # stop trickle-down bugs
        assert account.namespace is not None
        assert isinstance(body, bytes)

        message = Message()

        message.data_sha256 = sha256(body).hexdigest()

        # Persist the raw MIME message to disk/ S3
        save_raw_mime(message.data_sha256, body)

        # Persist the processed message to the database
        message.namespace_id = account.namespace.id

        with email_parsing_lock:
            try:
                body_length = len(body)
                if body_length > MAX_MESSAGE_BODY_LENGTH:
                    raise MessageTooBigException(body_length)
                parsed: MimePart = mime.from_string(body)
                message._parse_metadata(
                    parsed, body, received_date, account.id, folder_name, imap_uid
                )
            except (
                mime.DecodingError,
                MessageTooBigException,
                HeaderTooBigException,
            ) as e:
                parsed = None
                log.warning(
                    "Error parsing message metadata",
                    folder_name=folder_name,
                    account_id=account.id,
                    error=e,
                    imap_uid=imap_uid,
                )
                message._mark_error()
            except Exception as e:
                parsed = None
                log.error(
                    "Error parsing message metadata",
                    folder_name=folder_name,
                    account_id=account.id,
                    error=e,
                    imap_uid=imap_uid,
                )
                message._mark_error()

            if parsed is not None:
                plain_parts: List[str] = []
                html_parts: List[str] = []
                for mimepart in parsed.walk(
                    with_self=parsed.content_type.is_singlepart()
                ):
                    try:
                        if mimepart.content_type.is_multipart():
                            continue  # TODO should we store relations?
                        message._parse_mimepart(
                            imap_uid,
                            mimepart,
                            account.namespace.id,
                            html_parts,
                            plain_parts,
                        )
                    except (
                        mime.DecodingError,
                        AttributeError,
                        RuntimeError,
                        TypeError,
                        ValueError,
                    ) as e:
                        if isinstance(e, ValueError) and not isinstance(
                            e, UnicodeEncodeError
                        ):
                            error_msg = e.args[0] if e.args else ""
                            if error_msg != (
                                "string argument should contain only ASCII characters"
                            ):
                                raise

                        log.error(
                            "Error parsing message MIME parts",
                            folder_name=folder_name,
                            account_id=account.id,
                            error=e,
                            imap_uid=imap_uid,
                        )
                        message._mark_error()

                del parsed  # free up memory as soon as possible

                store_body: bool = config.get("STORE_MESSAGE_BODIES", True)
                message.calculate_body(html_parts, plain_parts, store_body=store_body)

                del html_parts  # free up memory as soon as possible
                del plain_parts  # free up memory as soon as possible

            # Occasionally people try to send messages to way too many
            # recipients. In such cases, empty the field and treat as a parsing
            # error so that we don't break the entire sync.
            for field in ("to_addr", "cc_addr", "bcc_addr", "references", "reply_to"):
                value: List[Any] = getattr(message, field)
                if json_field_too_long(value):
                    log.warning(
                        "Recipient field too long",
                        field=field,
                        account_id=account.id,
                        folder_name=folder_name,
                        imap_uid=imap_uid,
                    )
                    setattr(message, field, [])
                    message._mark_error()

        return message

    def _parse_metadata(
        self,
        parsed: MimePart,
        body_string: bytes,
        received_date: Optional[datetime.datetime],
        account_id: int,
        folder_name: str,
        mid: int,
    ) -> None:
        mime_version: Optional[str] = parsed.headers.get("Mime-Version")
        # sometimes MIME-Version is '1.0 (1.0)', hence the .startswith()
        if mime_version is not None and not mime_version.startswith("1.0"):
            log.warning(
                "Unexpected MIME-Version",
                account_id=account_id,
                folder_name=folder_name,
                mid=mid,
                mime_version=mime_version,
            )

        self.subject: Optional[str] = parsed.subject
        self.from_addr = parse_mimepart_address_header(parsed, "From")
        self.sender_addr = parse_mimepart_address_header(parsed, "Sender")
        self.reply_to = parse_mimepart_address_header(parsed, "Reply-To")
        self.to_addr = parse_mimepart_address_header(parsed, "To")
        self.cc_addr = parse_mimepart_address_header(parsed, "Cc")
        self.bcc_addr = parse_mimepart_address_header(parsed, "Bcc")

        self.in_reply_to: Optional[str] = parsed.headers.get("In-Reply-To")

        # The RFC mandates that the Message-Id header must be at most 998
        # characters. Sadly, not everybody follows specs.
        self.message_id_header: Optional[str] = parsed.headers.get("Message-Id")
        if self.message_id_header and len(self.message_id_header) > 998:
            self.message_id_header = self.message_id_header[:998]
            log.warning(
                "Message-Id header too long. Truncating",
                parsed.headers.get("Message-Id"),
                logstash_tag="truncated_message_id",
            )

        # received_date is passed from INTERNALDATE on IMAP protocol level,
        # fallback to Date and Received headers from BODY[] if not present.
        self.received_date = (
            received_date
            if received_date
            else get_internaldate(
                parsed.headers.get("Date"), parsed.headers.get("Received")
            )
        )

        # It seems MySQL rounds up fractional seconds in a weird way,
        # preventing us from reconciling messages correctly. See:
        # https://github.com/nylas/sync-engine/commit/ed16b406e0a for
        # more details.
        self.received_date = self.received_date.replace(microsecond=0)

        # Custom Nylas header
        self.nylas_uid: Optional[str] = parsed.headers.get("X-INBOX-ID")

        # In accordance with JWZ (http://www.jwz.org/doc/threading.html)
        self.references = parse_references(
            parsed.headers.get("References", ""), parsed.headers.get("In-Reply-To", "")
        )

        self.size = len(body_string)  # includes headers text

    def _parse_mimepart(
        self,
        imap_uid: int,
        mimepart: MimePart,
        namespace_id: int,
        html_parts: List[str],
        plain_parts: List[str],
    ) -> None:
        disposition, _ = mimepart.content_disposition
        content_id: Optional[str] = mimepart.headers.get("Content-Id")
        content_type, params = mimepart.content_type

        filename: Optional[str] = mimepart.detected_file_name
        if filename == "":
            filename = None

        # Caution: Don't access mimepart.body unless you are sure
        # you are gonna need it in further processing. Reading this
        # attribute increases memory pressure siginificantly as it
        # immediately triggers decoding behind the scenes.
        # See: https://github.com/closeio/sync-engine/pull/480

        is_text = content_type.startswith("text")
        if disposition not in (None, "inline", "attachment"):
            log.error(
                "Unknown Content-Disposition",
                imap_uid=imap_uid,
                bad_content_disposition=mimepart.content_disposition,
            )
            self._mark_error()
            return

        if disposition == "attachment":
            self._save_attachment(
                mimepart.body,
                disposition,
                content_type,
                filename,
                content_id,
                namespace_id,
            )
            return

        if disposition == "inline" and not (
            is_text and filename is None and content_id is None
        ):
            # Some clients set Content-Disposition: inline on text MIME parts
            # that we really want to treat as part of the text body. Don't
            # treat those as attachments.
            self._save_attachment(
                mimepart.body,
                disposition,
                content_type,
                filename,
                content_id,
                namespace_id,
            )
            return

        if is_text:
            if not mimepart.size:
                return

            if content_type == "text/html":
                html_parts.append(normalize_data(mimepart.body))
            elif content_type == "text/plain":
                if not html_parts:
                    # Either html_parts or plain_parts are used to calculate
                    # message body and snippet in calculate_body but not
                    # both at the same time. As soon as we have at least one
                    # html part we can stop collecting plain ones.
                    plain_parts.append(normalize_data(mimepart.body))
            else:
                log.info(
                    "Saving other text MIME part as attachment",
                    content_type=content_type,
                    namespace_id=namespace_id,
                )
                self._save_attachment(
                    mimepart.body,
                    "attachment",
                    content_type,
                    filename,
                    content_id,
                    namespace_id,
                )
            return

        # Finally, if we get a non-text MIME part without Content-Disposition,
        # treat it as an attachment.
        self._save_attachment(
            mimepart.body,
            "attachment",
            content_type,
            filename,
            content_id,
            namespace_id,
        )

    def _save_attachment(
        self,
        data: Optional[str],
        content_disposition: str,
        content_type: str,
        filename: Optional[str],
        content_id: Optional[str],
        namespace_id: int,
    ) -> None:
        from inbox.models import Block, Part

        block = Block()
        block.namespace_id = namespace_id
        block.filename = _trim_filename(filename, namespace_id=namespace_id)
        block.content_type = content_type
        part = Part(block=block, message=self)
        if content_id:
            content_id = content_id[:255]
        part.content_id = content_id
        part.content_disposition = content_disposition
        data = data or ""
        if not isinstance(data, bytes):
            data = data.encode("utf-8", "strict")
        block.data = data

    def _mark_error(self) -> None:
        """
        Mark message as having encountered errors while parsing.

        Message parsing can fail for several reasons. Occasionally iconv will
        fail via maximum recursion depth. EAS messages may be missing Date and
        Received headers. Flanker may fail to handle some out-of-spec messages.

        In this case, we keep what metadata we've managed to parse but also
        mark the message as having failed to parse properly.

        """
        self.decode_error = True
        # fill in required attributes with filler data if could not parse them
        self.size = 0
        if self.received_date is None:
            self.received_date = datetime.datetime.utcnow()
        if self.snippet is None:
            self.snippet = ""

    def calculate_body(
        self, html_parts: List[str], plain_parts: List[str], store_body: bool = True
    ) -> None:
        """
        Calculate short message snippet and optionally store the entire body.

        This prefers text/html parts over text/plain parts i.e. as soon
        as there is at least one text/html part text/plain parts are irrelevant.
        """
        if any(html_parts):
            html_body = "".join(html_parts).strip()
            self.snippet = self.calculate_html_snippet(html_body)
            if store_body:
                self.body = html_body
            else:
                self.body = None
        elif any(plain_parts):
            plain_body = "\n".join(plain_parts).strip()
            self.snippet = self.calculate_plaintext_snippet(plain_body)
            if store_body:
                self.body = plaintext2html(plain_body, False)
            else:
                self.body = None
        else:
            self.body = None
            self.snippet = ""

    def calculate_html_snippet(self, text: str) -> str:
        try:
            text = strip_tags(text)
        except HTMLParseError:
            log.error(
                "error stripping tags", message_nylas_uid=self.nylas_uid, exc_info=True
            )
            text = ""

        return self.calculate_plaintext_snippet(text)

    def calculate_plaintext_snippet(self, text: str) -> str:
        return unicode_safe_truncate(" ".join(text.split()), SNIPPET_LENGTH)

    @property
    def body(self) -> Optional[str]:
        if self._compacted_body is None:
            return None
        return decode_blob(self._compacted_body).decode("utf-8")

    @body.setter
    def body(self, value: Optional[str]) -> None:
        if value is None:
            self._compacted_body = None
        else:
            self._compacted_body = encode_blob(value.encode("utf-8"))

    @property
    def participants(self) -> List[Tuple[str, str]]:
        """
        Different messages in the thread may reference the same email
        address with different phrases. We partially deduplicate: if the same
        email address occurs with both empty and nonempty phrase, we don't
        separately return the (empty phrase, address) pair.

        """
        deduped_participants: DefaultDict[str, Set[str]] = defaultdict(set)
        chain = []
        if self.from_addr:
            chain.append(self.from_addr)

        if self.to_addr:
            chain.append(self.to_addr)

        if self.cc_addr:
            chain.append(self.cc_addr)

        if self.bcc_addr:
            chain.append(self.bcc_addr)

        for phrase, address in itertools.chain.from_iterable(chain):
            deduped_participants[address].add(phrase.strip())

        p = []
        for address, phrases in deduped_participants.items():
            for phrase in phrases:
                if phrase != "" or len(phrases) == 1:
                    p.append((phrase, address))
        return p

    @property
    def attachments(self) -> List["Part"]:
        return [part for part in self.parts if part.is_attachment]

    @property
    def api_attachment_metadata(self) -> List[Dict[str, Any]]:
        resp = []
        for part in self.parts:
            if not part.is_attachment:
                continue
            k = {
                "content_type": part.block.content_type,
                "size": part.block.size,
                "filename": part.block.filename,
                "id": part.block.public_id,
            }
            content_id = part.content_id
            if content_id:
                if content_id[0] == "<" and content_id[-1] == ">":
                    content_id = content_id[1:-1]
                k["content_id"] = content_id
            resp.append(k)
        return resp

    @property
    def versioned_relationships(self):
        return ["parts", "messagecategories"]

    @property
    def propagated_attributes(self):
        return ["is_read", "is_starred", "messagecategories"]

    @property
    def has_attached_events(self):
        return "text/calendar" in [p.block.content_type for p in self.parts]

    @property
    def attached_event_files(self) -> List["Part"]:
        return [
            part for part in self.parts if part.block.content_type == "text/calendar"
        ]

    @property
    def account(self) -> Account:
        return self.namespace.account

    @classmethod
    def from_public_id(
        cls, public_id: str, namespace_id: int, db_session: Any
    ) -> "Message":
        q = db_session.query(cls)
        q = q.filter(
            Message.public_id == bindparam("public_id"),
            Message.namespace_id == bindparam("namespace_id"),
        )
        q = q.options(
            joinedload(Message.thread).load_only("discriminator", "public_id"),
            joinedload(Message.messagecategories).joinedload(MessageCategory.category),
            joinedload(Message.parts).joinedload("block"),
            joinedload(Message.events),
        )
        return q.params(public_id=public_id, namespace_id=namespace_id).one()

    @classmethod
    def api_loading_options(cls, expand=False):
        columns = [
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
            "subject",
            "snippet",
            "version",
            "from_addr",
            "to_addr",
            "cc_addr",
            "bcc_addr",
            "reply_to",
            "_compacted_body",
            "thread_id",
            "namespace_id",
        ]
        if expand:
            columns += ["message_id_header", "in_reply_to", "references"]

        from inbox.models.event import Event, RecurringEvent, RecurringEventOverride

        all_event_subclasses = with_polymorphic(
            Event, [RecurringEvent, RecurringEventOverride], flat=True
        )

        return (
            load_only(*columns),
            subqueryload("parts").joinedload("block"),
            subqueryload("thread").load_only("public_id", "discriminator"),
            subqueryload(Message.events.of_type(all_event_subclasses)),
            subqueryload("messagecategories").joinedload("category"),
        )


# Need to explicitly specify the index length for table generation with MySQL
# 5.6 when columns are too long to be fully indexed with utf8mb4 collation.
Index("ix_message_subject", Message.subject, mysql_length=80)

# For API querying performance.
Index(
    "ix_message_ns_id_is_draft_received_date",
    Message.namespace_id,
    Message.is_draft,
    Message.received_date,
)

# For async deletion.
Index("ix_message_namespace_id_deleted_at", Message.namespace_id, Message.deleted_at)

# For statistics about messages sent via Nylas
Index("ix_message_namespace_id_is_created", Message.namespace_id, Message.is_created)

# For filtering messages by Message-Id via API (with namespace), and for
# debugging purposes (without namespace).
Index(
    "ix_message_message_id_header_namespace_id",
    Message.message_id_header,
    Message.namespace_id,
    mysql_length={"message_id_header": 80},
)

# Used by delete-accounts to find next batch to delete
Index(
    "ix_message_namespace_id_received_date", Message.namespace_id, Message.received_date
)


class MessageCategory(MailSyncBase):
    """Mapping between messages and categories."""

    message_id = Column(BigInteger, nullable=False)
    message = relationship(
        "Message",
        primaryjoin="foreign(MessageCategory.message_id) == remote(Message.id)",
        backref=backref(
            "messagecategories", collection_class=set, cascade="all, delete-orphan"
        ),
    )

    category_id = Column(BigInteger, nullable=False, index=True)
    category = relationship(
        Category,
        primaryjoin="foreign(MessageCategory.category_id) == remote(Category.id)",
        backref=backref(
            "messagecategories", cascade="all, delete-orphan", lazy="dynamic"
        ),
    )

    @property
    def namespace(self):
        return self.message.namespace


Index("message_category_ids", MessageCategory.message_id, MessageCategory.category_id)
