"""
Helper functions for actions that operate on accounts.

These could be methods of ImapAccount, but separating them gives us more
flexibility with calling code, as most don't need any attributes of the account
object other than the ID, to limit the action.

Types returned for data are the column types defined via SQLAlchemy.

Eventually we're going to want a better way of ACLing functions that operate on
accounts.

"""
from future import standard_library

standard_library.install_aliases()
from datetime import datetime

from sqlalchemy import bindparam, desc
from sqlalchemy.orm.exc import NoResultFound
from sqlalchemy.sql.expression import func

from inbox.contacts.processing import update_contacts_from_message
from inbox.logging import get_logger
from inbox.models import Account, ActionLog, Folder, Message, MessageCategory
from inbox.models.backends.imap import ImapFolderInfo, ImapUid
from inbox.models.session import session_scope
from inbox.models.util import reconcile_message
from inbox.sqlalchemy_ext.util import bakery

log = get_logger()


def local_uids(account_id, session, folder_id, limit=None):
    q = bakery(lambda session: session.query(ImapUid.msg_uid))
    q += lambda q: q.filter(
        ImapUid.account_id == bindparam("account_id"),
        ImapUid.folder_id == bindparam("folder_id"),
    )
    if limit:
        q += lambda q: q.order_by(desc(ImapUid.msg_uid))
        q += lambda q: q.limit(bindparam("limit"))
    results = (
        q(session).params(account_id=account_id, folder_id=folder_id, limit=limit).all()
    )
    return {u for u, in results}


def lastseenuid(account_id, session, folder_id):
    q = bakery(lambda session: session.query(func.max(ImapUid.msg_uid)))
    q += lambda q: q.filter(
        ImapUid.account_id == bindparam("account_id"),
        ImapUid.folder_id == bindparam("folder_id"),
    )
    res = q(session).params(account_id=account_id, folder_id=folder_id).one()[0]
    return res or 0


def update_message_metadata(session, account, message, is_draft):
    # Update the message's metadata.
    uids = message.imapuids

    message.is_read = any(i.is_seen for i in uids)
    message.is_starred = any(i.is_flagged for i in uids)
    message.is_draft = is_draft

    categories = set()

    for i in uids:
        categories.update(i.categories)

    if account.category_type == "folder":
        categories = [_select_category(categories)] if categories else []

    # Use a consistent time across creating categories, message updated_at
    # and the subsequent transaction that may be created.
    update_time = datetime.utcnow()

    # XXX: This will overwrite local state if syncback actions are scheduled,
    # but the eventual state is correct.
    # XXX: Don't just overwrite message categories but specifically add new
    # ones and remove old ones. That way we don't re-create them, which both
    # saves on database queries and also lets use rely on the message category
    # creation timestamp to determine when the message was added to a category.
    old_categories = [c for c in message.categories if c not in categories]
    new_categories = [c for c in categories if c not in message.categories]
    for category in old_categories:
        message.categories.remove(category)
    for category in new_categories:
        # message.categories.add(category)
        # Explicitly create association record so we can control the
        # created_at value. Taken from
        # https://docs.sqlalchemy.org/en/13/orm/extensions/
        # associationproxy.html#simplifying-association-objects
        MessageCategory(category=category, message=message, created_at=update_time)

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

    """
    if not message.categories_changes:
        # No syncback actions scheduled, so there is no danger of
        # overwriting modified local state.
        message.categories = categories
    else:
        _update_categories(session, message, categories)
    """


def update_metadata(account_id, folder_id, folder_role, new_flags, session):
    """
    Update flags and labels (the only metadata that can change).

    Make sure you're holding a db write lock on the account. (We don't try
    to grab the lock in here in case the caller needs to put higher-level
    functionality in the lock.)

    """
    if not new_flags:
        return

    account = Account.get(account_id, session)
    change_count = 0
    for item in session.query(ImapUid).filter(
        ImapUid.account_id == account_id,
        ImapUid.msg_uid.in_(list(new_flags.keys())),
        ImapUid.folder_id == folder_id,
    ):
        flags = new_flags[item.msg_uid].flags
        labels = getattr(new_flags[item.msg_uid], "labels", None)

        # TODO(emfree) refactor so this is only ever relevant for Gmail.
        changed = item.update_flags(flags)
        if labels is not None:
            item.update_labels(labels)
            changed = True

        if changed:
            change_count += 1
            is_draft = item.is_draft and (
                folder_role == "drafts" or folder_role == "all"
            )
            update_message_metadata(session, account, item.message, is_draft)
            session.commit()
    log.info("Updated UID metadata", changed=change_count, out_of=len(new_flags))


def remove_deleted_uids(account_id, folder_id, uids):
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


def get_folder_info(account_id, session, folder_name):
    try:
        # using .one() here may catch duplication bugs
        return (
            session.query(ImapFolderInfo)
            .join(Folder)
            .filter(ImapFolderInfo.account_id == account_id, Folder.name == folder_name)
            .one()
        )
    except NoResultFound:
        return None


def create_imap_message(db_session, account, folder, msg):
    """
    IMAP-specific message creation logic.

    Returns
    -------
    imapuid : inbox.models.backends.imap.ImapUid
        New db object, which links to new Message and Block objects through
        relationships. All new objects are uncommitted.

    """
    log.debug(
        "creating message", account_id=account.id, folder_name=folder.name, mid=msg.uid
    )
    new_message = Message.create_from_synced(
        account=account,
        mid=msg.uid,
        folder_name=folder.name,
        received_date=msg.internaldate,
        body_string=msg.body,
    )

    # Check to see if this is a copy of a message that was first created
    # by the Nylas API. If so, don't create a new object; just use the old one.
    existing_copy = reconcile_message(new_message, db_session)
    if existing_copy is not None:
        new_message = existing_copy

    imapuid = ImapUid(
        account=account, folder=folder, msg_uid=msg.uid, message=new_message
    )
    imapuid.update_flags(msg.flags)
    if msg.g_labels is not None:
        imapuid.update_labels(msg.g_labels)

    # Update the message's metadata
    with db_session.no_autoflush:
        is_draft = imapuid.is_draft and (
            folder.canonical_name == "drafts" or folder.canonical_name == "all"
        )
        update_message_metadata(db_session, account, new_message, is_draft)

    update_contacts_from_message(db_session, new_message, account.namespace.id)

    return imapuid


def _select_category(categories):
    # TODO[k]: Implement proper ranking function
    return list(categories)[0]


def _update_categories(db_session, message, synced_categories):
    now = datetime.utcnow()

    # We make the simplifying assumption that only the latest syncback action
    # matters, since it reflects the current local state.
    actionlog_id = (
        db_session.query(func.max(ActionLog.id))
        .filter(
            ActionLog.namespace_id == message.namespace_id,
            ActionLog.table_name == "message",
            ActionLog.record_id == message.id,
            ActionLog.action.in_(["change_labels", "move"]),
        )
        .scalar()
    )
    if actionlog_id is not None:
        actionlog = db_session.query(ActionLog).get(actionlog_id)
        # Do /not/ overwrite message.categories in case of a recent local
        # change - namely, a still 'pending' action or one that completed
        # recently.
        if (
            actionlog.status == "pending"
            or (now - actionlog.updated_at).total_seconds() <= 90
        ):
            return

    # We completed the syncback action /long enough ago/ (on average and
    # with an error margin) that:
    # - if it completed successfully, sync has picked it up; so, safe to
    # overwrite message.categories
    # - if syncback failed, the local changes made can be overwritten
    # without confusing the API user.
    # TODO[k]/(emfree): Implement proper rollback of local state in this case.
    # This is needed in order to pick up future changes to the message,
    # the local_changes counter is reset as well.
    message.categories = synced_categories
    message.categories_changes = False
