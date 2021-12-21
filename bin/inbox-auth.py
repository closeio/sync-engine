#!/usr/bin/env python
from __future__ import print_function

from gevent import monkey

monkey.patch_all()

import sys

import click
from setproctitle import setproctitle

setproctitle("inbox-auth")

from inbox.auth.base import handler_from_provider
from inbox.basicauth import NotSupportedError
from inbox.config import config
from inbox.error_handling import maybe_enable_rollbar
from inbox.logging import configure_logging
from inbox.models import Account
from inbox.models.session import session_scope
from inbox.util.startup import preflight
from inbox.util.url import provider_from_address

configure_logging(config.get("LOGLEVEL"))


@click.command()
@click.argument("email_address")
@click.option(
    "--reauth",
    is_flag=True,
    help="Re-authenticate an account even if it already exists",
)
@click.option(
    "--target", type=int, default=0, help="Database shard id to target for the account"
)
@click.option(
    "--provider",
    is_flag=False,
    help="Manually specify the provider instead of trying to detect it",
)
def main(email_address, reauth, target, provider):
    """ Auth an email account. """
    preflight()

    maybe_enable_rollbar()

    shard_id = target << 48

    with session_scope(shard_id) as db_session:
        account = (
            db_session.query(Account).filter_by(email_address=email_address).first()
        )
        if account is not None and not reauth:
            sys.exit("Already have this account!")

        if not provider:
            provider = provider_from_address(email_address)

            # Resolve unknown providers into either custom IMAP or EAS.
            if provider == "unknown":
                is_imap = raw_input("IMAP account? [Y/n] ").strip().lower() != "n"
                provider = "custom" if is_imap else "eas"

        auth_handler = handler_from_provider(provider)
        account_data = auth_handler.interactive_auth(email_address)

        if reauth:
            account = auth_handler.update_account(account, account_data)
        else:
            account = auth_handler.create_account(account_data)

        try:
            if auth_handler.verify_account(account):
                db_session.add(account)
                db_session.commit()
        except NotSupportedError as e:
            sys.exit(str(e))

    print("OK. Authenticated account for {}".format(email_address))


if __name__ == "__main__":
    main()
