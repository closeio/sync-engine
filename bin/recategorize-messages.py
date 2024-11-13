#!/usr/bin/env python

import datetime

import click
from sqlalchemy.orm import Query

from inbox.mailsync.backends.imap.common import update_message_metadata
from inbox.models.backends.imap import ImapUid
from inbox.models.folder import Folder
from inbox.models.message import Message
from inbox.models.namespace import Namespace
from inbox.models.session import global_session_scope, session_scope


def fetch_message_ids(
    *,
    account_id: int | None,
    date_start: datetime.date | None,
    date_end: datetime.date | None,
    only_inbox: bool,
) -> list[int]:
    query = Query([Message.id])
    if account_id:
        query = query.filter(Message.namespace.has(Namespace.account_id == account_id))
    if only_inbox:
        inbox_folder = ImapUid.folder.has(Folder._canonical_name == "INBOX")
        query = query.filter(Message.imapuids.any(inbox_folder))
    if date_start:
        query = query.filter(Message.created_at >= date_start)
    if date_end:
        query = query.filter(Message.created_at < date_end)

    with global_session_scope() as session:
        message_ids = [message_id for message_id, in query.with_session(session)]

    return message_ids


@click.command()
@click.option("--date-start", type=click.DateTime(formats=["%Y-%m-%d"]), default=None)
@click.option("--date-end", type=click.DateTime(formats=["%Y-%m-%d"]), default=None)
@click.option("--account-id", type=int, default=None)
@click.option("--only-inbox", is_flag=True, default=False)
def main(
    account_id: int | None,
    only_inbox: bool,
    date_start: datetime.date | None,
    date_end: datetime.date | None,
) -> None:
    message_ids = fetch_message_ids(
        account_id=account_id,
        date_start=date_start,
        date_end=date_end,
        only_inbox=only_inbox,
    )

    print(f"Found {len(message_ids)}")

    for message_id in message_ids:
        with session_scope(None) as session:
            message = session.query(Message).get(message_id)
            old_categories = set(
                category.display_name for category in message.categories
            )
            update_message_metadata(session, message.account, message, message.is_draft)
            new_categories = set(
                category.display_name for category in message.categories
            )
            if old_categories != new_categories:
                print(
                    f"Message {message_id} categories changed from {old_categories} to {new_categories}"
                )


if __name__ == "__main__":
    main()
