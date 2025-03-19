import imapclient  # type: ignore[import-untyped]

from inbox.crispin import FolderMissingError, connection_pool
from inbox.logging import get_logger
from inbox.mailsync.backends.imap.generic import uidvalidity_cb
from inbox.s3.exc import EmailDeletedException, EmailFetchException

log = get_logger()


def get_imap_raw_contents(message):  # type: ignore[no-untyped-def]  # noqa: ANN201
    account = message.namespace.account

    if len(message.imapuids) == 0:
        raise EmailDeletedException(
            "Message was deleted on the backend server."
        )

    uid = message.imapuids[0]
    folder = uid.folder

    with connection_pool(account.id).get() as crispin_client:
        try:
            crispin_client.select_folder(folder.name, uidvalidity_cb)
        except FolderMissingError as exc:
            log.error(  # noqa: G201
                "Error while fetching raw contents: can't find folder",
                exc_info=True,
                logstash_tag="fetching_error",
            )
            raise EmailFetchException(
                "Folder containing email cannot be found or accessed on the"
                " backend server."
            ) from exc

        try:
            uids = crispin_client.uids([uid.msg_uid])
            if len(uids) == 0:
                raise EmailDeletedException(
                    "Message was deleted on the backend server."
                )

            return uids[0].body
        except imapclient.IMAPClient.Error:
            log.error(  # noqa: G201
                "Error while fetching raw contents",
                exc_info=True,
                logstash_tag="fetching_error",
            )
            raise EmailFetchException(  # noqa: B904
                "Couldn't get message from server. "
                "Please try again in a few minutes."
            )
