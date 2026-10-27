"""Operations for syncing back local datastore changes to Gmail."""

import contextlib
from collections import defaultdict
from imaplib import IMAP4
from typing import NamedTuple

import imapclient  # type: ignore[import-untyped]
from sqlalchemy.orm import (  # type: ignore[import-untyped]
    Session,
    selectinload,
)

from inbox.crispin import CONN_UNUSABLE_EXC_CLASSES, GmailFlags
from inbox.logging import get_logger
from inbox.mailsync.backends.imap.generic import uidvalidity_cb
from inbox.models.action_log import FolderModseq
from inbox.models.backends.imap import ImapUid, ImapUidState, LabelItem
from inbox.models.category import Category
from inbox.models.session import session_scope

log = get_logger()

PROVIDER = "gmail"

__all__ = ["remote_create_label", "remote_update_label", "remote_delete_label"]


def _encode_labels(labels):  # type: ignore[no-untyped-def]
    return [imapclient.imap_utf7.encode(label) for label in labels]


class LabelChangeResult(NamedTuple):
    """The result of a label change in Gmail."""

    # The MODSEQ of each message after the change, with the UIDVALIDITY of
    # the folder, by message ID and then by folder ID.
    modseqs: dict[int, dict[str, FolderModseq]]
    # The UIDs that the label change found for each message, by message ID.
    # Each UID is a pair of folder ID and UID.
    uids_by_message_id: dict[int, set[tuple[int, int]]]
    # The state in the IMAP rows of each UID before the change, by folder ID
    # and UID.
    states_before_change: dict[tuple[int, int], ImapUidState]
    # The flags, the labels, and the MODSEQ of each UID after the change, by
    # folder ID and UID. A UID without data from the fetch after the change
    # is not in this dict.
    fetched_flags: dict[tuple[int, int], GmailFlags]


def save_fetched_imap_rows(
    db_session: Session, account_id: int, result: LabelChangeResult
) -> set[int]:
    """
    Save the flags and the labels in `result.fetched_flags` in the IMAP rows
    of their UIDs. Return the IDs of the messages whose IMAP rows the
    function saved for all their UIDs in `result.uids_by_message_id`. The
    function does not commit.

    Skip the IMAP row of a UID if it belongs to another message than in
    `result.uids_by_message_id`. For each other row, use the first rule that
    applies to its `ImapUidState`:

    - If the row still has its state in `result.states_before_change`, save
      the fetched data in the row.
    - If the row already has the state of the fetched data, count the row as
      saved, and do not change it. Thus, newer data in its other columns
      stays.
    - Otherwise, keep the row, and log it. Another session changed the row
      after the label change read it. Thus, the row can be newer than the
      fetched data.

    The rules compare only the `ImapUidState`. If another session saves
    newer data with the state in `result.states_before_change`, the first
    rule saves the older fetched data over it.
    """
    message_id_by_folder_uid = {
        folder_uid: message_id
        for message_id, folder_uids in result.uids_by_message_id.items()
        for folder_uid in folder_uids
    }
    msg_uids_by_folder_id: dict[int, list[int]] = defaultdict(list)
    for folder_id, msg_uid in result.fetched_flags:
        msg_uids_by_folder_id[folder_id].append(msg_uid)

    saved_folder_uids: set[tuple[int, int]] = set()
    for folder_id, msg_uids in msg_uids_by_folder_id.items():
        imapuids = (
            db_session.query(ImapUid)
            .filter(
                ImapUid.account_id == account_id,
                ImapUid.folder_id == folder_id,
                ImapUid.msg_uid.in_(msg_uids),
            )
            .with_hint(
                ImapUid,
                "FORCE INDEX (ix_imapuid_account_id_folder_id_msg_uid_desc)",
            )
            # Load the label rows of all UIDs in one query.
            .options(
                selectinload(
                    ImapUid.labelitems  # type: ignore[attr-defined]
                ).joinedload(LabelItem.label)
            )
        )
        for imapuid in imapuids:
            folder_uid = (folder_id, imapuid.msg_uid)
            # A resync of the folder can give the UID to another message after
            # the label change read the UIDs.
            if message_id_by_folder_uid.get(folder_uid) != imapuid.message_id:
                continue
            uid_flags = result.fetched_flags[folder_uid]
            state_before_change = result.states_before_change[folder_uid]
            saved_state = imapuid.saved_state()
            fetched_state = ImapUidState.from_flags_and_labels(
                uid_flags.flags, uid_flags.labels
            )
            if saved_state == state_before_change:
                imapuid.update_flags(list(uid_flags.flags))
                imapuid.update_labels(uid_flags.labels)
                # Flush, so that the next UID finds a label row that this UID
                # created.
                db_session.flush()
                saved_folder_uids.add(folder_uid)
            elif saved_state == fetched_state:
                saved_folder_uids.add(folder_uid)
            else:
                fetched_only_label_names, saved_only_label_names = (
                    imapuid.compare_labels(uid_flags.labels)
                )
                log.info(
                    "Kept IMAP rows that changed during the label change",
                    account_id=account_id,
                    message_id=imapuid.message_id,
                    folder_id=folder_id,
                    msg_uid=imapuid.msg_uid,
                    fetched_only_label_names=sorted(fetched_only_label_names),
                    saved_only_label_names=sorted(saved_only_label_names),
                    saved_is_seen=saved_state.is_seen,
                    fetched_is_seen=fetched_state.is_seen,
                    saved_is_flagged=saved_state.is_flagged,
                    fetched_is_flagged=fetched_state.is_flagged,
                    # The state before the change shows what the other
                    # session changed in the row. A label with a canonical
                    # name gives its canonical name.
                    before_change_label_names=sorted(
                        key for _, key in state_before_change.label_identities
                    ),
                    before_change_is_seen=state_before_change.is_seen,
                    before_change_is_flagged=state_before_change.is_flagged,
                )

    return {
        message_id
        for message_id, folder_uids in result.uids_by_message_id.items()
        if folder_uids <= saved_folder_uids
    }


def remote_change_labels(  # type: ignore[no-untyped-def]
    crispin_client, account_id, message_ids, removed_labels, added_labels
) -> LabelChangeResult:
    """
    Change the labels of the messages in Gmail. Before the change, read the
    state in the IMAP rows of each UID. After the change, fetch the flags,
    the labels, and the MODSEQ of each UID.

    In `LabelChangeResult.modseqs`, the folder IDs are strings. If a message
    has more than one UID in a folder, the result has the lowest MODSEQ of
    these UIDs. If the fetch after the change fails, and the connection stays
    usable, the result has no values for that folder.
    """
    uids_by_folder_name: dict[str, list[int]] = defaultdict(list)
    folder_id_by_name: dict[str, int] = {}
    message_id_by_folder_uid: dict[tuple[str, int], int] = {}
    uids_by_message_id: dict[int, set[tuple[int, int]]] = defaultdict(set)
    states_before_change: dict[tuple[int, int], ImapUidState] = {}
    with session_scope(account_id) as db_session:
        imapuids = (
            db_session.query(ImapUid)
            .filter(ImapUid.message_id.in_(list(set(message_ids))))
            # Load the label rows of all UIDs in one query.
            .options(
                selectinload(
                    ImapUid.labelitems  # type: ignore[attr-defined]
                ).joinedload(LabelItem.label)
            )
            .all()
        )
        for imapuid in imapuids:
            folder_name = imapuid.folder.name
            folder_uid = (imapuid.folder_id, imapuid.msg_uid)
            uids_by_folder_name[folder_name].append(imapuid.msg_uid)
            folder_id_by_name[folder_name] = imapuid.folder_id
            message_id_by_folder_uid[folder_name, imapuid.msg_uid] = (
                imapuid.message_id
            )
            uids_by_message_id[imapuid.message_id].add(folder_uid)
            states_before_change[folder_uid] = imapuid.saved_state()

    modseqs: dict[int, dict[str, FolderModseq]] = defaultdict(dict)
    fetched_flags: dict[tuple[int, int], GmailFlags] = {}
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
            # no fetched data for this folder.
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
        folder_id = folder_id_by_name[folder_name]
        folder_key = str(folder_id)
        uidvalidity = crispin_client.selected_uidvalidity
        for uid, uid_flags in remote_flags.items():
            fetched_flags[folder_id, uid] = uid_flags
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
    return LabelChangeResult(
        modseqs=dict(modseqs),
        uids_by_message_id=dict(uids_by_message_id),
        states_before_change=states_before_change,
        fetched_flags=fetched_flags,
    )


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
