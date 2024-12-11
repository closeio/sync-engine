import datetime
import json

import pytest

from inbox.crispin import Flags, GmailFlags
from inbox.mailsync.backends.imap.common import (
    update_message_metadata,
    update_metadata,
)
from inbox.models.backends.imap import ImapUid
from inbox.models.folder import Folder

from tests.util.base import (
    add_fake_folder,
    add_fake_imapuid,
    add_fake_message,
    add_fake_thread,
    delete_imapuids,
    delete_messages,
    delete_threads,
)


def test_gmail_label_sync(
    db, default_account, message, folder, imapuid, default_namespace
):
    # Note that IMAPClient parses numeric labels into integer types. We have to
    # correctly handle those too.
    new_flags = {
        imapuid.msg_uid: GmailFlags(
            (), ("\\Important", "\\Starred", "foo", 42), None
        )
    }
    update_metadata(
        default_namespace.account.id,
        folder.id,
        folder.canonical_name,
        new_flags,
        db.session,
    )
    category_canonical_names = {c.name for c in message.categories}
    category_display_names = {c.display_name for c in message.categories}
    assert "important" in category_canonical_names
    assert {"foo", "42"}.issubset(category_display_names)


def test_gmail_drafts_flag_constrained_by_folder(
    db, default_account, message, imapuid, folder
):
    new_flags = {imapuid.msg_uid: GmailFlags((), ("\\Draft",), None)}
    update_metadata(
        default_account.id, folder.id, "all", new_flags, db.session
    )
    assert message.is_draft
    update_metadata(
        default_account.id, folder.id, "trash", new_flags, db.session
    )
    assert not message.is_draft


@pytest.mark.parametrize("folder_role", ["drafts", "trash", "archive"])
def test_generic_drafts_flag_constrained_by_folder(
    db, generic_account, folder_role
):
    msg_uid = 22
    thread = add_fake_thread(db.session, generic_account.namespace.id)
    message = add_fake_message(
        db.session, generic_account.namespace.id, thread
    )
    folder = add_fake_folder(db.session, generic_account)
    add_fake_imapuid(db.session, generic_account.id, message, folder, msg_uid)

    new_flags = {msg_uid: Flags((b"\\Draft",), None)}
    update_metadata(
        generic_account.id, folder.id, folder_role, new_flags, db.session
    )
    assert message.is_draft == (folder_role == "drafts")


def test_update_categories_when_actionlog_entry_missing(
    db, default_account, message, imapuid
):
    message.categories_changes = True
    db.session.commit()
    update_message_metadata(db.session, imapuid.account, message, False)
    assert message.categories == {imapuid.folder.category}


@pytest.mark.parametrize(
    "folder_roles,categories",
    [
        ([], set()),
        (["inbox"], {"inbox"}),
        (["inbox", "archive"], {"archive"}),
        (["inbox", "trash"], {"trash"}),
        (["inbox", "archive", "trash"], {"trash"}),
    ],
)
def test_categories_from_multiple_imap_folders(
    db, generic_account, folder_roles, categories
):
    """
    This tests that if we somehow think that a message is inside
    many folders simultanously, we should categorize it with the one
    it was added to last.

    This should not happen in practice as with generic IMAP a message will always be
    in a single folder but it seems that for some on-prem servers we are not
    able to reliably detect when a message is moved between folders and we end
    up with many folders in our MySQL. Such message used to undeterministically
    appear in one of those folders depending on the order they were returned
    from the database. This makes it deterministic and more-correct because a message
    is likely in a folder it was added to last.
    """
    thread = add_fake_thread(db.session, generic_account.namespace.id)
    message = add_fake_message(
        db.session, generic_account.namespace.id, thread
    )
    for delay, folder_role in enumerate(folder_roles):
        folder = Folder.find_or_create(
            db.session, generic_account, folder_role, folder_role
        )
        imapuid = add_fake_imapuid(
            db.session, generic_account.id, message, folder, 2222
        )
        # Simulate that time passed since those timestamps have second resolution
        # and this executes fast enough that all of them would be the same otherwise
        imapuid.updated_at = imapuid.updated_at + datetime.timedelta(
            seconds=delay
        )
        db.session.commit()

    update_message_metadata(db.session, generic_account, message, False)
    assert {category.name for category in message.categories} == categories

    delete_imapuids(db.session)
    delete_messages(db.session)
    delete_threads(db.session)


def test_truncate_imapuid_extra_flags(db, default_account, message, folder):
    imapuid = ImapUid(
        message=message,
        account_id=default_account.id,
        msg_uid=2222,
        folder=folder,
    )
    imapuid.update_flags(
        [
            b"We",
            b"the",
            b"People",
            b"of",
            b"the",
            b"United",
            b"States",
            b"in",
            b"Order",
            b"to",
            b"form",
            b"a",
            b"more",
            b"perfect",
            b"Union",
            b"establish",
            b"Justice",
            b"insure",
            b"domestic",
            b"Tranquility",
            b"provide",
            b"for",
            b"the",
            b"common",
            b"defence",
            b"promote",
            b"the",
            b"general",
            b"Welfare",
            b"and",
            b"secure",
            b"the",
            b"Blessings",
            b"of",
            b"Liberty",
            b"to",
            b"ourselves",
            b"and",
            b"our",
            b"Posterity",
            b"do",
            b"ordain",
            b"and",
            b"establish",
            b"this",
            b"Constitution",
            b"for",
            b"the",
            b"United",
            b"States",
            b"of",
            b"America",
        ]
    )

    assert len(json.dumps(imapuid.extra_flags)) < 255
