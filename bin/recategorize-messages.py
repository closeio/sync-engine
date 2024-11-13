#!/usr/bin/env python

import datetime
from collections.abc import Iterable

import click
from sqlalchemy.orm import Query

from inbox.mailsync.backends.imap.common import update_message_metadata
from inbox.models.backends.imap import ImapUid
from inbox.models.folder import Folder
from inbox.models.message import Message
from inbox.models.namespace import Namespace
from inbox.models.session import global_session_scope, session_scope


def yield_account_id_and_message_ids(
    *,
    only_account_id: int | None,
    date_start: datetime.date | None,
    date_end: datetime.date | None,
    only_inbox: bool,
) -> Iterable[int, list[int]]:
    namespace_query = Query([Namespace.account_id, Namespace.id])
    if only_account_id:
        namespace_query = namespace_query.filter(
            Namespace.account_id == only_account_id
        )

    with global_session_scope() as session:
        account_id_to_namespace_id = {
            account_id: namespace_id
            for account_id, namespace_id in namespace_query.with_session(session)
        }

    for account_id, namespace_id in account_id_to_namespace_id.items():
        query = Query([Message.id]).filter(Message.namespace_id == namespace_id)

        if only_inbox:
            inbox_folder = ImapUid.folder.has(Folder._canonical_name == "INBOX")
            query = query.filter(Message.imapuids.any(inbox_folder))
        if date_start:
            query = query.filter(Message.created_at >= date_start)
        if date_end:
            query = query.filter(Message.created_at < date_end)

        with global_session_scope() as session:
            message_ids = [message_id for message_id, in query.with_session(session)]

        yield account_id, message_ids


@click.command()
@click.option("--date-start", type=click.DateTime(formats=["%Y-%m-%d"]), default=None)
@click.option("--date-end", type=click.DateTime(formats=["%Y-%m-%d"]), default=None)
@click.option("--only-account-id", type=int, default=None)
@click.option("--only-inbox", is_flag=True, default=False)
@click.option("--dry-run/--no-dry-run", default=True)
def main(
    only_account_id: int | None,
    only_inbox: bool,
    date_start: datetime.date | None,
    date_end: datetime.date | None,
    dry_run: bool,
) -> None:
    print(
        f"Settings: {only_account_id=}, {only_inbox=}, {date_start=}, {date_end=}, {dry_run=}\n"
    )

    def session_factory():
        return global_session_scope() if dry_run else session_scope(None)

    for account_id, message_ids in yield_account_id_and_message_ids(
        only_account_id=only_account_id,
        date_start=date_start,
        date_end=date_end,
        only_inbox=only_inbox,
    ):
        print(f"{account_id=}, {len(message_ids)=}")

        changed_counter = 0
        for message_id in message_ids:
            with session_factory() as session:
                message = session.query(Message).get(message_id)
                old_categories = set(
                    category.display_name for category in message.categories
                )
                update_message_metadata(
                    session, message.account, message, message.is_draft
                )
                new_categories = set(
                    category.display_name for category in message.categories
                )
                if old_categories != new_categories:
                    changed_counter += 1
                    print(f"\t{message_id=}, {old_categories=} to {new_categories}=")

        print(f"{account_id=}, {changed_counter=}\n")


if __name__ == "__main__":
    main()
