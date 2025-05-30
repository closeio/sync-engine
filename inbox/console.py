import sys  # noqa: EXE002

import IPython

from inbox.crispin import writable_connection_pool
from inbox.mailsync.backends.imap.generic import uidvalidity_cb
from inbox.models import Account
from inbox.models.session import global_session_scope


def user_console(user_email_address) -> None:  # type: ignore[no-untyped-def]
    with global_session_scope() as db_session:
        result = (
            db_session.query(Account)
            .filter_by(email_address=user_email_address)
            .all()
        )

        account = None

        if len(result) == 1:
            account = result[0]
        elif len(result) > 1:
            print(  # noqa: T201
                f"\n{len(result)} accounts found for that email.\n"
            )
            for idx, acc in enumerate(result):
                print(  # noqa: T201
                    f"[{idx}] - {acc.provider} {acc.namespace.email_address} {acc.namespace.public_id}"
                )
            choice = int(input("\nWhich # do you want to select? "))
            account = result[choice]

        if account is None:
            print(  # noqa: T201
                f"No account found with email '{user_email_address}'"
            )
            return

        if account.provider == "eas":
            banner = """
        You can access the account instance with the 'account' variable.
        """
            IPython.embed(banner1=banner)
        else:
            with writable_connection_pool(
                account.id, pool_size=1
            ).get() as crispin_client:
                if (
                    account.provider == "gmail"
                    and "all" in crispin_client.folder_names()
                ):
                    crispin_client.select_folder(
                        crispin_client.folder_names()["all"][0], uidvalidity_cb
                    )

                banner = """
        You can access the crispin instance with the 'crispin_client' variable,
        and the account instance with the 'account' variable.

        IMAPClient docs are at:

            http://imapclient.readthedocs.org/en/latest/#imapclient-class-reference
        """

                IPython.embed(banner1=banner)


def start_console(  # type: ignore[no-untyped-def]
    user_email_address=None,
) -> None:
    # You can also do this with
    # $ python -m imapclient.interact -H <host> -u <user> ...
    # but we want to use our session and crispin so we're not.
    if user_email_address:
        user_console(user_email_address)
    else:
        IPython.embed()


def start_client_console(  # type: ignore[no-untyped-def]
    user_email_address=None,
) -> None:
    try:
        from tests.system.client import (  # type: ignore[import-untyped]
            NylasTestClient,
        )
    except ImportError:
        sys.exit(
            "You need to have the Nylas Python SDK installed to use this option."
        )
    client = NylasTestClient(  # noqa: F841
        user_email_address
    )  # noqa: F841, RUF100
    IPython.embed(
        banner1=(
            "You can access a Nylas API client using the 'client' variable."
        )
    )
