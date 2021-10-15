#!/usr/bin/env python
import click

from inbox.error_handling import maybe_enable_rollbar
from inbox.models.account import Account
from inbox.models.session import global_session_scope, session_scope


@click.command()
@click.option("--dry-run", is_flag=True, default=False)
@click.option("--number", type=int, help="how many accounts to unschedule")
@click.argument("hostname")
@click.argument("process", required=False, default=None)
def main(dry_run, number, hostname, process):
    """
    Unschedule all accounts assigned to a given sync host.
    Intended primarily for use when decomissioning sync instances or for
    manually unloading an overloaded sync instance.

    """
    maybe_enable_rollbar()

    if not number:
        message = (
            "You have not provided a --number option. This will "
            "unschedule ALL syncs on the host. Proceed? [Y/n] "
        )
        if raw_input(message).strip().lower() == "n":
            print "Will not proceed"
            return

    if not dry_run:
        message = (
            "It is unsafe to unassign hosts while mailsync processes are running. "
            "Have you shut down the appropriate mailsync processes on {}? [Y/n]".format(
                hostname
            )
        )
        if raw_input(message).strip().lower() == "n":
            print "Bailing out"
            return

    with global_session_scope() as db_session:
        if process is not None:
            hostname = ":".join([hostname, process])
        to_unschedule = db_session.query(Account.id).filter(
            Account.sync_host.like("{}%".format(hostname))
        )
        if number:
            to_unschedule = to_unschedule.limit(number)
        to_unschedule = [id_ for id_, in to_unschedule.all()]
        if number:
            to_unschedule = to_unschedule[:number]

    for account_id in to_unschedule:
        with session_scope(account_id) as db_session:
            if dry_run:
                print "Would unassign", account_id
            else:
                account = db_session.query(Account).get(account_id)
                print "Unassigning", account.id
                account.desired_sync_host = None
                account.sync_host = None
                db_session.commit()


if __name__ == "__main__":
    main()
