"""IMAPClient wrapper for the Nylas Sync Engine."""

import contextlib
import datetime
import imaplib
import re
import ssl
import time
from typing import (
    Any,
    Callable,
    DefaultDict,
    Dict,
    List,
    NamedTuple,
    Optional,
    Set,
    Tuple,
    Type,
    Union,
)

import imapclient
import imapclient.exceptions
import imapclient.imap_utf7
import imapclient.imapclient
import imapclient.response_parser

from inbox.constants import MAX_MESSAGE_BODY_LENGTH

# Prevent "got more than 1000000 bytes" errors for servers that send more data.
imaplib._MAXLINE = 10000000  # type: ignore

# Even though RFC 2060 says that the date component must have two characters
# (either two digits or space+digit), it seems that some IMAP servers only
# return one digit. Fun times.
imaplib.InternalDate = re.compile(  # type: ignore
    r'.*INTERNALDATE "'
    r"(?P<day>[ 0123]?[0-9])-"  # insert that `?` to make first digit optional
    r"(?P<mon>[A-Z][a-z][a-z])-"
    r"(?P<year>[0-9][0-9][0-9][0-9])"
    r" (?P<hour>[0-9][0-9]):"
    r"(?P<min>[0-9][0-9]):"
    r"(?P<sec>[0-9][0-9])"
    r" (?P<zonen>[-+])(?P<zoneh>[0-9][0-9])(?P<zonem>[0-9][0-9])"
    r'"'
)

import functools
import queue
import socket
import threading
from collections import defaultdict, namedtuple
from email.parser import HeaderParser
from threading import BoundedSemaphore

from sqlalchemy.orm import joinedload

from inbox.exceptions import GmailSettingError
from inbox.folder_edge_cases import localized_folder_names
from inbox.logging import get_logger
from inbox.models import Account
from inbox.models.backends.generic import GenericAccount
from inbox.models.backends.gmail import GmailAccount
from inbox.models.backends.imap import ImapAccount
from inbox.models.backends.outlook import OutlookAccount
from inbox.models.session import session_scope
from inbox.util.concurrency import retry
from inbox.util.itert import chunk
from inbox.util.misc import or_none

log = get_logger()

__all__ = ["CrispinClient", "GmailCrispinClient"]


# Unify flags API across IMAP and Gmail
class Flags(NamedTuple):
    flags: Tuple[bytes, ...]
    modseq: Optional[int]


# Flags includes labels on Gmail because Gmail doesn't use \Draft.
class GmailFlags(NamedTuple):
    flags: Tuple[bytes, ...]
    labels: List[str]
    modseq: Optional[int]


GMetadata = namedtuple("GMetadata", "g_msgid g_thrid size")


class RawMessage(NamedTuple):
    uid: int
    internaldate: Optional[datetime.datetime]
    flags: Tuple[bytes, ...]
    body: bytes
    g_msgid: Optional[int]
    g_thrid: Optional[int]
    g_labels: Optional[List[str]]


RawFolder = namedtuple("RawFolder", "display_name role")
# class RawFolder(NamedTuple):
#     display_name: str
#     role: Optional[str]

# Lazily-initialized map of account ids to lock objects.
# This prevents multiple greenlets from concurrently creating duplicate
# connection pools for a given account.
_lock_map: DefaultDict[int, threading.Lock] = defaultdict(threading.Lock)

# Exception classes which indicate the network connection to the IMAP
# server is broken.
CONN_NETWORK_EXC_CLASSES = (socket.error, ssl.SSLError)

# Exception classes on which operations should be retried.
CONN_RETRY_EXC_CLASSES = CONN_NETWORK_EXC_CLASSES + (imaplib.IMAP4.error,)

# Exception classes on which connections should be discarded.
CONN_DISCARD_EXC_CLASSES = CONN_NETWORK_EXC_CLASSES + (
    ssl.CertificateError,
    imaplib.IMAP4.error,
)

# Exception classes which indicate the IMAP connection has become
# unusable.
CONN_UNUSABLE_EXC_CLASSES = CONN_NETWORK_EXC_CLASSES + (
    ssl.CertificateError,
    imaplib.IMAP4.abort,
)


class FolderMissingError(Exception):
    pass


class DraftDeletionException(Exception):
    pass


def _get_connection_pool(account_id, pool_size, pool_map, readonly):
    with _lock_map[account_id]:
        if account_id not in pool_map:
            pool_map[account_id] = CrispinConnectionPool(
                account_id, num_connections=pool_size, readonly=readonly
            )
        return pool_map[account_id]


_pool_map: Dict[int, "CrispinConnectionPool"] = {}


def connection_pool(account_id, pool_size=None):
    """Per-account crispin connection pool.

    Use like this:

        with crispin.connection_pool(account_id).get() as crispin_client:
            # your code here
            pass

    Note that the returned CrispinClient could have ANY folder selected, or
    none at all! It's up to the calling code to handle folder sessions
    properly. We don't reset to a certain select state because it's slow.
    """
    # Pick the pool size based on whether the account is throttled.
    if pool_size is None:
        with session_scope(account_id) as db_session:
            account = db_session.query(Account).get(account_id)
            if account.throttled:
                pool_size = 1
            else:
                pool_size = 3
    return _get_connection_pool(account_id, pool_size, _pool_map, True)


_writable_pool_map: Dict[int, "CrispinConnectionPool"] = {}


def writable_connection_pool(account_id, pool_size=1):
    """Per-account crispin connection pool, with *read-write* connections.

    Use like this:

        conn_pool = crispin.writable_connection_pool(account_id)
        with conn_pool.get() as crispin_client:
            # your code here
            pass
    """
    return _get_connection_pool(account_id, pool_size, _writable_pool_map, False)


def convert_flags(flags: Tuple[Union[bytes, int], ...]) -> Tuple[bytes, ...]:
    """
    Ensure flags are always treated as bytes in downstream code.

    We rarely get ints, but we only compare against well known flags like
    \\Seen, \\Answered, etc. which are always bytes. We don't care about ints
    but to prevent exceptions dowstream, we convert everything to bytes.
    """
    return tuple(
        flag if isinstance(flag, bytes) else str(flag).encode() for flag in flags
    )


class ConnectionPoolTimeoutError(Exception):
    pass


class CrispinConnectionPool:
    """
    Connection pool for Crispin clients.

    Connections in a pool are specific to an IMAPAccount.

    Parameters
    ----------
    account_id : int
        Which IMAPAccount to open up a connection to.
    num_connections : int
        How many connections in the pool.
    readonly : bool
        Is the connection to the IMAP server read-only?
    """

    def __init__(self, account_id, num_connections, readonly):
        log.info(
            "Creating Crispin connection pool",
            account_id=account_id,
            num_connections=num_connections,
        )
        self.account_id = account_id
        self.readonly = readonly
        self._queue: "queue.Queue[CrispinClient | None]" = queue.Queue(num_connections)
        for _ in range(num_connections):
            self._queue.put(None)
        self._sem = BoundedSemaphore(num_connections)
        self._set_account_info()

    def _should_timeout_connection(self):
        # Writable pools don't need connection timeouts because
        # SyncbackBatchTasks properly scope the IMAP connection across its
        # constituent SyncbackTasks.
        return self.readonly

    def _logout(self, client):
        try:
            client.logout()
        except Exception:
            log.info("Error on IMAP logout", exc_info=True)

    @contextlib.contextmanager
    def get(self, *, timeout: "float | None" = None):
        """Get a connection from the pool, or instantiate a new one if needed.

        If `num_connections` connections are already in use and timeout is `None`,
        block until one is available. If timeout is a `float`, raise a
        `ConnectionPoolTimeoutError` if a connection is not available within
        that time.

        Args:
            timeout: The maximum time in seconds to wait for a connection to
                become available. If `None`, block until a connection is available.
        """
        # A gevent semaphore is granted in the order that greenlets tried to
        # acquire it, so we use a semaphore here to prevent potential
        # starvation of greenlets if there is high contention for the pool.
        # The queue implementation does not have that property; having
        # greenlets simply block on self._queue.get(block=True) could cause
        # individual greenlets to block for arbitrarily long.

        succeeded = self._sem.acquire(timeout=timeout)
        if not succeeded:
            raise ConnectionPoolTimeoutError()

        client = self._queue.get()
        try:
            if client is None:
                client = self._new_connection()
            yield client

            if not self._should_timeout_connection():
                self._logout(client)
                client = None
        except CONN_DISCARD_EXC_CLASSES as exc:
            # Discard the connection on socket or IMAP errors. Technically this
            # isn't always necessary, since if you got e.g. a FETCH failure you
            # could reuse the same connection. But for now it's the simplest
            # thing to do.
            log.info("IMAP connection error; discarding connection", exc_info=True)
            if client is not None and not isinstance(exc, CONN_UNUSABLE_EXC_CLASSES):
                self._logout(client)
            client = None
            raise exc
        finally:
            self._queue.put(client)
            self._sem.release()

    def _set_account_info(self):
        with session_scope(self.account_id) as db_session:
            account = db_session.query(ImapAccount).get(self.account_id)
            self.sync_state = account.sync_state
            self.provider = account.provider
            self.provider_info = account.provider_info
            self.email_address = account.email_address
            self.auth_handler = account.auth_handler
            self.client_cls: Type[CrispinClient]
            if account.provider == "gmail":
                self.client_cls = GmailCrispinClient
            else:
                self.client_cls = CrispinClient

    def _new_raw_connection(self):
        """Returns a new, authenticated IMAPClient instance for the account."""
        from inbox.auth.google import GoogleAuthHandler
        from inbox.auth.microsoft import MicrosoftAuthHandler

        with session_scope(self.account_id) as db_session:
            if isinstance(self.auth_handler, GoogleAuthHandler):
                account = db_session.query(GmailAccount).get(self.account_id)
            elif isinstance(self.auth_handler, MicrosoftAuthHandler):
                account = db_session.query(OutlookAccount).get(self.account_id)
            else:
                account = (
                    db_session.query(GenericAccount)
                    .options(joinedload(GenericAccount.imap_secret))
                    .get(self.account_id)
                )
            db_session.expunge(account)

        return self.auth_handler.get_authenticated_imap_connection(
            account, self._should_timeout_connection()
        )

    def _new_connection(self):
        conn = self._new_raw_connection()
        return self.client_cls(
            self.account_id,
            self.provider_info,
            self.email_address,
            conn,
            readonly=self.readonly,
        )


def _exc_callback(exc):
    log.info(
        "Connection broken with error; retrying with new connection", exc_info=True
    )
    time.sleep(5)


retry_crispin = functools.partial(
    retry, retry_classes=CONN_RETRY_EXC_CLASSES, exc_callback=_exc_callback
)


common_uid_list_format = re.compile(b"^[0-9 ]+$")
uid_format = re.compile(b"[0-9]+")
original_parse_message_list = imapclient.response_parser.parse_message_list


def fixed_parse_message_list(data: List[bytes]) -> List[int]:
    """Fixed version of imapclient.response_parser.parse_message_list

    We observed in real world that some IMAP servers send many
    elements instead of a single element. While this is a violation of IMAP
    spec, we decided to still handle this gracefully by returning unique UIDs
    across all the elements.

    Aditionally this takes care of parsing the most common textual format that list may
    arrive in from an IMAP server. The algorithm is shorter and much less memory
    hungry than the generic version which becomes important when syncing
    large mailboxes. It relies on regexes to avoid creating intermediate lists.
    If we receive data in format that does not follow most common format
    we still fallback to the unoptimized version.

    Based off Tom's (Python 2 version):
    https://github.com/closeio/imapclient/commit/475d02f85be308fb4ac80e66628a03c30c096c9f
    For generic version see:
    https://github.com/mjs/imapclient/blob/master/imapclient/response_parser.py#L39-L79
    Implemented in:
    https://github.com/closeio/sync-engine/pull/483
    """
    # Handle case where we receive many elements instead of a single element
    if len(data) > 1:
        unique_uids: Set[int] = set()
        for datum in data:
            unique_uids.update(fixed_parse_message_list([datum]))

        return list(unique_uids)

    # Optimize most common textual format for low memory footprint
    with contextlib.suppress(TypeError):
        if len(data) == 1 and common_uid_list_format.match(data[0]):
            return [
                int(uid_match.group(0))
                for uid_match in re.finditer(uid_format, data[0])
            ]

    return original_parse_message_list(data)


# Replace the original algorithm with fixed algorithm from above
imapclient.response_parser.parse_message_list = fixed_parse_message_list
imapclient.imapclient.parse_message_list = fixed_parse_message_list


class CrispinClient:
    """
    Generic IMAP client wrapper.

    Generally, crispin client calls operate on the currently selected folder.
    There are some specific calls which may change the selected folder as a
    part of their work and may leave it selected at the end of the call, since
    folder selects are expensive in IMAP. These methods are called out in
    their docstrings.

    IMAP only guarantees that folder message UIDs are valid for a "session",
    which is defined as from the time you SELECT a folder until the connection
    is closed or another folder is selected.

    Crispin clients *always* return ints rather than strings for number
    data types, such as message UIDs, Google message IDs, and Google thread
    IDs.

    All inputs are coerced to strings before being passed off to the IMAPClient
    connection.

    You should really be interfacing with this class via a connection pool,
    see `connection_pool()`.

    Parameters
    ----------
    account_id : int
        Database id of the associated IMAPAccount.
    provider_info: dict
        Provider info from inbox/providers.py
    email_address: str
        Email address associated with the account.
    conn : IMAPClient
        Open IMAP connection (should be already authed).
    readonly : bool
        Whether or not to open IMAP connections as readonly.

    """

    def __init__(
        self,
        account_id: int,
        provider_info: Dict[str, Any],
        email_address: str,
        conn: imapclient.imapclient.IMAPClient,
        readonly: bool = True,
    ) -> None:
        self.account_id = account_id
        self.provider_info = provider_info
        self.email_address = email_address
        # IMAP isn't stateless :(
        self.selected_folder: Optional[Tuple[str, Dict[bytes, Any]]] = None
        self._folder_names: Optional[DefaultDict[str, List[str]]] = None
        self.conn = conn
        self.readonly = readonly

    def _fetch_folder_list(self) -> List[Tuple[Tuple[bytes, ...], bytes, str]]:
        r"""NOTE: XLIST is deprecated, so we just use LIST.

        An example response with some other flags:

            * LIST (\HasNoChildren) "/" "INBOX"
            * LIST (\Noselect \HasChildren) "/" "[Gmail]"
            * LIST (\HasNoChildren \All) "/" "[Gmail]/All Mail"
            * LIST (\HasNoChildren \Drafts) "/" "[Gmail]/Drafts"
            * LIST (\HasNoChildren \Important) "/" "[Gmail]/Important"
            * LIST (\HasNoChildren \Sent) "/" "[Gmail]/Sent Mail"
            * LIST (\HasNoChildren \Junk) "/" "[Gmail]/Spam"
            * LIST (\HasNoChildren \Flagged) "/" "[Gmail]/Starred"
            * LIST (\HasNoChildren \Trash) "/" "[Gmail]/Trash"

        IMAPClient parses this response into a list of
        (flags, delimiter, name) tuples.
        """
        # As discovered in the wild list_folders() can return None as name,
        # we cannot handle those folders anyway so just filter them out.
        return [
            (flags, delimiter, name)
            for flags, delimiter, name in self.conn.list_folders()
            if name is not None
        ]

    def select_folder_if_necessary(
        self,
        folder_name: str,
        uidvalidity_callback: Callable[[int, str, Dict[bytes, Any]], Dict[bytes, Any]],
    ) -> Dict[bytes, Any]:
        """Selects a given folder if it isn't already the currently selected
        folder.

        Makes sure to set the 'selected_folder' attribute to a
        (folder_name, select_info) pair.

        Selecting a folder indicates the start of an IMAP session.  IMAP UIDs
        are only guaranteed valid for sessions, so the caller must provide a
        callback that checks UID validity.

        If the folder is already the currently selected folder then we don't
        reselect the folder which in turn won't initiate a new session, so if
        you care about having a non-stale value for HIGHESTMODSEQ then don't
        use this function.
        """
        if self.selected_folder is None or folder_name != self.selected_folder[0]:
            return self.select_folder(folder_name, uidvalidity_callback)
        return uidvalidity_callback(
            self.account_id, folder_name, self.selected_folder[1]
        )

    def select_folder(
        self,
        folder_name: str,
        uidvalidity_callback: Callable[[int, str, Dict[bytes, Any]], Dict[bytes, Any]],
    ) -> Dict[bytes, Any]:
        """Selects a given folder.

        Makes sure to set the 'selected_folder' attribute to a
        (folder_name, select_info) pair.

        Selecting a folder indicates the start of an IMAP session.  IMAP UIDs
        are only guaranteed valid for sessions, so the caller must provide a
        callback that checks UID validity.

        Starts a new session even if `folder` is already selected, since
        this does things like e.g. makes sure we're not getting
        cached/out-of-date values for HIGHESTMODSEQ from the IMAP server.
        """
        try:
            select_info: Dict[bytes, Any] = self.conn.select_folder(
                folder_name, readonly=self.readonly
            )
        except imapclient.IMAPClient.Error as e:
            # Specifically point out folders that come back as missing by
            # checking for Yahoo / Gmail / Outlook (Hotmail) specific errors:
            # TODO: match with FolderSyncEngine.get_new_uids
            message = e.args[0] if e.args else ""
            if (
                "[NONEXISTENT] Unknown Mailbox:" in message
                or "does not exist" in message
                or "doesn't exist" in message
            ):
                raise FolderMissingError(folder_name)

            if "Access denied" in message:
                # TODO: This is not the best exception name, but it does the
                # expected thing here: We stop syncing the folder (but would
                # attempt selecting the folder again later).
                raise FolderMissingError(folder_name)

            # We can't assume that all errors here are caused by the folder
            # being deleted, as other connection errors could occur - but we
            # want to make sure we keep track of different providers'
            # "nonexistent" messages, so log this event.
            log.warning("IMAPClient error selecting folder", error=str(e))
            raise

        select_info[b"UIDVALIDITY"] = int(select_info[b"UIDVALIDITY"])
        self.selected_folder = (folder_name, select_info)
        # Don't propagate cached information from previous session
        self._folder_names = None
        return uidvalidity_callback(self.account_id, folder_name, select_info)

    @property
    def selected_folder_name(self):
        return or_none(self.selected_folder, lambda f: f[0])

    @property
    def selected_folder_info(self):
        return or_none(self.selected_folder, lambda f: f[1])

    @property
    def selected_uidvalidity(self):
        return or_none(self.selected_folder_info, lambda i: i[b"UIDVALIDITY"])

    @property
    def selected_uidnext(self):
        return or_none(self.selected_folder_info, lambda i: i.get(b"UIDNEXT"))

    @property
    def folder_separator(self) -> str:
        # We use the list command because it works for most accounts.
        folders_list: List[Tuple[Tuple[bytes, ...], bytes, str]] = (
            self.conn.list_folders()
        )

        if len(folders_list) == 0:
            return "."

        return folders_list[0][1].decode()

    @property
    def folder_prefix(self) -> str:
        # Unfortunately, some servers don't support the NAMESPACE command.
        # In this case, assume that there's no folder prefix.
        if self.conn.has_capability("NAMESPACE"):
            folder_prefix, folder_separator = self.conn.namespace()[0][0]
            return folder_prefix
        else:
            return ""

    def sync_folders(self):
        # () -> List[str]
        """
        List of folders to sync, in order of sync priority. Currently, that
        simply means inbox folder first.

        In generic IMAP, the 'INBOX' folder should be there but there are
        accounts without it in the wild.

        Returns
        -------
        list
            Folders to sync (as strings).

        """
        have_folders: DefaultDict[str, List[str]] = self.folder_names()

        # Sync inbox folder first, then sent, then others.
        to_sync: List[str] = []
        if "inbox" in have_folders:
            to_sync.extend(have_folders["inbox"])
        else:
            log.warning("Missing 'inbox' folder", account_id=self.account_id)
        to_sync.extend(have_folders.get("sent", []))
        for role, folder_names in have_folders.items():
            if role in ["inbox", "sent"]:
                continue
            to_sync.extend(folder_names)

        return to_sync

    def folder_names(self, force_resync: bool = False) -> DefaultDict[str, List[str]]:
        """
        Return the folder names for the account as a mapping from
        recognized role: list of folder names,
        for example: 'sent': ['Sent Items', 'Sent'].

        The list of recognized folder roles is in:
        inbox/models/constants.py

        Folders that do not belong to a recognized role are mapped to
        None, for example: None: ['MyFolder', 'OtherFolder'].

        The mapping is also cached in self._folder_names

        Parameters:
        -----------
        force_resync: boolean
            Return the cached mapping or return a refreshed mapping
            (after refetching from the remote).

        """
        if force_resync or self._folder_names is None:
            self._folder_names = defaultdict(list)

            raw_folders: List[RawFolder] = self.folders()
            for raw_folder in raw_folders:
                self._folder_names[raw_folder.role].append(raw_folder.display_name)

        return self._folder_names

    def folders(self) -> List[RawFolder]:
        """
        Fetch the list of folders for the account from the remote, return as a
        list of RawFolder objects.

        Note:
        Always fetches the list of folders from the remote.

        """
        raw_folders: List[RawFolder] = []

        # Folders that provide basic functionality of email
        system_role_names = ["inbox", "sent", "trash", "spam"]

        folders: List[Tuple[Tuple[bytes, ...], bytes, str]] = self._fetch_folder_list()
        for flags, _, name in folders:
            if (
                b"\\Noselect" in flags
                or b"\\NoSelect" in flags
                or b"\\NonExistent" in flags
            ):
                # Special folders that can't contain messages
                continue

            raw_folder: RawFolder = self._process_folder(name, flags)
            raw_folders.append(raw_folder)

        # Check to see if we have to guess the roles for any system role
        missing_roles: List[str] = self._get_missing_roles(
            raw_folders, system_role_names
        )
        guessed_roles: List[Optional[str]] = [
            self._guess_role(folder.display_name) for folder in raw_folders
        ]

        for role in missing_roles:
            if guessed_roles.count(role) == 1:
                guess_index = guessed_roles.index(role)
                raw_folders[guess_index] = RawFolder(
                    display_name=raw_folders[guess_index].display_name, role=role
                )

        return raw_folders

    def _get_missing_roles(
        self, folders: List[RawFolder], roles: List[str]
    ) -> List[str]:
        """
        Given a list of folders, and a list of roles, returns a list
        a list of roles that did not appear in the list of folders

        Parameters:
            folders: List of RawFolder objects
        roles: list of role strings

        Returns:
            a list of roles that did not appear as a role in folders
        """
        assert len(folders) > 0
        assert len(roles) > 0

        missing_roles: Dict[str, str] = {role: "" for role in roles}
        for folder in folders:
            # if role is in missing_roles, then we lied about it being missing
            if folder.role in missing_roles:
                del missing_roles[folder.role]

        return list(missing_roles)

    def _guess_role(self, folder: str) -> Optional[str]:
        """
        Given a folder, guess the system role that corresponds to that folder

        Parameters:
            folder: string representing the folder in question

        Returns:
            string representing role that most likely correpsonds to folder
        """
        # localized_folder_names is an external map of folders we have seen
        # in the wild with implicit roles that we were unable to determine
        # because they had missing flags. We've manually gone through the
        # folders and assigned them roles. When we find a folder we weren't
        # able to assign a role, we add it to that map
        for role in localized_folder_names:
            if folder in localized_folder_names[role]:
                return role

        return None

    def _process_folder(self, display_name: str, flags: Tuple[bytes, ...]) -> RawFolder:
        """
        Determine the role for the remote folder from its `name` and `flags`.

        Returns
        -------
            RawFolder representing the folder

        """
        # TODO[[k]: Important/ Starred for generic IMAP?

        # Different providers have different names for folders, here
        # we have a default map for common name mapping, additional
        # mappings can be provided via the provider configuration file
        default_folder_map: Dict[str, str] = {
            "inbox": "inbox",
            "drafts": "drafts",
            "draft": "drafts",
            "entw\xfcrfe": "drafts",
            "junk": "spam",
            "spam": "spam",
            "archive": "archive",
            "archiv": "archive",
            "sent": "sent",
            "sent items": "sent",
            "trash": "trash",
        }

        # Additionally we provide a custom mapping for providers that
        # don't fit into the defaults.
        folder_map = self.provider_info.get("folder_map", {})

        # Some providers also provide flags to determine common folders
        # Here we read these flags and apply the mapping
        flag_map: Dict[bytes, str] = {
            b"\\Trash": "trash",
            b"\\Sent": "sent",
            b"\\Drafts": "drafts",
            b"\\Junk": "spam",
            b"\\Inbox": "inbox",
            b"\\Spam": "spam",
        }

        role = default_folder_map.get(display_name.lower())

        if not role:
            role = folder_map.get(display_name)

        if not role:
            for flag in flags:
                if flag in flag_map:
                    role = flag_map[flag]
                    break

        return RawFolder(display_name=display_name, role=role)

    def create_folder(self, name):
        self.conn.create_folder(name)

    def condstore_supported(self) -> bool:
        # Technically QRESYNC implies CONDSTORE, although this is unlikely to
        # matter in practice.
        capabilities: Tuple[bytes, ...] = self.conn.capabilities()
        return b"CONDSTORE" in capabilities or b"QRESYNC" in capabilities

    def idle_supported(self) -> bool:
        return b"IDLE" in self.conn.capabilities()

    def search_uids(self, criteria: List[str]) -> List[int]:
        """
        Find UIDs in this folder matching the criteria. See
        http://tools.ietf.org/html/rfc3501.html#section-6.4.4 for valid
        criteria.

        """
        return sorted(
            int(uid) if not isinstance(uid, int) else uid
            for uid in self.conn.search(criteria)
        )

    def all_uids(self) -> List[int]:
        """Fetch all UIDs associated with the currently selected folder.

        Returns
        -------
        list
            UIDs as integers sorted in ascending order.
        """
        # Note that this list may include items which have been marked for
        # deletion with the \Deleted flag, but not yet actually removed via
        # an EXPUNGE command. I choose to include them here since most clients
        # will still display them (sometimes with a strikethrough). If showing
        # these is a problem, we can either switch back to searching for
        # 'UNDELETED' or doing a fetch for ['UID', 'FLAGS'] and filtering.

        try:
            t = time.time()
            fetch_result: List[int] = self.conn.search(["ALL"])
        except imaplib.IMAP4.error as e:
            message = e.args[0] if e.args else ""
            if message.find("UID SEARCH wrong arguments passed") >= 0:
                # Search query must not have parentheses for Mail2World servers
                log.debug(
                    "Getting UIDs failed when using 'UID SEARCH "
                    "(ALL)'. Switching to alternative 'UID SEARCH "
                    "ALL",
                    exception=e,
                )
                t = time.time()
                fetch_result = self.conn._search(["ALL"], None)
            elif message.find("UID SEARCH failed: Internal error") >= 0:
                # Oracle Beehive fails for some folders
                log.debug(
                    "Getting UIDs failed when using 'UID SEARCH "
                    "ALL'. Switching to alternative 'UID SEARCH "
                    "1:*",
                    exception=e,
                )
                t = time.time()
                fetch_result = self.conn.search(["1:*"])
            else:
                raise

        elapsed = time.time() - t
        log.debug(
            "Requested all UIDs", search_time=elapsed, total_uids=len(fetch_result)
        )
        return sorted(
            int(uid) if not isinstance(uid, int) else uid for uid in fetch_result
        )

    def uids(self, uids: List[int]) -> List[RawMessage]:
        uid_set = set(uids)
        imap_messages: Dict[int, Dict[bytes, Any]] = {}
        raw_messages: List[RawMessage] = []

        for uid in uid_set:
            try:
                # Microsoft IMAP server returns a bunch of crap which could
                # corrupt other UID data. Also we don't always get a message
                # back at the first try.
                for _ in range(3):
                    # We already reject parsing messages bigger than MAX_MESSAGE_BODY_PARSE_LENGTH.
                    # We might as well not download them at all, save bandwidth, processing time
                    # and prevent OOMs due to fetching oversized emails.
                    fetched_size: Dict[int, Dict[bytes, Any]] = self.conn.fetch(
                        uid, ["RFC822.SIZE"]
                    )
                    body_size = int(fetched_size.get(uid, {}).get(b"RFC822.SIZE", 0))
                    if body_size > MAX_MESSAGE_BODY_LENGTH:
                        log.warning("Skipping fetching of oversized message", uid=uid)
                        continue

                    result: Dict[int, Dict[bytes, Any]] = self.conn.fetch(
                        uid, ["BODY.PEEK[]", "INTERNALDATE", "FLAGS"]
                    )
                    if uid in result:
                        imap_messages[uid] = result[uid]
                        break
            except imapclient.IMAPClient.Error as e:
                if (
                    "[UNAVAILABLE] UID FETCH Server error while fetching messages"
                ) in str(e):
                    log.info(
                        "Got an exception while requesting an UID",
                        uid=uid,
                        error=e,
                        logstash_tag="imap_download_exception",
                    )
                    continue
                else:
                    log.info(
                        ("Got an unhandled exception while requesting an UID"),
                        uid=uid,
                        error=e,
                        logstash_tag="imap_download_exception",
                    )
                    raise

        for uid in sorted(imap_messages, key=int):
            # Skip handling unsolicited FETCH responses
            if uid not in uid_set:
                continue
            imap_message = imap_messages[uid]
            if not set(imap_message).issuperset({b"FLAGS", b"BODY[]"}):
                assert self.selected_folder
                log.warning(
                    "Missing one of FLAGS or BODY[] for UID, skipping",
                    folder=self.selected_folder[0],
                    uid=uid,
                    keys=list(imap_message),
                )
                continue

            raw_messages.append(
                RawMessage(
                    uid=int(uid),
                    # we can recover from missing INTERNALDATE by parsing BODY[]
                    # and relying on Date and Received headers. This is done in
                    # inbox.models.message.Message._parse_metadata.
                    internaldate=imap_message.get(b"INTERNALDATE"),
                    flags=convert_flags(imap_message[b"FLAGS"]),
                    body=imap_message[b"BODY[]"],
                    # TODO: use data structure that isn't
                    # Gmail-specific
                    g_thrid=None,
                    g_msgid=None,
                    g_labels=None,
                )
            )
        return raw_messages

    def flags(self, uids: List[int]) -> Dict[int, Union[GmailFlags, Flags]]:
        seqset: Union[str, List[int]]
        if len(uids) > 100:
            # Some backends abort the connection if you give them a really
            # long sequence set of individual UIDs, so instead fetch flags for
            # all UIDs greater than or equal to min(uids).
            seqset = f"{min(uids)}:*"
        else:
            seqset = uids
        data: Dict[int, Dict[bytes, Any]] = self.conn.fetch(seqset, ["FLAGS"])
        uid_set = set(uids)
        return {
            uid: Flags(ret[b"FLAGS"], None)
            for uid, ret in data.items()
            if uid in uid_set
        }

    def delete_uids(self, uids):
        uids = [str(u) for u in uids]
        self.conn.delete_messages(uids, silent=True)
        self.conn.expunge()

    def set_starred(self, uids, starred):
        if starred:
            self.conn.add_flags(uids, ["\\Flagged"], silent=True)
        else:
            self.conn.remove_flags(uids, ["\\Flagged"], silent=True)

    def set_unread(self, uids, unread):
        uids = [str(u) for u in uids]
        if unread:
            self.conn.remove_flags(uids, ["\\Seen"], silent=True)
        else:
            self.conn.add_flags(uids, ["\\Seen"], silent=True)

    def save_draft(self, message, date=None):
        assert (
            self.selected_folder_name in self.folder_names()["drafts"]
        ), f"Must select a drafts folder first ({self.selected_folder_name})"

        self.conn.append(
            self.selected_folder_name, message, ["\\Draft", "\\Seen"], date
        )

    def create_message(self, message, date=None):
        """
        Create a message on the server. Only used to fix server-side bugs,
        like iCloud not saving Sent messages.

        """
        assert (
            self.selected_folder_name in self.folder_names()["sent"]
        ), f"Must select sent folder first ({self.selected_folder_name})"

        return self.conn.append(self.selected_folder_name, message, ["\\Seen"], date)

    def fetch_headers(self, uids: List[int]) -> Dict[int, Dict[bytes, Any]]:
        """
        Fetch headers for the given uids. Chunked because certain providers
        fail with 'Command line too large' if you feed them too many uids at
        once.

        """
        headers: Dict[int, Dict[bytes, Any]] = {}
        for uid_chunk in chunk(uids, 100):
            headers.update(self.conn.fetch(uid_chunk, ["BODY.PEEK[HEADER]"]))
        return headers

    def find_by_header(self, header_name, header_value):
        """Find all uids in the selected folder with the given header value."""
        all_uids = self.all_uids()
        # It would be nice to just search by header too, but some backends
        # don't support that, at least not if you want to search by X-INBOX-ID
        # header. So fetch the header for each draft and see if we
        # can find one that matches.
        # TODO(emfree): are there other ways we can narrow the result set a
        # priori (by subject or date, etc.)
        matching_draft_headers = self.fetch_headers(all_uids)
        results = []
        for uid, response in matching_draft_headers.items():
            headers = response[b"BODY[HEADER]"]
            parser = HeaderParser()
            header = parser.parsestr(headers).get(header_name)
            if header == header_value:
                results.append(uid)

        return results

    def delete_sent_message(self, message_id_header, delete_multiple=False):
        """
        Delete a message in the sent folder, as identified by the Message-Id
        header. We first delete the message from the Sent folder, and then
        also delete it from the Trash folder if necessary.

        Leaves the Trash folder selected at the end of the method.

        """
        log.info("Trying to delete sent message", message_id_header=message_id_header)
        sent_folder_name = self.folder_names()["sent"][0]
        self.conn.select_folder(sent_folder_name)
        msg_deleted = self._delete_message(message_id_header, delete_multiple)
        if msg_deleted:
            trash_folder_name = self.folder_names()["trash"][0]
            self.conn.select_folder(trash_folder_name)
            self._delete_message(message_id_header, delete_multiple)
        return msg_deleted

    def delete_draft(self, message_id_header):
        """
        Delete a draft, as identified by its Message-Id header. We first delete
        the message from the Drafts folder,
        and then also delete it from the Trash folder if necessary.

        Leaves the Trash folder selected at the end of the method.

        """
        drafts_folder_name = self.folder_names()["drafts"][0]
        log.info(
            "Trying to delete draft",
            message_id_header=message_id_header,
            folder=drafts_folder_name,
        )
        self.conn.select_folder(drafts_folder_name)
        draft_deleted = self._delete_message(message_id_header)
        if draft_deleted:
            trash_folder_name = self.folder_names()["trash"][0]
            self.conn.select_folder(trash_folder_name)
            self._delete_message(message_id_header)
        return draft_deleted

    def _delete_message(self, message_id_header, delete_multiple=False):
        """
        Delete a message from the selected folder, using the Message-Id header
        to locate it. Does nothing if no matching messages are found, or if
        more than one matching message is found.

        """
        matching_uids = self.find_by_header("Message-Id", message_id_header)
        if not matching_uids:
            log.error(
                "No remote messages found to delete",
                message_id_header=message_id_header,
            )
            return False
        if len(matching_uids) > 1 and not delete_multiple:
            log.error(
                "Multiple remote messages found to delete",
                message_id_header=message_id_header,
                uids=matching_uids,
            )
            return False
        self.conn.delete_messages(matching_uids, silent=True)
        self.conn.expunge()
        return True

    def logout(self):
        self.conn.logout()

    def idle(self, timeout):
        """Idle for up to `timeout` seconds. Make sure we take the connection
        back out of idle mode so that we can reuse this connection in another
        context.
        """
        self.conn.idle()
        try:
            r = self.conn.idle_check(timeout)
        except Exception:
            self.conn.idle_done()
            raise
        self.conn.idle_done()
        return r

    def condstore_changed_flags(
        self, modseq: int
    ) -> Dict[int, Union[GmailFlags, Flags]]:
        """
        Fetch flags that changed since the given modseq.

        Note that a well-behaved server should always return the MODSEQ
        as part of the response as specified in
        https://datatracker.ietf.org/doc/html/rfc4551#section-3.3.1.

        > When CHANGEDSINCE FETCH modifier is specified,
        > it implicitly adds MODSEQ FETCH message data item

        However, SmarterMail does not do that, so we add it explicitely.
        """
        items = (
            ["FLAGS", "MODSEQ"]
            if self.conn.welcome.endswith(b" SmarterMail")
            else ["FLAGS"]
        )
        data: Dict[int, Dict[bytes, Any]] = self.conn.fetch(
            "1:*", items, modifiers=[f"CHANGEDSINCE {modseq}"]
        )
        return {
            uid: Flags(ret[b"FLAGS"], ret[b"MODSEQ"][0] if b"MODSEQ" in ret else None)
            for uid, ret in data.items()
        }


class GmailCrispinClient(CrispinClient):
    PROVIDER = "gmail"

    def sync_folders(self) -> List[str]:
        """
        Gmail-specific list of folders to sync.

        In Gmail, every message is in `All Mail`, with the exception of
        messages in the Trash and Spam folders. So we only sync the `All Mail`,
        Trash and Spam folders.

        Returns
        -------
        list
            Folders to sync (as strings).

        """
        present_folders = self.folder_names()

        if "all" not in present_folders:
            raise GmailSettingError(
                f"Account {self.email_address} is missing the 'All Mail' folder. This is "
                "probably due to 'Show in IMAP' being disabled. "
                "See https://support.nylas.com/hc/en-us/articles/217562277 "
                "for more details. "
                f"Folders that were present at the time of error: {dict(present_folders)}"
            )

        # If the account has Trash, Spam folders, sync those too.
        to_sync: List[str] = []
        for folder in ["all", "trash", "spam"]:
            if folder in present_folders:
                to_sync.append(present_folders[folder][0])
        return to_sync

    def flags(self, uids: List[int]) -> Dict[int, Union[GmailFlags, Flags]]:
        """
        Gmail-specific flags.

        Returns
        -------
        dict
            Mapping of `uid` : GmailFlags.

        """
        data: Dict[int, Dict[bytes, Any]] = self.conn.fetch(
            uids, ["FLAGS", "X-GM-LABELS"]
        )
        uid_set = set(uids)
        return {
            uid: GmailFlags(
                tuple(flag for flag in ret[b"FLAGS"] if isinstance(flag, bytes)),
                self._decode_labels(ret[b"X-GM-LABELS"]),
                ret[b"MODSEQ"][0] if b"MODSEQ" in ret else None,
            )
            for uid, ret in data.items()
            if uid in uid_set
        }

    def condstore_changed_flags(
        self, modseq: int
    ) -> Dict[int, Union[GmailFlags, Flags]]:
        data: Dict[int, Dict[bytes, Any]] = self.conn.fetch(
            "1:*", ["FLAGS", "X-GM-LABELS"], modifiers=[f"CHANGEDSINCE {modseq}"]
        )
        results: Dict[int, Union[GmailFlags, Flags]] = {}
        for uid, ret in data.items():
            if b"FLAGS" not in ret or b"X-GM-LABELS" not in ret:
                # We might have gotten an unsolicited fetch response that
                # doesn't have all the data we asked for -- if so, explicitly
                # fetch flags and labels for that UID.
                log.info(
                    "Got incomplete response in flags fetch", uid=uid, ret=str(ret)
                )
                data_for_uid: Dict[int, Dict[bytes, Any]] = self.conn.fetch(
                    uid, ["FLAGS", "X-GM-LABELS"]
                )
                if not data_for_uid:
                    continue
                ret = data_for_uid[uid]
            results[uid] = GmailFlags(
                tuple(flag for flag in ret[b"FLAGS"] if isinstance(flag, bytes)),
                self._decode_labels(ret[b"X-GM-LABELS"]),
                ret[b"MODSEQ"][0],
            )
        return results

    def g_msgids(self, uids: List[int]) -> Dict[int, int]:
        """
        X-GM-MSGIDs for the given UIDs.

        Returns
        -------
        dict
            Mapping of `uid` (int) : `g_msgid` (int)

        """
        data: Dict[int, Dict[bytes, Any]] = self.conn.fetch(uids, ["X-GM-MSGID"])
        uid_set = set(uids)
        return {uid: ret[b"X-GM-MSGID"] for uid, ret in data.items() if uid in uid_set}

    def g_msgid_to_uids(self, g_msgid: int) -> List[int]:
        """
        Find all message UIDs in the selected folder with X-GM-MSGID equal to
        g_msgid.

        Returns
        -------
        list
        """
        uids = [int(uid) for uid in self.conn.search(["X-GM-MSGID", g_msgid])]
        # UIDs ascend over time; return in order most-recent first
        return sorted(uids, reverse=True)

    def folder_names(self, force_resync: bool = False) -> DefaultDict[str, List[str]]:
        """
        Return the folder names ( == label names for Gmail) for the account
        as a mapping from recognized role: list of folder names in the
        role, for example: 'sent': ['Sent Items', 'Sent'].

        The list of recognized categories is in:
        inbox/models/constants.py

        Folders that do not belong to a recognized role are mapped to None, for
        example: None: ['MyFolder', 'OtherFolder'].

        The mapping is also cached in self._folder_names

        Parameters:
        -----------
        force_resync: boolean
            Return the cached mapping or return a refreshed mapping
            (after refetching from the remote).

        """
        if force_resync or self._folder_names is None:
            self._folder_names = defaultdict(list)

            raw_folders: List[RawFolder] = self.folders()
            for raw_folder in raw_folders:
                self._folder_names[raw_folder.role].append(raw_folder.display_name)

        return self._folder_names

    def _process_folder(self, display_name: str, flags: Tuple[bytes, ...]) -> RawFolder:
        """
        Determine the canonical_name for the remote folder from its `name` and
        `flags`.

        Returns
        -------
            RawFolder representing the folder

        """
        flag_map = {
            b"\\Drafts": "drafts",
            b"\\Important": "important",
            b"\\Sent": "sent",
            b"\\Junk": "spam",
            b"\\Flagged": "starred",
            b"\\Trash": "trash",
        }

        role = None
        if b"\\All" in flags:
            role = "all"
        elif display_name.lower() == "inbox":
            # Special-case the display name here. In Gmail, the inbox
            # folder shows up in the folder list as 'INBOX', and in sync as
            # the label '\\Inbox'. We're just always going to give it the
            # display name 'Inbox'.
            role = "inbox"
            display_name = "Inbox"
        else:
            for flag in flags:
                if flag in flag_map:
                    role = flag_map[flag]
                    break

        return RawFolder(display_name=display_name, role=role)

    def uids(self, uids: List[int]) -> List[RawMessage]:
        imap_messages: Dict[int, Dict[bytes, Any]] = self.conn.fetch(
            uids,
            [
                "BODY.PEEK[]",
                "INTERNALDATE",
                "FLAGS",
                "X-GM-THRID",
                "X-GM-MSGID",
                "X-GM-LABELS",
            ],
        )

        raw_messages = []
        uid_set = set(uids)
        for uid in sorted(imap_messages, key=int):
            # Skip handling unsolicited FETCH responses
            if uid not in uid_set:
                continue
            imap_message = imap_messages[uid]
            raw_messages.append(
                RawMessage(
                    uid=int(uid),
                    internaldate=imap_message[b"INTERNALDATE"],
                    flags=convert_flags(imap_message[b"FLAGS"]),
                    body=imap_message[b"BODY[]"],
                    g_thrid=int(imap_message[b"X-GM-THRID"]),
                    g_msgid=int(imap_message[b"X-GM-MSGID"]),
                    g_labels=self._decode_labels(imap_message[b"X-GM-LABELS"]),
                )
            )
        return raw_messages

    def g_metadata(self, uids):
        """
        Download Gmail MSGIDs, THRIDs, and message sizes for the given uids.

        Parameters
        ----------
        uids : list
            UIDs to fetch data for. Must be from the selected folder.

        Returns
        -------
        dict
            uid: GMetadata(msgid, thrid, size)
        """
        # Super long sets of uids may fail with BAD ['Could not parse command']
        # In that case, just fetch metadata for /all/ uids.
        seqset = uids if len(uids) < 1e6 else "1:*"
        data = self.conn.fetch(seqset, ["X-GM-MSGID", "X-GM-THRID", "RFC822.SIZE"])
        uid_set = set(uids)
        return {
            uid: GMetadata(ret[b"X-GM-MSGID"], ret[b"X-GM-THRID"], ret[b"RFC822.SIZE"])
            for uid, ret in data.items()
            if uid in uid_set
        }

    def expand_thread(self, g_thrid):
        """
        Find all message UIDs in the selected folder with X-GM-THRID equal to
        g_thrid.

        Returns
        -------
        list
        """
        uids = [int(uid) for uid in self.conn.search(["X-GM-THRID", g_thrid])]
        # UIDs ascend over time; return in order most-recent first
        return sorted(uids, reverse=True)

    def find_by_header(self, header_name, header_value):
        return self.conn.search(["HEADER", header_name, header_value])

    def _decode_labels(self, labels):
        return [imapclient.imap_utf7.decode(label) for label in labels]

    def delete_draft(self, message_id_header):
        """
        Delete a message in the drafts folder, as identified by the Message-Id
        header. This overrides the parent class's method because gmail has
        weird delete semantics: to delete a message from a "folder" (actually a
        label) besides Trash or Spam, you must copy it to the trash. Issuing a
        delete command will only remove the label. So here we first copy the
        message from the draft folder to Trash, and then also delete it from the
        Trash folder to permanently delete it.

        Leaves the Trash folder selected at the end of the method.
        """
        log.info("Trying to delete gmail draft", message_id_header=message_id_header)
        drafts_folder_name = self.folder_names()["drafts"][0]
        trash_folder_name = self.folder_names()["trash"][0]
        sent_folder_name = self.folder_names()["sent"][0]

        # There's a race condition in how Gmail reconciles sent messages
        # which sometimes causes us to delete both the sent and draft
        # (because for a brief moment in time they're the same message).
        # To work around this, we use x-gm-msgid and check that the
        # sent message and the draft have been reconciled to different
        # values.

        # First find the message in the sent folder
        self.conn.select_folder(sent_folder_name)
        matching_uids = self.find_by_header("Message-Id", message_id_header)

        if len(matching_uids) == 0:
            raise DraftDeletionException("Couldn't find sent message in sent folder.")

        sent_gm_msgids = self.g_msgids(matching_uids)
        if len(sent_gm_msgids) != 1:
            raise DraftDeletionException("Only one message should have this msgid")

        # Then find the draft in the draft folder
        self.conn.select_folder(drafts_folder_name)
        matching_uids = self.find_by_header("Message-Id", message_id_header)
        if not matching_uids:
            return False

        # Make sure to remove the \\Draft flags so that Gmail removes it from
        # the draft folder.
        self.conn.remove_flags(matching_uids, ["\\Draft"])
        self.conn.remove_gmail_labels(matching_uids, ["\\Draft"])

        gm_msgids = self.g_msgids(matching_uids)
        for msgid in gm_msgids.values():
            if msgid == list(sent_gm_msgids.values())[0]:
                raise DraftDeletionException(
                    "Send and draft should have been reconciled as "
                    "different messages."
                )

        self.conn.copy(matching_uids, trash_folder_name)
        self.conn.select_folder(trash_folder_name)

        for msgid in gm_msgids.values():
            uids = self.g_msgid_to_uids(msgid)
            self.conn.delete_messages(uids, silent=True)

        self.conn.expunge()
        return True

    def delete_sent_message(self, message_id_header, delete_multiple=False):
        """
        Delete a message in the sent folder, as identified by the Message-Id
        header. This overrides the parent class's method because gmail has
        weird delete semantics: to delete a message from a "folder" (actually a
        label) besides Trash or Spam, you must copy it to the trash. Issuing a
        delete command will only remove the label. So here we first copy the
        message from the Sent folder to Trash, and then also delete it from the
        Trash folder to permanently delete it.

        Leaves the Trash folder selected at the end of the method.

        """
        log.info("Trying to delete sent message", message_id_header=message_id_header)
        sent_folder_name = self.folder_names()["sent"][0]
        trash_folder_name = self.folder_names()["trash"][0]
        # First find the message in Sent
        self.conn.select_folder(sent_folder_name)
        matching_uids = self.find_by_header("Message-Id", message_id_header)
        if not matching_uids:
            return False

        # To delete, first copy the message to trash (sufficient to move from
        # gmail's All Mail folder to Trash folder)
        self.conn.copy(matching_uids, trash_folder_name)

        # Next, select delete the message from trash (in the normal way) to
        # permanently delete it.
        self.conn.select_folder(trash_folder_name)
        self._delete_message(message_id_header, delete_multiple)
        return True

    def search_uids(self, criteria: List[str]) -> List[int]:
        """
        Handle Gmail label search oddities.
        https://developers.google.com/gmail/imap/imap-extensions#access_to_gmail_labels_x-gm-labels.

        UTF-7 encodes label names and also quotes it to prevent errors when the label contains
        asterisks (*). imapclient's search method sends label names containing asterisks unquoted which
        upsets Gmail IMAP server.
        """
        if len(criteria) != 2:
            return super().search_uids(criteria)

        if criteria[0] != "X-GM-LABELS":
            return super().search_uids(criteria)

        # First UTF-7 encode the label name
        label_name = criteria[1]
        encoded_label_name: bytes = imapclient.imap_utf7.encode(label_name)

        # If label contained only ASCII characters and does not contain asterisks
        # we don't need to do anything special
        if encoded_label_name.decode("ascii") == label_name and "*" not in label_name:
            return super().search_uids(criteria)

        # At this point quote Gmail label name since it could contain asterisks.
        # Sending unquotted label name containg asterisks upsets Gmail IMAP server
        # and triggers imapclient.exceptions.InvalidCriteriaError: b'Could not parse command'
        # based off: https://github.com/mjs/imapclient/blob/0279592557495d4ddf7619b17ed9e73b21161bdf/imapclient/imapclient.py#L1826
        encoded_label_name = encoded_label_name.replace(b"\\", b"\\\\")
        encoded_label_name = encoded_label_name.replace(b'"', b'\\"')
        encoded_label_name = b'"' + encoded_label_name + b'"'

        # Now actually perform the search skipping imapclient's public API which does quoting differently
        # based off: https://github.com/mjs/imapclient/blob/master/imapclient/imapclient.py#L1123-L1145
        try:
            data = self.conn._raw_command_untagged(
                b"SEARCH", [b"X-GM-LABELS", encoded_label_name]
            )
        except imaplib.IMAP4.error as e:
            # Make BAD IMAP responses easier to understand to the user, with a link to the docs
            m = re.match(r"SEARCH command error: BAD \[(.+)\]", str(e))
            if m:
                raise imapclient.exceptions.InvalidCriteriaError(
                    "{original_msg}\n\n"
                    "This error may have been caused by a syntax error in the criteria: "
                    "{criteria}\nPlease refer to the documentation for more information "
                    "about search criteria syntax..\n"
                    "https://imapclient.readthedocs.io/en/master/#imapclient.IMAPClient.search".format(
                        original_msg=m.group(1),
                        criteria=(
                            f'"{criteria}"'
                            if not isinstance(criteria, list)
                            else criteria
                        ),
                    )
                )

            # If the exception is not from a BAD IMAP response, re-raise as-is
            raise

        response = imapclient.response_parser.parse_message_list(data)
        return sorted(int(uid) if not isinstance(uid, int) else uid for uid in response)
