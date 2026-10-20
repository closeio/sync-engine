"""Operations for syncing back local datastore changes to Gmail."""

import contextlib
from collections import defaultdict
from imaplib import IMAP4

import imapclient  # type: ignore[import-untyped]

from inbox.crispin import CONN_UNUSABLE_EXC_CLASSES
from inbox.logging import get_logger
from inbox.mailsync.backends.imap.generic import uidvalidity_cb
from inbox.models import Folder
from inbox.models.action_log import FolderModseq
from inbox.models.backends.imap import ImapUid
from inbox.models.category import Category
from inbox.models.session import session_scope

log = get_logger()

PROVIDER = "gmail"

__all__ = ["remote_create_label", "remote_update_label", "remote_delete_label"]


def _encode_labels(labels):  # type: ignore[no-untyped-def]
    return [imapclient.imap_utf7.encode(label) for label in labels]


def remote_change_labels(  # type: ignore[no-untyped-def]
    crispin_client, account_id, message_ids, removed_labels, added_labels
) -> dict[int, dict[str, FolderModseq]]:
    """
    Change the labels of the messages in Gmail.

    Return the MODSEQ of each message after the change, with the UIDVALIDITY
    of the folder, by message ID and then by folder ID. The folder IDs are
    strings. If a message has more than one UID in a folder, the result has
    the lowest MODSEQ of these UIDs. If the fetch after the change fails, and
    the connection stays usable, the result has no values for that folder.
    """
    with session_scope(account_id) as db_session:
        rows = (
            db_session.query(
                ImapUid.message_id, ImapUid.msg_uid, Folder.id, Folder.name
            )
            .join(Folder)
            .filter(ImapUid.message_id.in_(list(set(message_ids))))
            .all()
        )
    uids_by_folder_name: dict[str, list[int]] = defaultdict(list)
    folder_id_by_name: dict[str, int] = {}
    message_id_by_folder_uid: dict[tuple[str, int], int] = {}
    for message_id, msg_uid, folder_id, folder_name in rows:
        uids_by_folder_name[folder_name].append(msg_uid)
        folder_id_by_name[folder_name] = folder_id
        message_id_by_folder_uid[folder_name, msg_uid] = message_id

    modseqs: dict[int, dict[str, FolderModseq]] = defaultdict(dict)
    for folder_name, uids in uids_by_folder_name.items():
        crispin_client.select_folder_if_necessary(folder_name, uidvalidity_cb)
        if len(added_labels) > 0:
            crispin_client.conn.add_gmail_labels(
                uids, _encode_labels(added_labels), silent=True
            )
        if len(removed_labels) > 0:
            crispin_client.conn.remove_gmail_labels(
                uids, _encode_labels(removed_labels), silent=True
            )
        # Fetch on the same connection after the change, so that the values
        # include the change.
        try:
            remote_flags = crispin_client.flags(uids)
        except CONN_UNUSABLE_EXC_CLASSES:
            raise
        except Exception:
            # The label change is already in Gmail, so do not raise. Return
            # no MODSEQ for this folder.
            log.warning(
                "Could not fetch MODSEQ after label change",
                account_id=account_id,
                folder_name=folder_name,
                message_ids=sorted({
                    message_id_by_folder_uid[folder_name, uid] for uid in uids
                }),
                exc_info=True,
            )
            continue
        folder_key = str(folder_id_by_name[folder_name])
        uidvalidity = crispin_client.selected_uidvalidity
        for uid, uid_flags in remote_flags.items():
            if uid_flags.modseq is None:
                continue
            message_id = message_id_by_folder_uid[folder_name, uid]
            # Data fetched after the change has at least the MODSEQ of its
            # UID here. A STORE that changes nothing keeps the old MODSEQ, so
            # this is the only rule for all UIDs. Keep the lowest MODSEQ of
            # the UIDs of a message, so that no data fetched after the change
            # is lower than the result.
            current_folder_modseq = modseqs[message_id].get(folder_key)
            if (
                current_folder_modseq is None
                or uid_flags.modseq < current_folder_modseq["modseq"]
            ):
                modseqs[message_id][folder_key] = FolderModseq(
                    uidvalidity=uidvalidity, modseq=uid_flags.modseq
                )
    return dict(modseqs)


def remote_create_label(  # type: ignore[no-untyped-def]
    crispin_client, account_id, category_id
) -> None:
    with session_scope(account_id) as db_session:
        category = db_session.query(Category).get(category_id)
        if category is None:
            return
        display_name = category.display_name
    crispin_client.conn.create_folder(display_name)


def remote_update_label(  # type: ignore[no-untyped-def]
    crispin_client, account_id, category_id, old_name, new_name
) -> None:
    crispin_client.conn.rename_folder(old_name, new_name)


def remote_delete_label(  # type: ignore[no-untyped-def]
    crispin_client, account_id, category_id
) -> None:
    with session_scope(account_id) as db_session:
        category = db_session.query(Category).get(category_id)
        if category is None:
            return
        display_name = category.display_name

    with contextlib.suppress(IMAP4.error):
        # IMAP4.error: Label has already been deleted on remote. Treat delete
        # as no-op.
        crispin_client.conn.delete_folder(display_name)

    # TODO @karim --- the main sync loop has a hard time detecting
    # Gmail renames because of a Gmail issue (see https://github.com/nylas/sync-engine/blob/c99656df3c048faf7951e54d74cb5ef9d7dc3c97/inbox/mailsync/gc.py#L146 for more details).
    # Fix the problem and then remove the following shortcut.
    with session_scope(account_id) as db_session:
        category = db_session.query(Category).get(category_id)
        db_session.delete(category)
        db_session.commit()
