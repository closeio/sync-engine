import imapclient

from inbox.crispin import connection_pool
from inbox.logging import get_logger
from inbox.mailsync.backends.imap.generic import uidvalidity_cb
from inbox.s3.exc import EmailDeletedException, EmailFetchException

log = get_logger()


def get_imap_raw_contents(message):
    account = message.namespace.account

    if len(message.imapuids) == 0:
        raise EmailDeletedException(
            "Message was deleted on the backend server."
        )

    uid = message.imapuids[0]
    folder = uid.folder

    with connection_pool(account.id).get() as crispin_client:
        crispin_client.select_folder(folder.name, uidvalidity_cb)

        try:
            uids = crispin_client.uids([uid.msg_uid])
            if len(uids) == 0:
                raise EmailDeletedException(
                    "Message was deleted on the backend server."
                )

            return uids[0].body
        except imapclient.IMAPClient.Error:
            log.error(
                "Error while fetching raw contents",
                exc_info=True,
                logstash_tag="fetching_error",
            )
            raise EmailFetchException(
                "Couldn't get message from server. "
                "Please try again in a few minutes."
            )
