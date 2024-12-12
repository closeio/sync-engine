#!/usr/bin/env python

import time

from inbox.error_handling import maybe_enable_rollbar
from inbox.ignition import engine_manager
from inbox.logging import configure_logging, get_logger
from inbox.mailsync.service import shared_sync_event_queue_for_zone
from inbox.models.account import Account
from inbox.models.session import global_session_scope
from inbox.util.concurrency import retry_with_logging

configure_logging()
log = get_logger()

accounts_without_sync_host = set()


def check_accounts():
    maybe_enable_rollbar()

    global accounts_without_sync_host
    poll_interval = 30

    with global_session_scope() as db_session:
        not_syncing_accounts = set(
            db_session.query(Account.id).filter(
                Account.sync_should_run, Account.sync_host.is_(None)
            )
        )
        still_not_syncing_accounts = (
            accounts_without_sync_host & not_syncing_accounts
        )

        for account_id in still_not_syncing_accounts:
            account = (
                db_session.query(Account).with_for_update().get(account_id)
            )

            # The Account got claimed while we were checking.
            if account.sync_host is not None:
                not_syncing_accounts.remove(account.id)
                db_session.commit()
                continue

            # Notify the shared sync queue if desired_sync_host was already None.
            # We have to do this because the post commit callback won't fire if
            # the object isn't dirty. By clearing the desired_sync_host we allow
            # any worker to claim the account.
            if account.desired_sync_host is None:
                queue = shared_sync_event_queue_for_zone(
                    engine_manager.zone_for_id(account.id)
                )
                queue.send_event({"event": "migrate", "id": account.id})
            else:
                account.desired_sync_host = None

            log.warning(
                "Account appears to be unclaimed, "
                "clearing desired_sync_host, "
                "notifying shared sync queue",
                account_id=account.id,
            )
            db_session.commit()

        accounts_without_sync_host = not_syncing_accounts

    time.sleep(poll_interval)


def main():
    while True:
        retry_with_logging(check_accounts, log)


if __name__ == "__main__":
    main()
