#!/usr/bin/env python  # noqa: EXE001, N999


import click

from inbox.error_handling import maybe_enable_rollbar
from inbox.models.account import Account
from inbox.models.session import global_session_scope


@click.command()
@click.argument("hostname")
def main(hostname) -> None:
    maybe_enable_rollbar()

    with global_session_scope() as db_session:
        account_ids = db_session.query(Account.id).filter(
            Account.sync_host == hostname
        )

        print(f"Accounts being synced by {hostname}:")
        for account_id in account_ids:
            print(account_id[0])
        db_session.commit()


if __name__ == "__main__":
    main()
