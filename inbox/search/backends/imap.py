from imaplib import IMAP4

from imapclient import IMAPClient  # type: ignore[import-untyped]
from sqlalchemy import desc  # type: ignore[import-untyped]

from inbox.api.kellogs import APIEncoder
from inbox.crispin import CrispinClient, FolderMissingError
from inbox.exceptions import (
    IMAPDisabledError,
    NotSupportedError,
    ValidationError,
)
from inbox.logging import get_logger
from inbox.mailsync.backends.imap.generic import UidInvalid, uidvalidity_cb
from inbox.models import Account, Folder, Message, Thread
from inbox.models.backends.imap import ImapUid
from inbox.models.session import session_scope
from inbox.providers import provider_info
from inbox.search.base import SearchBackendException

PROVIDER = "imap"


class IMAPSearchClient:
    def __init__(self, account) -> None:  # type: ignore[no-untyped-def]
        self.account = account
        self.account_id = account.id
        self.log = get_logger().new(account_id=account.id, component="search")

    def _open_crispin_connection(  # type: ignore[no-untyped-def]
        self, db_session
    ):
        account = db_session.query(Account).get(self.account_id)
        try:
            conn = account.auth_handler.get_authenticated_imap_connection(
                account
            )
        except (IMAPClient.Error, OSError, IMAP4.error) as exc:
            raise SearchBackendException(
                (
                    "Unable to connect to the IMAP "
                    "server. Please retry in a "
                    "couple minutes."
                ),
                503,
            ) from exc
        except ValidationError as exc:
            raise SearchBackendException(
                (
                    "This search can't be performed "
                    "because the account's credentials "
                    "are out of date. Please "
                    "reauthenticate and try again."
                ),
                403,
            ) from exc
        except IMAPDisabledError as exc:
            raise SearchBackendException(
                (
                    "This search can't be performed "
                    "because the account doesn't have IMAP enabled. "
                    "Enable IMAP and try again."
                ),
                403,
            ) from exc

        try:
            acct_provider_info = provider_info(account.provider)
        except NotSupportedError:
            self.log.warning(
                "Account provider not supported", provider=account.provider
            )
            raise

        self.crispin_client = CrispinClient(
            self.account_id,
            acct_provider_info,
            account.email_address,
            conn,
            readonly=True,
        )

    def _close_crispin_connection(self) -> None:
        self.crispin_client.logout()

    def search_messages(  # type: ignore[no-untyped-def]  # noqa: ANN201
        self, db_session, search_query, offset: int = 0, limit: int = 40
    ):
        imap_uids = []
        for uids in self._search(db_session, search_query):
            imap_uids.extend(uids)

        query = (
            db_session.query(Message)
            .join(ImapUid)
            .filter(
                ImapUid.account_id == self.account_id,
                ImapUid.msg_uid.in_(imap_uids),
            )
            .order_by(desc(Message.received_date))
        )
        if offset:
            query = query.offset(offset)

        if limit:
            query = query.limit(limit)

        return query.all()

    def stream_messages(self, search_query):  # type: ignore[no-untyped-def]  # noqa: ANN201
        def g():  # type: ignore[no-untyped-def]
            encoder = APIEncoder()

            with session_scope(self.account_id) as db_session:
                try:
                    for imap_uids in self._search(db_session, search_query):
                        query = (
                            db_session.query(Message)
                            .join(ImapUid)
                            .filter(
                                ImapUid.account_id == self.account_id,
                                ImapUid.msg_uid.in_(imap_uids),
                            )
                            .order_by(desc(Message.received_date))
                        )
                        yield encoder.cereal(query.all()) + "\n"
                except Exception as e:
                    self.log.error("Error while streaming messages", error=e)

        return g

    def search_threads(  # type: ignore[no-untyped-def]  # noqa: ANN201
        self, db_session, search_query, offset: int = 0, limit: int = 40
    ):
        imap_uids = []
        for uids in self._search(db_session, search_query):
            imap_uids.extend(uids)

        query = (
            db_session.query(Thread)
            .join(Message, Message.thread_id == Thread.id)
            .join(ImapUid)
            .filter(
                ImapUid.account_id == self.account_id,
                ImapUid.msg_uid.in_(imap_uids),
                Thread.deleted_at.is_(None),
                Thread.id == Message.thread_id,
            )
            .order_by(desc(Message.received_date))
        )

        if offset:
            query = query.offset(offset)

        if limit:
            query = query.limit(limit)
        return query.all()

    def stream_threads(self, search_query):  # type: ignore[no-untyped-def]  # noqa: ANN201
        def g():  # type: ignore[no-untyped-def]
            encoder = APIEncoder()

            with session_scope(self.account_id) as db_session:
                for imap_uids in self._search(db_session, search_query):
                    query = (
                        db_session.query(Thread)
                        .join(Message, Message.thread_id == Thread.id)
                        .join(ImapUid)
                        .filter(
                            ImapUid.account_id == self.account_id,
                            ImapUid.msg_uid.in_(imap_uids),
                            Thread.id == Message.thread_id,
                        )
                        .order_by(desc(Message.received_date))
                    )

                    yield encoder.cereal(query.all()) + "\n"

        return g

    def _search(  # type: ignore[no-untyped-def]
        self, db_session, search_query
    ):
        self._open_crispin_connection(db_session)

        try:
            criteria = [b"TEXT", search_query.encode("ascii")]
            charset = None
        except UnicodeEncodeError:
            criteria = ["TEXT", search_query]
            charset = "UTF-8"

        folders = []

        account_folders = db_session.query(Folder).filter(
            Folder.account_id == self.account_id
        )

        # We want to start the search with the 'inbox', 'sent'
        # and 'archive' folders, if they exist.
        for cname in ["inbox", "sent", "archive"]:
            special_folder = (
                db_session.query(Folder)
                .filter(
                    Folder.account_id == self.account_id,
                    Folder.canonical_name  # type: ignore[comparison-overlap]
                    == cname,
                )
                .one_or_none()
            )

            if special_folder is not None:
                folders.append(special_folder)

                # Don't search the folder twice.
                account_folders = account_folders.filter(
                    Folder.id != special_folder.id
                )

        folders = folders + account_folders.all()

        for folder in folders:
            yield self._search_folder(folder, criteria, charset)

        self._close_crispin_connection()

    def _search_folder(  # type: ignore[no-untyped-def]
        self, folder, criteria, charset
    ):
        try:
            self.crispin_client.select_folder(folder.name, uidvalidity_cb)
        except FolderMissingError:
            self.log.warning("Won't search missing IMAP folder", exc_info=True)
            return []
        except UidInvalid:
            self.log.error(  # noqa: G201
                ("Got Uidvalidity error when searching. Skipping."),
                exc_info=True,
            )
            return []

        try:
            uids = self.crispin_client.conn.search(criteria, charset=charset)
        except IMAP4.error:
            self.log.warning("Search error", exc_info=True)
            raise SearchBackendException(  # noqa: B904
                ("Unknown IMAP error when performing search."), 503
            )

        self.log.debug(
            "Search found messages for folder",
            folder_name=folder.id,
            uids=len(uids),
        )
        return uids
