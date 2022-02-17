#!/usr/bin/env python
"""
Deletes an account's data permanently.

Includes:
* All data in the database.
* Account liveness/status data (in Redis).

USE WITH CAUTION.

If you need to delete the data for an account, it MUST be marked as deleted.
You can do this manually like this:

./bin/inbox-console -e foo@bar.com

    account.disable_sync("account deleted")
    db_session.commit()

"""


import time

import click

from inbox.error_handling import maybe_enable_rollbar
from inbox.heartbeat.status import clear_heartbeat_status
from inbox.models import Account
from inbox.models.session import session_scope
from inbox.models.util import delete_namespace


@click.command()
@click.argument("account_id", type=int)
@click.option("--dry-run", is_flag=True)
@click.option("--yes", is_flag=True)
@click.option("--throttle", is_flag=True)
def delete_account_data(account_id, dry_run, yes, throttle):
    maybe_enable_rollbar()

    with session_scope(account_id) as db_session:
        account = db_session.query(Account).get(account_id)

        if not account:
            print("Account with id {} does NOT exist.".format(account_id))
            return

        email_address = account.email_address
        namespace_id = account.namespace.id

        if account.sync_should_run or not account.is_marked_for_deletion:
            print(
                "Account with id {} NOT marked for deletion.\n"
                "Will NOT delete, goodbye.".format(account_id)
            )
            return -1

    if not yes:
        question = (
            "Are you sure you want to delete all data for account with "
            "id: {}, email_address: {} and namespace_id: {}? [yes / no]".format(
                account_id, email_address, namespace_id
            )
        )

        answer = raw_input(question).strip().lower()

        if answer != "yes":
            print("Will NOT delete, goodbye.")
            return 0

    print("Deleting account with id: {}...".format(account_id))
    start = time.time()

    # Delete data in database
    try:
        print("Deleting database data")
        delete_namespace(namespace_id, dry_run=dry_run, throttle=throttle)
    except Exception as e:
        print("Database data deletion failed! Error: {}".format(str(e)))
        return -1

    database_end = time.time()
    print("Database data deleted. Time taken: {}".format(database_end - start))

    # Delete liveness data
    print("Deleting liveness data")
    clear_heartbeat_status(account_id)

    end = time.time()
    print("All data deleted successfully! TOTAL time taken: {}".format(end - start))
    return 0


if __name__ == "__main__":
    delete_account_data()
