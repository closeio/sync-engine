#!/usr/bin/env python

import logging
from threading import BoundedSemaphore

import click

from inbox.logging import configure_logging
from inbox.mailsync.service import get_monitor_classes
from inbox.models.account import Account
from inbox.models.session import global_session_scope


@click.command()
@click.option("--account-id", required=True, type=int, help="Account ID to sync.")
@click.option(
    "--folder-name",
    default=None,
    help="Folder name to sync. If not provided, sync all folders.",
)
def main(account_id: int, folder_name: str) -> None:
    """
    Sync a single account.

    This script is intended as a debugging tool and can be used to sync
    a single account and/or folder. It is useful to sync a single account
    to surface issues like memory leaks, high CPU usage, problematic IMAP behavior
    or sync halting issues. When syncing multiple accounts in a single process
    it might be challenging to attribute these issues to a specific account.
    """
    configure_logging(logging.DEBUG)

    with global_session_scope() as db_session:
        account = db_session.query(Account).get(account_id)
        monitor_class = get_monitor_classes()[account.provider]

        if folder_name:
            folder_sync_engine = monitor_class.sync_engine_class(
                account_id,
                account.namespace.id,
                folder_name,
                account.email_address,
                account.verbose_provider,
                BoundedSemaphore(1),
            )
            run = folder_sync_engine.run
        else:
            monitor = monitor_class(account)
            run = monitor.run

    run()


if __name__ == "__main__":
    main()
