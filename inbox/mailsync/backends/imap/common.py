"""
Helper functions for actions that operate on accounts.

These could be methods of ImapAccount, but separating them gives us more
flexibility with calling code, as most don't need any attributes of the account
object other than the ID, to limit the action.

Types returned for data are the column types defined via SQLAlchemy.

Eventually we're going to want a better way of ACLing functions that operate on
accounts.

"""

from collections import defaultdict
from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta
from typing import Literal, NamedTuple

from sqlalchemy import bindparam, desc  # type: ignore[import-untyped]
from sqlalchemy.orm import (  # type: ignore[import-untyped]
    Session,
    selectinload,
)
from sqlalchemy.orm.exc import NoResultFound  # type: ignore[import-untyped]

from inbox.contacts.processing import update_contacts_from_message
from inbox.crispin import Flags, GmailFlags, RawMessage
from inbox.logging import get_logger
from inbox.models import Account, ActionLog, Folder, Message, MessageCategory
from inbox.models.action_log import CHANGE_LABELS_MODSEQ_KEY
from inbox.models.backends.imap import ImapFolderInfo, ImapUid, LabelItem
from inbox.models.category import Category
from inbox.models.session import session_scope
from inbox.models.util import reconcile_message
from inbox.sqlalchemy_ext.util import get_db_api_cursor_with_query

log = get_logger()

# Maximum age of a pending `change_labels` action that keeps the categories
# of its message. Thus, a stuck action cannot hide the IMAP labels of its
# message for longer than this. Clock differences between hosts do not matter
# at this size.
PENDING_LABEL_CHANGE_MAX_AGE = timedelta(minutes=30)

# Maximum time after the end of a successful `change_labels` action for which
# its saved MODSEQ values count. In this time only, a MODSEQ reset without a
# UIDVALIDITY change can make fresh IMAP data look older than the action.
LABEL_CHANGE_MODSEQ_MAX_AGE = timedelta(hours=1)

# Reason to keep the current categories of a message of a label account,
# instead of the categories from its IMAP labels.
KeepCategoriesReason = Literal["pending_label_change", "stale_imap_data"]


class LabelChangeCheck(NamedTuple):
    """The result of `_check_label_change`."""

    # The categories of the message before the queries for actions.
    current_categories: set[Category]
    # The reason to keep `current_categories`, or `None` if the IMAP labels
    # can replace them.
    keep_reason: KeepCategoriesReason | None


def local_uids(  # type: ignore[no-untyped-def]
    account_id: int, session, folder_id: int, limit: "int | None" = None
) -> "set[int]":
    """
    Get the local UIDs of all messages in a folder.

    Note that these days a lot email inboxes can have millions of messages in it,
    and we prefer to skip SQLAlchemy's ORM layer when fetching these UIDs
    from the database as it's a lot faster.
    """
    q = session.query(ImapUid.msg_uid).with_hint(
        ImapUid, "FORCE INDEX (ix_imapuid_account_id_folder_id_msg_uid_desc)"
    )
    q = q.filter(
        ImapUid.account_id == bindparam("account_id"),
        ImapUid.folder_id == bindparam("folder_id"),
    )
    if limit:
        q = q.order_by(desc(ImapUid.msg_uid))
        q = q.limit(bindparam("limit"))
    q = q.params(account_id=account_id, folder_id=folder_id, limit=limit)

    # We're using a raw DB-API cursor here to avoid the overhead of the ORM.
    db_api_cursor = get_db_api_cursor_with_query(session, q)

    return {uid for (uid,) in db_api_cursor.fetchall()}


def lastseenuid(  # type: ignore[no-untyped-def]  # noqa: ANN201
    account_id, session, folder_id
):
    q = (
        session.query(ImapUid.msg_uid)
        .with_hint(
            ImapUid,
            "FORCE INDEX (ix_imapuid_account_id_folder_id_msg_uid_desc)",
        )
        .filter(
            ImapUid.account_id == bindparam("account_id"),
            ImapUid.folder_id == bindparam("folder_id"),
        )
        .order_by(ImapUid.msg_uid.desc())
        .limit(1)
    )
    res = q.params(account_id=account_id, folder_id=folder_id).first()
    return res[0] if res else 0


def _has_pending_label_change(session: Session, message: Message) -> bool:
    """
    Return whether a `change_labels` action for the message is pending and
    is at most `PENDING_LABEL_CHANGE_MAX_AGE` old.
    """
    query = session.query(ActionLog.id).filter(
        ActionLog.status == "pending",
        ActionLog.namespace_id == message.namespace_id,
        ActionLog.record_id == message.id,
        ActionLog.table_name == "message",
        ActionLog.action == "change_labels",
        ActionLog.discriminator == "actionlog",
        ActionLog.created_at
        >= datetime.utcnow() - PENDING_LABEL_CHANGE_MAX_AGE,
    )
    return session.query(query.exists()).scalar()


def _pending_flag_actions(
    session: Session, namespace_id: int, message_ids: set[int]
) -> dict[int, set[str]]:
    """
    Return the names of the pending `mark_unread` and `mark_starred` actions
    of the messages, by message ID.
    """
    rows = session.query(ActionLog.record_id, ActionLog.action).filter(
        ActionLog.status == "pending",
        ActionLog.namespace_id == namespace_id,
        ActionLog.record_id.in_(message_ids),
        ActionLog.table_name == "message",
        ActionLog.action.in_(["mark_unread", "mark_starred"]),
        ActionLog.discriminator == "actionlog",
    )
    actions_by_message_id: dict[int, set[str]] = defaultdict(set)
    for message_id, action in rows:
        actions_by_message_id[message_id].add(action)
    return dict(actions_by_message_id)


def _is_older_than_label_change(
    session: Session, message: Message, folder_id: int, modseq: int
) -> bool:
    """
    Return whether IMAP data with `modseq` in the folder with the ID
    `folder_id` is older than a successful `change_labels` action for the
    message.

    Each action counts with the MODSEQ that it saved for that folder, if the
    UIDVALIDITY that it saved is the current UIDVALIDITY of the folder.
    Actions that finished more than `LABEL_CHANGE_MODSEQ_MAX_AGE` ago do not
    count.
    """
    rows = session.query(ActionLog.extra_args).filter(
        ActionLog.status == "successful",
        ActionLog.namespace_id == message.namespace_id,
        ActionLog.record_id == message.id,
        ActionLog.table_name == "message",
        ActionLog.action == "change_labels",
        ActionLog.discriminator == "actionlog",
        ActionLog.updated_at
        >= datetime.utcnow() - LABEL_CHANGE_MODSEQ_MAX_AGE,
    )
    # The UIDVALIDITY values that go with the saved MODSEQ values that are
    # higher than `modseq`.
    uidvalidities_of_higher_modseqs: set[int] = set()
    for row in rows:
        if not row.extra_args:
            continue
        folder_modseq = row.extra_args.get(CHANGE_LABELS_MODSEQ_KEY, {}).get(
            str(folder_id)
        )
        if folder_modseq is not None and modseq < folder_modseq["modseq"]:
            uidvalidities_of_higher_modseqs.add(folder_modseq["uidvalidity"])
    if not uidvalidities_of_higher_modseqs:
        return False
    # MODSEQ values compare only under the same UIDVALIDITY. Read the current
    # UIDVALIDITY only here, because few calls get this far.
    current_uidvalidity = (
        session.query(ImapFolderInfo.uidvalidity)
        .filter(ImapFolderInfo.folder_id == folder_id)
        .scalar()
    )
    return current_uidvalidity in uidvalidities_of_higher_modseqs


def _check_label_change(
    session: Session,
    message: Message,
    fetched_folder_id: int | None,
    fetched_modseq: int | None,
) -> LabelChangeCheck:
    """
    Return the current categories of the message, and the reason to keep
    them instead of the categories from its IMAP labels.

    Keep them while a `change_labels` action for the message is pending (see
    `_has_pending_label_change`). Also keep them if the IMAP data with
    `fetched_modseq` in the folder with the ID `fetched_folder_id` is older
    than a successful `change_labels` action for the message (see
    `_is_older_than_label_change`).
    """
    # Read the categories before the queries for actions. The API commits a
    # label change together with its action. Thus, if the categories show the
    # change, the queries also find the action.
    current_categories = set(message.categories)
    # Query for a successful action only after the query for a pending one.
    # An action becomes `successful` only after it was `pending`. Thus, if the
    # first query does not find an action because it is already `successful`,
    # the second query finds it, with its `CHANGE_LABELS_MODSEQ_KEY` values.
    if _has_pending_label_change(session, message):
        return LabelChangeCheck(current_categories, "pending_label_change")
    if (
        fetched_folder_id is not None
        and fetched_modseq is not None
        and _is_older_than_label_change(
            session, message, fetched_folder_id, fetched_modseq
        )
    ):
        return LabelChangeCheck(current_categories, "stale_imap_data")
    return LabelChangeCheck(current_categories, None)


def _category_log_names(categories: Iterable[Category | None]) -> list[str]:
    """
    Return the sorted names of the categories for a log line. A category of
    a custom label has no canonical name, so it gives its display name.
    """
    # A `MessageCategory` row of a deleted category gives `None`.
    return sorted(
        category.name or category.display_name
        for category in categories
        if category is not None
    )


def update_message_metadata(
    session: Session,
    account: Account,
    message: Message,
    is_draft: bool,
    *,
    fetched_folder_id: int | None = None,
    fetched_modseq: int | None = None,
    label_change_check: LabelChangeCheck | None = None,
) -> None:
    """
    Update the message's metadata.

    For a label account, keep the current categories if
    `_check_label_change` gives a reason to keep them. Use
    `label_change_check` if the caller gives it, instead of a new check.
    """
    # Sort imapuids in a way that the ones that were added later come last
    now = datetime.utcnow()
    sorted_imapuids: list[ImapUid] = sorted(
        message.imapuids,  # type: ignore[attr-defined]
        key=lambda imapuid: imapuid.updated_at or now,
    )

    message.is_read = any(imapuid.is_seen for imapuid in sorted_imapuids)
    message.is_starred = any(imapuid.is_flagged for imapuid in sorted_imapuids)
    message.is_draft = is_draft

    sorted_categories: list[Category] = [
        category
        for imapuid in sorted_imapuids
        for category in imapuid.categories
    ]

    categories: set[Category]
    if account.category_type == "folder":
        # For generic IMAP we want to deterministically select the last category.
        # A message will always be in a single folder but it seems that for some
        # on-prem servers we are not able to reliably detect when a message is moved
        # between folders and we end up with many folders in our MySQL.
        # Such message used to undeterministically appear in one of those folders
        # (and in turn one category) depending on the order they were returned
        # from the database. This makes it deterministic and more-correct because a message
        # is likely in a folder (and category) it was added to last.
        categories = {sorted_categories[-1]} if sorted_categories else set()
    elif account.category_type == "label":
        categories = set(sorted_categories)
    else:
        raise AssertionError("Unreachable")

    current_categories: set[Category]
    keep_reason: KeepCategoriesReason | None
    if label_change_check is None:
        current_categories = set(message.categories)
        keep_reason = None
        # If the IMAP labels give the current categories, the result is the
        # same with or without the queries for actions. Thus, the queries run
        # only if the categories are different.
        if (
            account.category_type == "label"
            and message.id is not None
            and categories != current_categories
        ):
            current_categories, keep_reason = _check_label_change(
                session, message, fetched_folder_id, fetched_modseq
            )
    else:
        current_categories, keep_reason = label_change_check
    if keep_reason is not None and categories != current_categories:
        # `message.categories` already has the change of the last
        # `change_labels` action, but the IMAP labels can be older than that
        # change. Thus, keep the current categories. Other label changes in
        # the IMAP labels stay out of the categories until a later call that
        # keeps no categories.
        log.info(
            "Kept message categories during a label change",
            account_id=account.id,
            message_id=message.id,
            reason=keep_reason,
            imap_only_category_names=_category_log_names(
                categories - current_categories
            ),
            local_only_category_names=_category_log_names(
                current_categories - categories
            ),
        )
        categories = current_categories

    # Use a consistent time across creating categories, message updated_at
    # and the subsequent transaction that may be created.
    update_time = datetime.utcnow()

    # XXX: This will overwrite local state for a folder account with a
    # pending `move` action, and for a label account with a pending
    # `change_labels` action older than `PENDING_LABEL_CHANGE_MAX_AGE`.
    # The eventual state is correct.
    # XXX: Don't just overwrite message categories but specifically add new
    # ones and remove old ones. That way we don't re-create them, which both
    # saves on database queries and also lets use rely on the message category
    # creation timestamp to determine when the message was added to a category.
    old_categories = [
        category
        for category in message.categories
        if category not in categories
    ]
    new_categories = [
        category
        for category in categories
        if category not in message.categories
    ]
    for category in old_categories:
        message.categories.remove(category)
    for category in new_categories:
        # message.categories.add(category)
        # Explicitly create association record so we can control the
        # created_at value. Taken from
        # https://docs.sqlalchemy.org/en/13/orm/extensions/
        # associationproxy.html#simplifying-association-objects
        MessageCategory(  # type: ignore[call-arg]
            category=category, message=message, created_at=update_time
        )

    # Update the message updated_at field so that it can be used in
    # the transaction that will be created for category changes.
    # Although no data actually changes for the message
    # record in MySQL, Nylas expresses category changes as message changes
    # in transactions. Since we are explicitly setting updated_at we should
    # be able to assume even if message gets changed later in this
    # transaction it will still use the time set here which will match the
    # category change times. This will cause the message row to be updated
    # even though only the categories may have changed and are stored in
    # a different table.
    if old_categories or new_categories:
        message.updated_at = update_time


def _update_uid_metadata(
    session: Session,
    imapuid: ImapUid,
    folder_id: int,
    uid_flags: GmailFlags | Flags,
    is_label_account: bool,
) -> tuple[bool, LabelChangeCheck | None]:
    """
    Save the fetched flags and labels of the UID in its IMAP rows. Return
    whether the message of the UID needs an update, and the label change
    check of the message, if the function made one. A UID with fetched
    labels always needs an update of its message.

    For a label account, check the label change of the message if the
    fetched labels differ from the saved labels. Keep the saved flags and
    labels if the fetched data is older than a successful `change_labels`
    action for the message, and log the labels that differ.
    """
    labels = getattr(uid_flags, "labels", None)
    label_change_check: LabelChangeCheck | None = None
    if labels is not None and is_label_account:
        fetched_only_label_names, saved_only_label_names = (
            imapuid.compare_labels(labels)
        )
        if fetched_only_label_names or saved_only_label_names:
            label_change_check = _check_label_change(
                session, imapuid.message, folder_id, uid_flags.modseq
            )
            keep_reason = label_change_check.keep_reason
            # The reason `pending_label_change` does not tell whether the
            # fetched data is older than a successful action. Thus, query for
            # it here.
            if keep_reason == "stale_imap_data" or (
                keep_reason == "pending_label_change"
                and uid_flags.modseq is not None
                and _is_older_than_label_change(
                    session, imapuid.message, folder_id, uid_flags.modseq
                )
            ):
                log.info(
                    "Kept IMAP rows newer than the fetched data",
                    account_id=imapuid.account_id,
                    message_id=imapuid.message_id,
                    folder_id=folder_id,
                    reason=keep_reason,
                    fetched_only_label_names=sorted(fetched_only_label_names),
                    saved_only_label_names=sorted(saved_only_label_names),
                )
                return True, label_change_check

    # TODO(emfree) refactor so this is only ever relevant for Gmail.
    changed = imapuid.update_flags(list(uid_flags.flags))
    if labels is not None:
        imapuid.update_labels(labels)
        changed = True
    return changed, label_change_check


def update_metadata(  # type: ignore[no-untyped-def]
    account_id, folder_id, folder_role, new_flags, session
) -> None:
    """
    Update flags and labels (the only metadata that can change).

    Make sure you're holding a db write lock on the account. (We don't try
    to grab the lock in here in case the caller needs to put higher-level
    functionality in the lock.)

    """
    if not new_flags:
        return

    account = Account.get(account_id, session)
    is_label_account = account.category_type == "label"
    change_count = 0
    for item in (
        session.query(ImapUid)
        .filter(
            ImapUid.account_id == account_id,
            ImapUid.folder_id == folder_id,
            ImapUid.msg_uid.in_(new_flags),
        )
        .with_hint(
            ImapUid,
            "FORCE INDEX (ix_imapuid_account_id_folder_id_msg_uid_desc)",
        )
    ):
        uid_flags = new_flags[item.msg_uid]
        # Do not commit between the label change check and
        # `update_message_metadata`. Thus, the categories in the check stay
        # current.
        changed, label_change_check = _update_uid_metadata(
            session, item, folder_id, uid_flags, is_label_account
        )
        if changed:
            change_count += 1
            is_draft = item.is_draft and folder_role in ["drafts", "all"]
            update_message_metadata(
                session,
                account,
                item.message,
                is_draft,
                fetched_folder_id=folder_id,
                fetched_modseq=uid_flags.modseq,
                label_change_check=label_change_check,
            )
            session.commit()
    log.info(
        "Updated UID metadata", changed=change_count, out_of=len(new_flags)
    )


def _find_uid_with_changed_data(
    message: Message,
    fetched_flags: Mapping[tuple[int, int], GmailFlags],
) -> ImapUid | None:
    """
    Return a UID of the message that has no data in `fetched_flags`, by
    folder ID and UID, or whose IMAP rows differ from this data. Compare the
    label rows, and the flags that give the read and the starred state.
    Return `None` if the message has no such UID.
    """
    for imapuid in message.imapuids:  # type: ignore[attr-defined]
        uid_flags = fetched_flags.get((imapuid.folder_id, imapuid.msg_uid))
        if uid_flags is None:
            return imapuid
        fetched_only_label_names, saved_only_label_names = (
            imapuid.compare_labels(uid_flags.labels)
        )
        if (
            fetched_only_label_names
            or saved_only_label_names
            or imapuid.is_seen != (b"\\Seen" in uid_flags.flags)
            or imapuid.is_flagged != (b"\\Flagged" in uid_flags.flags)
        ):
            return imapuid
    return None


def rebuild_message_metadata_from_imap_rows(
    session: Session,
    account_id: int,
    message_ids: set[int],
    *,
    fetched_flags: Mapping[tuple[int, int], GmailFlags],
) -> None:
    """
    Rebuild the categories of the messages from their IMAP label rows, and
    their read and starred state from the flags of their IMAP rows. The
    function does not commit.

    `fetched_flags` has the flags and the labels of each UID, by folder ID
    and UID. Skip a message if one of its UIDs has no data in
    `fetched_flags`, or IMAP rows that differ from this data. Log the
    categories of that UID.

    Keep the read state of a message while a `mark_unread` action for it is
    pending, and its starred state while a `mark_starred` action for it is
    pending.
    """
    if not message_ids:
        return
    account = Account.get(account_id, session)
    # Load the messages before the query for pending actions. The API commits
    # a flag change together with its action. Thus, if a loaded flag shows
    # the change, the query also finds the action.
    messages = (
        session.query(Message)
        .filter(Message.id.in_(message_ids))
        # Load the UIDs of all messages, their label rows, and the categories
        # of the messages together.
        .options(
            selectinload(Message.imapuids)  # type: ignore[attr-defined]
            .selectinload(ImapUid.labelitems)  # type: ignore[attr-defined]
            .joinedload(LabelItem.label),
            selectinload(
                Message.messagecategories  # type: ignore[attr-defined]
            ).joinedload(MessageCategory.category),
        )
        .all()
    )
    pending_flag_actions = _pending_flag_actions(
        session, account.namespace.id, message_ids
    )
    for message in messages:
        # The comparison and the rebuild read the same loaded rows. Thus, a
        # change to the rows after the comparison does not reach the rebuild.
        changed_imapuid = _find_uid_with_changed_data(message, fetched_flags)
        if changed_imapuid is not None:
            log.warning(
                "Skipped message rebuild with changed IMAP rows",
                account_id=account_id,
                message_id=message.id,
                folder_id=changed_imapuid.folder_id,
                msg_uid=changed_imapuid.msg_uid,
                imap_category_names=_category_log_names(
                    changed_imapuid.categories
                ),
            )
            continue
        old_categories = set(message.categories)
        old_is_read = message.is_read
        old_is_starred = message.is_starred
        update_message_metadata(session, account, message, message.is_draft)
        # The API can change a flag in the message before its action changes
        # the flag in Gmail.
        message_flag_actions = pending_flag_actions.get(message.id, set())
        if "mark_unread" in message_flag_actions:
            message.is_read = old_is_read
        if "mark_starred" in message_flag_actions:
            message.is_starred = old_is_starred
        new_categories = set(message.categories)
        if new_categories != old_categories:
            log.warning(
                "Rebuilt message categories after a change_labels action",
                account_id=account_id,
                message_id=message.id,
                added_category_names=_category_log_names(
                    new_categories - old_categories
                ),
                removed_category_names=_category_log_names(
                    old_categories - new_categories
                ),
            )
        if (message.is_read, message.is_starred) != (
            old_is_read,
            old_is_starred,
        ):
            log.info(
                "Rebuilt message flags after a change_labels action",
                account_id=account_id,
                message_id=message.id,
                is_read=message.is_read,
                is_starred=message.is_starred,
            )


def remove_deleted_uids(  # type: ignore[no-untyped-def]
    account_id, folder_id, uids
) -> None:
    """
    Make sure you're holding a db write lock on the account. (We don't try
    to grab the lock in here in case the caller needs to put higher-level
    functionality in the lock.)

    """
    if not uids:
        return
    deleted_uid_count = 0
    for uid in uids:
        # We do this one-uid-at-a-time because issuing many deletes within a
        # single database transaction is problematic. But loading many
        # objects into a session and then frequently calling commit() is also
        # bad, because expiring objects and checking for revisions is O(number
        # of objects in session), resulting in quadratic runtimes.
        # Performance could perhaps be additionally improved by choosing a
        # sane balance, e.g., operating on 10 or 100 uids or something at once.
        with session_scope(account_id) as db_session:
            imapuid = (
                db_session.query(ImapUid)
                .filter(
                    ImapUid.account_id == account_id,
                    ImapUid.folder_id == folder_id,
                    ImapUid.msg_uid == uid,
                )
                .with_hint(
                    ImapUid,
                    "FORCE INDEX (ix_imapuid_account_id_folder_id_msg_uid_desc)",
                )
                .first()
            )
            if imapuid is None:
                continue
            deleted_uid_count += 1
            message = imapuid.message

            db_session.delete(imapuid)

            if message is not None:
                if not message.imapuids and message.is_draft:
                    # Synchronously delete drafts.
                    thread = message.thread
                    if thread is not None:
                        thread.messages.remove(message)
                        # Thread.messages relationship is versioned i.e. extra
                        # logic gets executed on remove call.
                        # This early flush is needed so the configure_versioning logic
                        # in inbox.model.sessions can work reliably on newer versions of
                        # SQLAlchemy.
                        db_session.flush()
                    db_session.delete(message)
                    if thread is not None and not thread.messages:
                        db_session.delete(thread)
                else:
                    account = Account.get(account_id, db_session)
                    update_message_metadata(
                        db_session, account, message, message.is_draft
                    )
                    if not message.imapuids:
                        # But don't outright delete messages. Just mark them as
                        # 'deleted' and wait for the asynchronous
                        # dangling-message-collector to delete them.
                        message.mark_for_deletion()
            db_session.commit()
    log.info("Deleted expunged UIDs", count=deleted_uid_count)


def get_folder_info(  # type: ignore[no-untyped-def]  # noqa: ANN201
    account_id, session, folder_name
):
    try:
        # using .one() here may catch duplication bugs
        return (
            session.query(ImapFolderInfo)
            .join(Folder)
            .filter(
                ImapFolderInfo.account_id == account_id,
                Folder.name == folder_name,
            )
            .one()
        )
    except NoResultFound:
        return None


def create_imap_message(
    db_session: Session,
    account: Account,
    folder: Folder,
    raw_message: RawMessage,
) -> ImapUid:
    """
    IMAP-specific message creation logic.

    Returns
    -------
    imapuid : inbox.models.backends.imap.ImapUid
        New db object, which links to new Message and Block objects through
        relationships. All new objects are uncommitted.

    """
    log.debug(
        "creating message",
        account_id=account.id,
        folder_name=folder.name,
        mid=raw_message.uid,
    )
    new_message = Message.create_from_synced(
        account=account,
        imap_uid=raw_message.uid,
        folder_name=folder.name,
        received_date=raw_message.internaldate,
        body=raw_message.body,
    )

    # Check to see if this is a copy of a message that was first created
    # by the Nylas API. If so, don't create a new object; just use the old one.
    existing_copy = reconcile_message(new_message, db_session)
    if existing_copy is not None:
        new_message = existing_copy

    imapuid = ImapUid(  # type: ignore[call-arg]
        account=account,
        folder=folder,
        msg_uid=raw_message.uid,
        message=new_message,
    )
    imapuid.update_flags(raw_message.flags)  # type: ignore[arg-type]
    if raw_message.g_labels is not None:
        imapuid.update_labels(raw_message.g_labels)

    # Update the message's metadata
    with db_session.no_autoflush:
        is_draft = imapuid.is_draft and (
            folder.canonical_name in ("drafts", "all")
        )
        update_message_metadata(db_session, account, new_message, is_draft)

    update_contacts_from_message(
        db_session,
        new_message,
        account.namespace.id,  # type: ignore[attr-defined]
    )

    return imapuid
